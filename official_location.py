"""Official website employment-location enrichment.

YC's company profile location is retained as a separate source because it can
represent a primary/profile location rather than every current operating office.
This module looks for current office evidence on the company's own website or
career/contact pages and never uses founder location as proof of an office.
"""

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin, urlparse

from location_utils import normalize_location, normalize_locations


class _WebsiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.script_depth = 0
        self.style_depth = 0
        self.in_jsonld = False
        self.current_script: List[str] = []
        self.jsonld_blocks: List[str] = []
        self.anchor_stack: List[Tuple[str, str]] = []
        self.links: List[Tuple[str, str]] = []
        self.text_chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        attr = dict(attrs)
        tag = tag.lower()
        if tag == "script":
            self.script_depth += 1
            if attr.get("type", "").lower() == "application/ld+json":
                self.in_jsonld = True
                self.current_script = []
        elif tag == "style":
            self.style_depth += 1
        elif tag == "a":
            href = attr.get("href")
            if href:
                self.anchor_stack.append((href, ""))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self.script_depth:
            if self.in_jsonld:
                self.jsonld_blocks.append("".join(self.current_script))
                self.in_jsonld = False
                self.current_script = []
            self.script_depth -= 1
        elif tag == "style" and self.style_depth:
            self.style_depth -= 1
        elif tag == "a" and self.anchor_stack:
            href, text = self.anchor_stack.pop()
            self.links.append((href, " ".join(text.split())))

    def handle_data(self, data: str) -> None:
        if self.in_jsonld:
            self.current_script.append(data)
        if self.script_depth or self.style_depth:
            return

        clean = " ".join(data.split())
        if not clean:
            return

        self.text_chunks.append(clean)
        if self.anchor_stack:
            href, text = self.anchor_stack[-1]
            self.anchor_stack[-1] = (href, f"{text} {clean}")


def _walk_json(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_json(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json(item)


def _jsonld_locations(block: str) -> List[Dict[str, Any]]:
    locations: List[Dict[str, Any]] = []
    try:
        data = json.loads(block)
    except Exception:
        return locations

    for obj in _walk_json(data):
        for key in ("address",):
            address = obj.get(key)
            if isinstance(address, (dict, list, str)):
                locs = normalize_locations([address])
                for loc in locs:
                    if loc and (loc.get("city") or loc.get("country") or loc.get("state")):
                        loc["evidence_type"] = "official_structured_address"
                        locations.append(loc)

        for key in ("location", "locations"):
            nested = obj.get(key)
            if isinstance(nested, (dict, list, str)):
                for loc in normalize_locations([nested]):
                    if loc and (loc.get("city") or loc.get("country") or loc.get("state")):
                        loc["evidence_type"] = "official_structured_location"
                        locations.append(loc)

    return locations


def _textual_locations(text: str) -> List[Dict[str, Any]]:
    """Find city/country mentions close to office/location wording."""
    lower = text.lower()
    keywords = (
        "office", "offices", "location", "locations", "headquarters", "hq",
        "our team", "based in", "based at", "contact us", "where we work",
    )
    found: List[Dict[str, Any]] = []

    for keyword in keywords:
        start = 0
        while True:
            idx = lower.find(keyword, start)
            if idx < 0:
                break
            snippet = text[max(0, idx - 80): idx + 180]
            # Reuse the canonical location vocabulary from location_utils.
            for candidate in normalize_locations([snippet]):
                if candidate.get("city") or candidate.get("country") or candidate.get("state"):
                    candidate["evidence_type"] = "official_context"
                    candidate["snippet"] = snippet
                    found.append(candidate)
            start = idx + len(keyword)

    return found


def parse_official_site_locations(html: str, source_url: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Tuple[str, str]]]:
    parser = _WebsiteParser()
    try:
        parser.feed(html or "")
    except Exception:
        pass

    locations: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []

    for block in parser.jsonld_blocks:
        for loc in _jsonld_locations(block):
            loc["source"] = "official_company_website"
            loc["source_url"] = source_url
            locations.append(loc)
            evidence.append({
                "source": "official_company_website",
                "url": source_url,
                "kind": loc.get("evidence_type"),
                "location": {
                    "city": loc.get("city"),
                    "state": loc.get("state"),
                    "country": loc.get("country"),
                },
            })

    visible_text = " ".join(parser.text_chunks)
    for loc in _textual_locations(visible_text):
        loc["source"] = "official_company_website"
        loc["source_url"] = source_url
        locations.append(loc)
        evidence.append({
            "source": "official_company_website",
            "url": source_url,
            "kind": loc.get("evidence_type"),
            "snippet": loc.get("snippet"),
            "location": {
                "city": loc.get("city"),
                "state": loc.get("state"),
                "country": loc.get("country"),
            },
        })

    # Keep only links likely to lead to current employment/office information.
    relevant: List[Tuple[str, str]] = []
    keywords = ("career", "careers", "jobs", "contact", "about", "office", "location", "team")
    base_host = urlparse(source_url).netloc.lower()

    for href, text in parser.links:
        absolute = urljoin(source_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        label = f"{text} {parsed.path}".lower()
        if not any(k in label for k in keywords):
            continue
        # Prefer official-domain links; allow known ATS/careers links as a
        # secondary source because they can contain location-specific jobs.
        if parsed.netloc.lower() != base_host and not any(k in label for k in ("career", "careers", "jobs")):
            continue
        relevant.append((absolute, text))

    unique = []
    seen = set()
    for item in relevant:
        if item[0] in seen:
            continue
        seen.add(item[0])
        unique.append(item)
    return locations, evidence, unique[:4]

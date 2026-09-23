"""Location normalization and target-geography matching for startup/job discovery.

The pipeline uses two independent signals:
1. current company/office locations from the YC company profile;
2. active job locations from the jobs attached to that profile.

This avoids treating a startup's original/incorporation/HQ location as the
only employment location signal.
"""

import re
from typing import Any, Dict, Iterable, List, Optional


COUNTRY_ALIASES = {
    "india": "India",
    "in": "India",
    "ind": "India",
    "united states": "United States",
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "canada": "Canada",
    "singapore": "Singapore",
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "australia": "Australia",
    "germany": "Germany",
}


CITY_ALIASES = {
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "bengalore": "Bengaluru",
    "hyderabad": "Hyderabad",
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "pune": "Pune",
    "chennai": "Chennai",
    "madras": "Chennai",
    "delhi": "Delhi NCR",
    "new delhi": "Delhi NCR",
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "noida": "Noida",
    "greater noida": "Noida",
    "san francisco": "San Francisco",
    "new york": "New York",
    "london": "London",
}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_country(value: Any) -> Optional[str]:
    raw = _text(value).lower()
    if not raw:
        return None
    if raw in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[raw]
    for alias, canonical in COUNTRY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            return canonical
    return None


def _normalize_city(value: Any) -> Optional[str]:
    raw = _text(value).lower()
    if not raw:
        return None
    for alias, canonical in CITY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            return canonical
    return None


def normalize_location(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize flexible YC/company/job location structures into a common shape."""
    if raw is None:
        return None

    if isinstance(raw, dict):
        # YC / job payloads may use several equivalent keys.
        nested = raw.get("location") or raw.get("office") or raw.get("address")
        city = _normalize_city(
            raw.get("city") or raw.get("town") or raw.get("municipality") or raw.get("locality")
        )
        country = _normalize_country(
            raw.get("country") or raw.get("country_name") or raw.get("countryCode") or raw.get("country_code")
        )

        text_candidates = [
            raw.get("name"),
            raw.get("label"),
            raw.get("display_name"),
            raw.get("location_name"),
            nested,
        ]
        blob = " ".join(_text(v) for v in text_candidates if v)
        city = city or _normalize_city(blob)
        country = country or _normalize_country(blob)

        # Some payloads provide a simple "location": string plus state/region.
        state = _text(raw.get("state") or raw.get("region") or raw.get("province")) or None
        remote = bool(raw.get("remote") or raw.get("is_remote"))
        if not city and not country and not state and not remote:
            return normalize_location(nested) if nested else None

        return {
            "city": city,
            "state": state,
            "country": country,
            "remote": remote,
            "raw": raw,
        }

    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            normalized = normalize_location(item)
            if normalized:
                return normalized
        return None

    text = _text(raw)
    if not text:
        return None

    city = _normalize_city(text)
    country = _normalize_country(text)
    remote = "remote" in text.lower()

    # Try comma-separated parts for state/country text.
    parts = [p.strip() for p in text.split(",") if p.strip()]
    state = parts[1] if len(parts) >= 3 else None

    if not city and not country and not remote:
        return {"city": None, "state": None, "country": None, "remote": False, "raw": text}

    return {
        "city": city,
        "state": state,
        "country": country,
        "remote": remote,
        "raw": text,
    }


def normalize_locations(raw: Any) -> List[Dict[str, Any]]:
    """Normalize one or many locations and drop obvious duplicates."""
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    output: List[Dict[str, Any]] = []
    seen = set()

    for value in values:
        loc = normalize_location(value)
        if not loc:
            continue
        key = (
            loc.get("city"),
            loc.get("state"),
            loc.get("country"),
            bool(loc.get("remote")),
            _text(loc.get("raw")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(loc)

    return output


def extract_company_locations(company_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract likely current office/HQ locations from a YC company payload."""
    candidates: List[Any] = []
    for key in (
        "locations",
        "office_locations",
        "offices",
        "location",
        "hq_location",
        "headquarters",
        "address",
    ):
        if company_info.get(key):
            candidates.append(company_info.get(key))

    # Some structured payloads expose city/state/country separately.
    if any(company_info.get(k) for k in ("city", "state", "country", "country_name")):
        candidates.append(
            {
                "city": company_info.get("city"),
                "state": company_info.get("state"),
                "country": company_info.get("country") or company_info.get("country_name"),
            }
        )

    return normalize_locations(candidates)


def extract_job_locations(jobs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract location signals from active job records."""
    output: List[Dict[str, Any]] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        candidates = []
        for key in ("location", "locations", "office_location", "workplace", "city"):
            if job.get(key):
                candidates.append(job.get(key))
        if job.get("remote") or job.get("is_remote"):
            candidates.append({"remote": True, "location": job.get("location")})
        output.extend(normalize_locations(candidates))

    # Deduplicate again across job records.
    return normalize_locations(output)


def locations_match(
    office_locations: List[Dict[str, Any]],
    job_locations: List[Dict[str, Any]],
    target_country: Optional[str] = "India",
    target_city: Optional[str] = "",
) -> str:
    """Return MATCH, MISMATCH, or UNKNOWN for the requested geography."""
    target_country = _normalize_country(target_country) if target_country else None
    target_city = _normalize_city(target_city) if target_city else None

    all_locations = [*office_locations, *job_locations]
    if not all_locations:
        return "UNKNOWN"

    for loc in all_locations:
        country_ok = not target_country or loc.get("country") == target_country
        city_ok = not target_city or loc.get("city") == target_city
        if country_ok and city_ok:
            return "MATCH"

    return "MISMATCH"


def primary_location(locations: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """Return a stable primary location representation."""
    if not locations:
        return {"country": None, "state": None, "city": None}

    loc = locations[0]
    return {
        "country": loc.get("country"),
        "state": loc.get("state"),
        "city": loc.get("city"),
    }

"""YC batch catalog helpers.

YC historically used Winter/Summer batches; the current 2026 catalog also
contains the Fall 2026 batch. The UI exposes the full selectable catalog even
when a batch has not yet been stored in this application's database.
"""

from typing import Dict, List

SEASON_ORDER = {"Fall": 0, "Summer": 1, "Winter": 2}


def yc_batch_catalog(start_year: int = 2005, end_year: int = 2026) -> List[Dict[str, str]]:
    batches: List[Dict[str, str]] = []
    for year in range(end_year, start_year - 1, -1):
        seasons = ["Summer", "Winter"]
        if year == end_year:
            seasons = ["Fall", "Summer", "Winter"]
        for season in seasons:
            batches.append({
                "batch": f"{season} {year}",
                "code": f"{season[0]}{str(year)[-2:]}",
            })
    return batches

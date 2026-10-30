"""Discover Titled Tuesday tournament slugs from players' tournament histories.

The Published Data API has no "list tournaments" endpoint, but every player's
/pub/player/{username}/tournaments lists the events they finished. Taking the
union over a handful of regular participants recovers the weekly series. The
result was pinned into config.TOURNAMENTS; this script documents how.

    python -m ttpredict.discover --year 2026 --months january february march
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime

from .api import get_json
from .config import API_BASE

REGULARS = ["hikaru", "lachesisq", "javokhir_sindarov05", "konavets", "jaisankars"]


def slug_date(slug: str) -> datetime:
    month, day, year = slug.split("-")[3:6]
    return datetime.strptime(f"{month}-{day}-{year}", "%B-%d-%Y")


def discover(year: int, months: list[str]) -> list[str]:
    pattern = re.compile(rf"titled-tuesday-blitz-({'|'.join(months)})-\d\d-{year}-\d+")
    slugs: set[str] = set()
    for user in REGULARS:
        data = get_json(f"{API_BASE}/player/{user}/tournaments", use_cache=False)
        for t in data.get("finished", []):
            slug = t["@id"].rsplit("/", 1)[1]
            if pattern.fullmatch(slug):
                slugs.add(slug)
    return sorted(slugs, key=slug_date)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", nargs="+", default=["january", "february", "march"])
    args = parser.parse_args()
    for slug in discover(args.year, [m.lower() for m in args.months]):
        print(slug)


if __name__ == "__main__":
    main()

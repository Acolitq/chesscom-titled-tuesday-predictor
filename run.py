"""Reproduce everything with one command.

    python run.py            # train + evaluate from the committed data/features.csv
    python run.py --refresh  # re-download from the Chess.com API first (~20-40 min)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ttpredict import archives, collect, features, model  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-collect data from the Chess.com API")
    args = parser.parse_args()
    if args.refresh or not features.FEATURES_CSV.exists():
        collect.main()
        archives.main()
        features.main()
    model.run()
    print(f"\nReports written to {model.REPORTS_DIR}")


if __name__ == "__main__":
    main()

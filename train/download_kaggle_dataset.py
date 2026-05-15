from __future__ import annotations

import argparse
import importlib
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a Kaggle dataset and optionally copy CSV files to data/raw"
    )
    parser.add_argument(
        "--dataset",
        default="vigneshvenkateswaran/bot-iot",
        help="Kaggle dataset handle in owner/dataset format",
    )
    parser.add_argument(
        "--copy-csv-to",
        default="data/raw",
        help="Target folder to copy discovered CSV files",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Optional cap for copied CSV file count, 0 means all",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    kagglehub = importlib.import_module("kagglehub")

    downloaded_path = Path(kagglehub.dataset_download(args.dataset))
    print(f"Downloaded to cache: {downloaded_path}")

    csv_files = sorted(downloaded_path.rglob("*.csv"))
    if not csv_files:
        print("No CSV files found in downloaded dataset.")
        return

    if args.max_files > 0:
        csv_files = csv_files[: args.max_files]

    target_dir = Path(args.copy_csv_to)
    target_dir.mkdir(parents=True, exist_ok=True)

    for src in csv_files:
        dst = target_dir / src.name
        if dst.exists():
            stem = src.stem
            suffix = src.suffix
            i = 1
            while True:
                candidate = target_dir / f"{stem}_{i}{suffix}"
                if not candidate.exists():
                    dst = candidate
                    break
                i += 1
        shutil.copy2(src, dst)

    print(f"Copied {len(csv_files)} CSV file(s) to: {target_dir}")


if __name__ == "__main__":
    main()

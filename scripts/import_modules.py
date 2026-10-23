#!/usr/bin/env python3
"""Re-derive data/curriculum.json from the school's modules.xlsx.

The shipped spreadsheet has no stable schema (four grade blocks laid out
as free-floating tables), so this script extracts the raw (course, minutes)
pairs per block and prints them for review; the curated, model-ready
curriculum lives in data/curriculum.json (see README for the modelling
decisions, e.g. combined module totals counting as one course).

Usage:  python scripts/import_modules.py path/to/modules.xlsx
"""

import sys

import pandas as pd


def main(path: str) -> None:
    raw = pd.read_excel(path, header=None)
    blocks, current = [], []
    for _, row in raw.iterrows():
        vals = [v for v in row.tolist() if pd.notna(v)]
        if len(vals) == 2 and str(vals[0]).strip() == "Course":
            if current:
                blocks.append(current)
            current = []
        elif len(vals) == 2 and isinstance(vals[1], (int, float)):
            current.append((str(vals[0]).strip(), int(vals[1])))
    if current:
        blocks.append(current)

    grade_blocks = [b for b in blocks
                    if any("Math" in name for name, _ in b)]
    for i, block in enumerate(grade_blocks, start=9):
        print(f"\n# Grade {i} programme (2-week minutes)")
        total = 0
        for name, minutes in block:
            if "Empty Time" in name:
                print(f"  {'Empty time':<45} {minutes}")
                continue
            total += minutes
            print(f"  {name:<45} {minutes:>5}")
        print(f"  {'TOTAL (excl. empty)':<45} {total:>5}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "modules.xlsx")

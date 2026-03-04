"""
Nikke Union Raid - teams.txt to teams.csv converter
=====================================================
Converts the guild's tab/space-separated teams export into the CSV format
expected by the optimizer and HTML report generator.

Input column layout (tab or 2+ spaces as separator):
  0  member
  1  rank         (ignored)
  2  element      (Iron / Water / Electric / Fire / Wind)
  3  run number   (ignored)
  4  damage
  5  unit1
  6  unit2
  7  unit3
  8  unit4
  9  unit5
  10 X-flag       (optional annotation, ignored)
  11 secondary    (ignored)

Usage:
  python tools/convert_teams.py
  python tools/convert_teams.py --txt data/teams.txt --bosses data/bosses_template.csv --out data/teams.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path


def load_element_map(bosses_path: str) -> dict:
    """Build {element_lowercase: boss_name} from the bosses CSV."""
    mapping = {}
    with open(bosses_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "element_weakness" not in reader.fieldnames:
            print(
                "[ERROR] bosses CSV has no 'element_weakness' column.",
                file=sys.stderr,
            )
            sys.exit(1)
        for row in reader:
            element = row["element_weakness"].strip().lower()
            boss = row["boss_name"].strip()
            mapping[element] = boss
    return mapping


def split_line(line: str) -> list:
    """Split a line on tabs, falling back to 2+ spaces."""
    if "\t" in line:
        return [p.strip() for p in line.split("\t")]
    return [p.strip() for p in re.split(r" {2,}", line)]


def convert(txt_path: str, bosses_path: str, out_path: str) -> None:
    element_map = load_element_map(bosses_path)

    rows = []
    warnings = []

    with open(txt_path, encoding="utf-8") as f:
        for line_num, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue

            parts = split_line(line)
            # Drop empty strings that result from consecutive separators
            parts = [p for p in parts if p != ""]

            if len(parts) < 10:
                warnings.append(
                    f"Line {line_num}: only {len(parts)} columns (need at least 10) — skipped"
                )
                continue

            member  = parts[0]
            element = parts[2].strip().lower()
            damage_raw = parts[4].replace(",", "")
            units   = parts[5:10]

            # Validate damage
            try:
                damage = int(damage_raw)
            except ValueError:
                warnings.append(
                    f"Line {line_num}: invalid damage value '{parts[4]}' — skipped"
                )
                continue

            # Resolve boss from element
            boss_name = element_map.get(element)
            if boss_name is None:
                warnings.append(
                    f"Line {line_num}: unknown element '{parts[2]}' — skipped"
                )
                continue

            if len(units) != 5:
                warnings.append(
                    f"Line {line_num}: expected 5 units, got {len(units)} — skipped"
                )
                continue

            rows.append([member, boss_name, damage] + units)

    # Write output CSV
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["member", "boss_name", "damage", "unit1", "unit2", "unit3", "unit4", "unit5"])
        writer.writerows(rows)

    # Summary
    print(f"Converted {len(rows)} rows -> {out.resolve()}")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  [!] {w}")


def parse_args():
    parser = argparse.ArgumentParser(description="Convert teams.txt to teams.csv")
    parser.add_argument("--txt",    default="data/teams.txt",             help="Input .txt file")
    parser.add_argument("--bosses", default="data/bosses_template.csv",   help="Bosses CSV (for element mapping)")
    parser.add_argument("--out",    default="data/teams.csv",             help="Output CSV file")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    convert(args.txt, args.bosses, args.out)

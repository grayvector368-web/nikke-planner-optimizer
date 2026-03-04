"""
Quick damage summary from teams.csv.
Usage:
  python tools/sum_damage.py
  python tools/sum_damage.py --teams data/teams.csv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def fmt_hp(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n:,}"


def main():
    parser = argparse.ArgumentParser(description="Sum damage from teams.csv")
    parser.add_argument("--teams", default="data/teams.csv", help="Path to teams CSV")
    args = parser.parse_args()

    path = Path(args.teams)
    if not path.exists():
        print(f"[ERROR] File not found: {path}")
        return

    total = 0
    by_member: dict[str, int] = defaultdict(int)
    by_boss: dict[str, int] = defaultdict(int)
    rows = 0

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dmg = int(row["damage"].replace(",", ""))
            total += dmg
            by_member[row["member"]] += dmg
            by_boss[row["boss_name"]] += dmg
            rows += 1

    sep = "-" * 44

    print(f"\n  DAMAGE SUMMARY  -  {path.name}")
    print(sep)
    print(f"  Total rows   : {rows}")
    print(f"  Total damage : {fmt_hp(total)}  ({total:,})")

    print(f"\n  By Boss")
    print(sep)
    for boss, dmg in sorted(by_boss.items(), key=lambda x: -x[1]):
        print(f"  {boss:<22} {fmt_hp(dmg):>10}  ({dmg:,})")

    print(f"\n  By Member")
    print(sep)
    for member, dmg in sorted(by_member.items(), key=lambda x: -x[1]):
        print(f"  {member:<22} {fmt_hp(dmg):>10}  ({dmg:,})")

    print()


if __name__ == "__main__":
    main()

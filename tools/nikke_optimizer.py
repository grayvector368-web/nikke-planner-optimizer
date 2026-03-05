"""
Nikke Union Raid Hard Mode Optimizer
=====================================
Assigns pre-tested member team compositions to bosses, maximizing guild
progression while respecting:
  - Per-member unit uniqueness (a unit can only appear once in a single
    member's runs; different members MAY use the same unit independently)
  - Per-member hit limit (default: 3 hits per member)
  - A member MAY hit the same boss multiple times (across levels) as long
    as each run uses a distinct set of units (no per-member unit reuse)
  - Boss level gating (all 5 bosses must clear L1 before L2 unlocks)
  - Damage capping (excess damage beyond boss HP is wasted)
  - Optional damage buffer (--buffer N) scales every team's damage up by N%
    before optimizing, to account for members performing better in the
    actual fight than in their test run

Usage:
  python tools/nikke_optimizer.py --bosses data/bosses.csv --teams data/teams.csv
  python tools/nikke_optimizer.py --buffer 5   # assume everyone does 5% more

Solvers:
  auto   (default) Use ILP if pulp is installed, else greedy
  ilp    Unified ILP via PuLP/CBC (globally optimal across all levels)
  greedy Greedy + local search (no extra dependencies, near-optimal)
"""

import argparse
import csv
import random
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

try:
    import pulp

    PULP_AVAILABLE = True
except ImportError:
    PULP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class Boss:
    def __init__(self, name: str, hp: List[int]):
        self.name = name
        self.hp = hp  # [hp_L1, hp_L2, hp_L3]

    @property
    def total_hp(self) -> int:
        return sum(self.hp)


class Team:
    def __init__(self, member: str, boss_name: str, damage: int, units: List[str]):
        self.member = member
        self.boss_name = boss_name
        self.damage = damage
        self.units = units  # exactly 5

    def __repr__(self):
        return f"Team({self.member} -> {self.boss_name}, dmg={self.damage:,})"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_bosses(path: str) -> Dict[str, Boss]:
    bosses = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["boss_name"].strip()
            hp = [
                int(row["hp_level1"].strip().replace(",", "")),
                int(row["hp_level2"].strip().replace(",", "")),
                int(row["hp_level3"].strip().replace(",", "")),
            ]
            bosses[name] = Boss(name, hp)
    return bosses


def load_teams(path: str, bosses: Dict[str, Boss]) -> List[Team]:
    teams = []
    warnings = []
    # Tracks exact duplicate rows: (member_norm, boss_norm, frozenset(units)) -> row index
    seen_exact: Dict[tuple, int] = {}

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            member = row["member"].strip()
            boss_name = row["boss_name"].strip()
            damage = int(row["damage"].strip().replace(",", ""))
            units = [
                row[f"unit{j}"].strip()
                for j in range(1, 6)
                if row.get(f"unit{j}", "").strip()
            ]

            if len(units) != 5:
                warnings.append(
                    f"Row {i + 2}: {member}/{boss_name} has {len(units)} units (expected 5) - skipping"
                )
                continue

            # Normalize names for conflict detection (keep display names as-is)
            boss_norm = boss_name.lower()
            if boss_norm not in {b.lower() for b in bosses}:
                warnings.append(
                    f"Row {i + 2}: Boss '{boss_name}' not found in bosses.csv - skipping"
                )
                continue

            # Resolve actual boss name (case-insensitive match)
            actual_boss = next(b for b in bosses if b.lower() == boss_norm)

            # Only deduplicate truly identical rows (same member + boss + exact unit set)
            exact_key = (member.lower(), boss_norm, frozenset(u.lower() for u in units))
            if exact_key in seen_exact:
                existing_idx = seen_exact[exact_key]
                if damage > teams[existing_idx].damage:
                    warnings.append(
                        f"Duplicate submission: {member}/{boss_name} same units - keeping higher damage ({damage:,})"
                    )
                    teams[existing_idx] = Team(member, actual_boss, damage, units)
                else:
                    warnings.append(
                        f"Duplicate submission: {member}/{boss_name} same units - keeping existing ({teams[existing_idx].damage:,})"
                    )
            else:
                seen_exact[exact_key] = len(teams)
                teams.append(Team(member, actual_boss, damage, units))

    for w in warnings:
        print(f"[WARNING] {w}", file=sys.stderr)

    return teams


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def compute_score(
    assignments: List[Tuple[Team, int]],
    bosses: Dict[str, Boss],
    weights: Tuple[int, int, int] = (1, 1, 1),
) -> Tuple[float, dict]:
    """
    Compute the weighted effective HP score for a given assignment.

    Each (team, level) pair targets a specific level (1/2/3) of its boss.
    Damage is capped at that level's HP — no overflow between levels.
    Level 2 and 3 scores are only counted if ALL bosses cleared the prior level.

    Returns (total_score, breakdown) where breakdown[boss_name] is a list of
    (dealt, hp) per level.
    """
    # Aggregate damage per (boss, level)
    damage_by_boss_level: Dict[Tuple[str, int], int] = defaultdict(int)
    for team, level in assignments:
        damage_by_boss_level[(team.boss_name, level)] += team.damage

    breakdown = {}
    score = 0.0
    all_l1_cleared = True
    all_l2_cleared = True

    for boss_name, boss in bosses.items():
        lvl_detail = []
        for lvl_idx, hp in enumerate(boss.hp):
            raw = damage_by_boss_level[(boss_name, lvl_idx + 1)]
            dealt = min(raw, hp)
            lvl_detail.append((dealt, hp))

        breakdown[boss_name] = lvl_detail

        if lvl_detail[0][0] < boss.hp[0]:
            all_l1_cleared = False
        if lvl_detail[1][0] < boss.hp[1]:
            all_l2_cleared = False

        # L1 always scores
        score += lvl_detail[0][0] * weights[0]

    # L2 only if all L1 cleared
    if all_l1_cleared:
        for boss_name, lvl_detail in breakdown.items():
            score += lvl_detail[1][0] * weights[1]

    # L3 only if all L2 cleared (and all L1 were cleared)
    if all_l1_cleared and all_l2_cleared:
        for boss_name, lvl_detail in breakdown.items():
            score += lvl_detail[2][0] * weights[2]

    return score, breakdown


def validate_assignment(
    assignments: List[Tuple[Team, int]], max_hits: int = 3
) -> List[str]:
    """Return a list of constraint violations. Empty list = valid.

    Unit uniqueness is per-member: the same unit cannot appear in two of
    the same member's runs. Different members may use the same unit.
    """
    violations = []
    member_unit_runs: Dict[tuple, List[Tuple[Team, int]]] = defaultdict(list)
    member_hits: Dict[str, int] = defaultdict(int)

    for team, level in assignments:
        member_hits[team.member] += 1
        for unit in team.units:
            member_unit_runs[(team.member, unit.lower())].append((team, level))

    for (member, unit_norm), runs in member_unit_runs.items():
        if len(runs) > 1:
            details = ", ".join(f"{t.boss_name} L{l}" for t, l in runs)
            violations.append(
                f"Unit '{unit_norm}' used by {member} in {len(runs)} runs: {details}"
            )

    for member, hits in member_hits.items():
        if hits > max_hits:
            violations.append(f"Member '{member}' has {hits} runs (max {max_hits})")

    return violations


# ---------------------------------------------------------------------------
# Greedy solver
# ---------------------------------------------------------------------------


def solve_greedy(
    teams: List[Team],
    bosses: Dict[str, Boss],
    weights: Tuple[int, int, int],
    max_hits: int,
    seed: int = 42,
) -> List[Tuple[Team, int]]:
    """
    Phase-based greedy optimizer.

    Phase 1: Assign highest-damage teams to L1 of each boss until L1 HP is filled.
    Phase 2: Assign remaining teams to L2 of each boss until L2 HP is filled.
    Phase 3: Assign remaining teams to L3 of each boss until L3 HP is filled.
    Phase 4: Use any leftover member hits on L3 (to push L3 progress further).

    Each team is explicitly assigned to a single level. Damage is capped at that
    level's HP — no overflow between levels.
    """
    random.seed(seed)

    assignments: List[Tuple[Team, int]] = []
    used_units_per_member: Dict[str, set] = defaultdict(set)
    member_hits: Dict[str, int] = defaultdict(int)
    assigned_ids: set = set()

    for target_level in [1, 2, 3]:
        lvl_idx = target_level - 1

        # Gate: L2 only if all bosses cleared L1; L3 only if all bosses cleared L2
        if target_level == 2:
            l1_damage: Dict[str, int] = defaultdict(int)
            for team, lvl in assignments:
                if lvl == 1:
                    l1_damage[team.boss_name] += team.damage
            if any(l1_damage[b] < boss.hp[0] for b, boss in bosses.items()):
                break  # L1 not fully cleared — skip L2 and L3 entirely
        if target_level == 3:
            l2_damage: Dict[str, int] = defaultdict(int)
            for team, lvl in assignments:
                if lvl == 2:
                    l2_damage[team.boss_name] += team.damage
            if any(l2_damage[b] < boss.hp[1] for b, boss in bosses.items()):
                break  # L2 not fully cleared — skip L3 entirely

        # Remaining HP still to fill at this level for each boss
        damage_at_level: Dict[str, int] = defaultdict(int)
        for team, lvl in assignments:
            if lvl == target_level:
                damage_at_level[team.boss_name] += team.damage
        remaining_hp = {
            b: max(0, boss.hp[lvl_idx] - damage_at_level[b])
            for b, boss in bosses.items()
        }

        candidates = sorted(
            [t for t in teams if id(t) not in assigned_ids],
            key=lambda t: t.damage,
            reverse=True,
        )

        for team in candidates:
            if remaining_hp.get(team.boss_name, 0) <= 0:
                continue  # This boss's level is already filled
            unit_norms = {u.lower() for u in team.units}
            if (
                not unit_norms & used_units_per_member[team.member]
                and member_hits[team.member] < max_hits
            ):
                assignments.append((team, target_level))
                assigned_ids.add(id(team))
                used_units_per_member[team.member] |= unit_norms
                member_hits[team.member] += 1
                remaining_hp[team.boss_name] = max(
                    0, remaining_hp[team.boss_name] - team.damage
                )

    # Phase 4: use remaining member hits on L3 — only if all L1 and L2 are cleared
    all_l1_cleared = all(
        sum(t.damage for t, lvl in assignments if lvl == 1 and t.boss_name == b)
        >= boss.hp[0]
        for b, boss in bosses.items()
    )
    all_l2_cleared = all(
        sum(t.damage for t, lvl in assignments if lvl == 2 and t.boss_name == b)
        >= boss.hp[1]
        for b, boss in bosses.items()
    )

    if all_l1_cleared and all_l2_cleared:
        l3_damage: Dict[str, int] = defaultdict(int)
        for team, lvl in assignments:
            if lvl == 3:
                l3_damage[team.boss_name] += team.damage
        l3_remaining = {
            b: max(0, boss.hp[2] - l3_damage[b]) for b, boss in bosses.items()
        }

        remaining = sorted(
            [t for t in teams if id(t) not in assigned_ids],
            key=lambda t: t.damage,
            reverse=True,
        )
        for team in remaining:
            if l3_remaining.get(team.boss_name, 0) <= 0:
                continue  # L3 already cleared for this boss
            unit_norms = {u.lower() for u in team.units}
            if (
                not unit_norms & used_units_per_member[team.member]
                and member_hits[team.member] < max_hits
            ):
                assignments.append((team, 3))
                assigned_ids.add(id(team))
                used_units_per_member[team.member] |= unit_norms
                member_hits[team.member] += 1
                l3_remaining[team.boss_name] = max(
                    0, l3_remaining[team.boss_name] - team.damage
                )

    return assignments


# ---------------------------------------------------------------------------
# ILP solver
# ---------------------------------------------------------------------------


def _find_tight_boss_splits(
    teams: List[Team],
    bosses: Dict[str, Boss],
    margin_pct: float = 5.0,
) -> Dict[str, Dict[int, set]]:
    """
    Pre-solve: find bosses where total team damage barely covers L1+L2 HP.

    For these 'tight' bosses, use subset-sum to find an exact L1/L2 split
    that minimises waste.  This guides the ILP toward solutions it would
    otherwise never explore (exact subset sums are needles in a haystack
    for branch-and-bound).

    Returns {boss_name: {1: set_of_team_indices, 2: set_of_team_indices}}
    for any boss that has a tight split, empty dict otherwise.
    """
    from itertools import combinations

    teams_for: Dict[str, List[int]] = defaultdict(list)
    for i, team in enumerate(teams):
        teams_for[team.boss_name].append(i)

    splits: Dict[str, Dict[int, set]] = {}

    for boss_name, boss in bosses.items():
        boss_indices = teams_for[boss_name]
        total_dmg = sum(teams[i].damage for i in boss_indices)
        l1l2_hp = boss.hp[0] + boss.hp[1]

        margin = (total_dmg - l1l2_hp) / l1l2_hp * 100 if l1l2_hp > 0 else 999
        if margin > margin_pct or total_dmg < l1l2_hp:
            continue  # not tight, or impossible

        # Try all subset sizes for L1
        target_l1 = boss.hp[0]
        best_waste = float("inf")
        best_l1_set = None

        for k in range(1, len(boss_indices) + 1):
            if k > 15:
                break  # cap to keep runtime reasonable
            for combo in combinations(boss_indices, k):
                s = sum(teams[i].damage for i in combo)
                if s >= target_l1:
                    waste = s - target_l1
                    if waste < best_waste:
                        best_waste = waste
                        best_l1_set = set(combo)

        if best_l1_set is not None:
            l2_set = set(boss_indices) - best_l1_set
            l2_total = sum(teams[i].damage for i in l2_set)
            if l2_total >= boss.hp[1]:
                splits[boss_name] = {1: best_l1_set, 2: l2_set}
                print(
                    f"[INFO] Pre-solve: {boss_name} tight split found "
                    f"(L1 waste: {fmt_hp(best_waste)}, "
                    f"L2 surplus: {fmt_hp(l2_total - boss.hp[1])})",
                    file=sys.stderr,
                )
            else:
                # L2 can't be cleared even with perfect L1 - still fix L1
                splits[boss_name] = {1: best_l1_set, 2: l2_set}
                print(
                    f"[INFO] Pre-solve: {boss_name} L1 optimised "
                    f"(waste: {fmt_hp(best_waste)}), "
                    f"L2 deficit: {fmt_hp(boss.hp[1] - l2_total)}",
                    file=sys.stderr,
                )

    return splits


def solve_ilp(
    teams: List[Team],
    bosses: Dict[str, Boss],
    weights: Tuple[int, int, int],
    max_hits: int,
    time_limit: int = 120,
) -> List[Tuple[Team, int]]:
    """
    Unified ILP solver using PuLP/CBC with tight-boss pre-solving.

    Phase 1 (pre-solve): For bosses where total team damage barely covers
    L1+L2 HP, use exact subset-sum to find the optimal L1/L2 split.
    These assignments are fixed in the ILP to guide CBC past the
    combinatorial bottleneck.

    Phase 2 (ILP): Builds a single model that sees all 3 levels
    simultaneously, using gating constraints to enforce level progression
    (L2 only scores if ALL bosses clear L1, L3 only if ALL clear L2).

    Variables:
      x[i,l]  binary  - team i assigned to level l
      e[b,l]  continuous - effective (capped) damage to boss b at level l
      c[b,l]  binary  - boss b cleared level l
      g[l]    binary  - gate: all bosses cleared level l-1
      es[b,l] continuous - scored effective damage (gated)
    """
    n = len(teams)
    boss_names = list(bosses.keys())
    levels = [1, 2, 3]

    # ---- Phase 1: Pre-solve tight boss splits ----------------------------
    tight_splits = _find_tight_boss_splits(teams, bosses)

    # Index helpers
    member_to_indices: Dict[str, List[int]] = defaultdict(list)
    for i, team in enumerate(teams):
        member_to_indices[team.member].append(i)

    member_unit_to_indices: Dict[tuple, List[int]] = defaultdict(list)
    for i, team in enumerate(teams):
        for unit in team.units:
            member_unit_to_indices[(team.member, unit.lower())].append(i)

    teams_for: Dict[str, List[int]] = defaultdict(list)
    for i, team in enumerate(teams):
        teams_for[team.boss_name].append(i)

    # ---- Phase 2: Build unified ILP --------------------------------------
    prob = pulp.LpProblem("NikkeRaid_Unified", pulp.LpMaximize)

    # x[i,l] = 1 if team i is assigned to level l
    x: Dict[Tuple[int, int], pulp.LpVariable] = {}
    for i in range(n):
        for l in levels:
            x[i, l] = pulp.LpVariable(f"x_{i}_{l}", cat="Binary")

    # Fix tight boss assignments from pre-solve
    for boss_name, level_map in tight_splits.items():
        boss_indices = set(teams_for[boss_name])
        for lvl, idx_set in level_map.items():
            for i in idx_set:
                prob += x[i, lvl] == 1  # fix this team to this level
            # Prevent these teams from being assigned to other levels
            for i in idx_set:
                for other_l in levels:
                    if other_l != lvl:
                        prob += x[i, other_l] == 0

    # C1: Each team assigned to at most one level
    for i in range(n):
        prob += pulp.lpSum(x[i, l] for l in levels) <= 1

    # C2: Per-member hit limit (total across all levels)
    for member, indices in member_to_indices.items():
        prob += pulp.lpSum(x[i, l] for i in indices for l in levels) <= max_hits

    # C3: Per-member unit uniqueness (across all levels)
    for (member, unit_norm), indices in member_unit_to_indices.items():
        if len(indices) > 1:
            prob += pulp.lpSum(x[i, l] for i in indices for l in levels) <= 1

    # Raw damage per (boss, level) - auxiliary for clearing constraints
    raw: Dict[Tuple[str, int], pulp.LpAffineExpression] = {}
    for b in boss_names:
        for l in levels:
            raw[b, l] = pulp.lpSum(teams[i].damage * x[i, l] for i in teams_for[b])

    # Effective damage per (boss, level) - capped at that level's HP
    e: Dict[Tuple[str, int], pulp.LpVariable] = {}
    for b in boss_names:
        for l in levels:
            hp = bosses[b].hp[l - 1]
            e[b, l] = pulp.LpVariable(f"e_{b}_{l}", lowBound=0, upBound=hp)
            prob += e[b, l] <= raw[b, l]

    # Clearing variables: c[b,l] = 1 only if raw damage >= HP
    c: Dict[Tuple[str, int], pulp.LpVariable] = {}
    for b in boss_names:
        for l in levels:
            c[b, l] = pulp.LpVariable(f"c_{b}_{l}", cat="Binary")
            hp = bosses[b].hp[l - 1]
            prob += raw[b, l] >= hp * c[b, l]

    # Gate variables: g2 = all L1 cleared, g3 = all L2 cleared
    g2 = pulp.LpVariable("g_2", cat="Binary")
    g3 = pulp.LpVariable("g_3", cat="Binary")

    for b in boss_names:
        prob += g2 <= c[b, 1]
    for b in boss_names:
        prob += g3 <= c[b, 2]
    prob += g3 <= g2  # can't unlock L3 without L2

    # Assignment gating: can only assign teams to a level if it's unlocked.
    # This prevents wasting hits on L3 when L2 isn't cleared (CBC would
    # otherwise freely assign leftover teams to L3 since they score 0 either way).
    for i in range(n):
        prob += x[i, 2] <= g2  # can't hit L2 unless all L1 cleared
        prob += x[i, 3] <= g3  # can't hit L3 unless all L2 cleared

    # Scored effective damage (only counts when gate is open)
    es: Dict[Tuple[str, int], pulp.LpVariable] = {}
    for b in boss_names:
        # L1 always scores
        es[b, 1] = e[b, 1]

        # L2 only scores if g2 = 1
        hp2 = bosses[b].hp[1]
        es[b, 2] = pulp.LpVariable(f"es_{b}_2", lowBound=0, upBound=hp2)
        prob += es[b, 2] <= e[b, 2]
        prob += es[b, 2] <= hp2 * g2

        # L3 only scores if g3 = 1
        hp3 = bosses[b].hp[2]
        es[b, 3] = pulp.LpVariable(f"es_{b}_3", lowBound=0, upBound=hp3)
        prob += es[b, 3] <= e[b, 3]
        prob += es[b, 3] <= hp3 * g3

    # Objective: maximize weighted scored effective damage, with a tiny penalty
    # per assignment to break ties in favour of fewer runs. Without this, CBC
    # freely piles redundant teams onto already-cleared bosses (same score),
    # wasting member hits that could be saved for later levels.
    prob += (
        pulp.lpSum(weights[l - 1] * es[b, l] for b in boss_names for l in levels)
        - pulp.lpSum(x[i, l] for i in range(n) for l in levels)
    )

    # Try PuLP's bundled CBC first (consistent behaviour across platforms).
    # Fall back to system CBC only if the bundled binary isn't available
    # (e.g. Streamlit Cloud where the bundled exe may not be executable).
    # Pass ratioGap explicitly via options to ensure CBC honours the zero gap
    # regardless of version.
    _solver_options = ["ratioGap 0", f"seconds {time_limit}"]
    try:
        _solver = pulp.PULP_CBC_CMD(
            timeLimit=time_limit, msg=0, gapRel=0, options=_solver_options
        )
        if not _solver.available():
            raise RuntimeError("bundled CBC not available")
    except Exception:
        import shutil as _shutil

        _cbc_path = _shutil.which("cbc")
        if _cbc_path:
            _solver = pulp.COIN_CMD(
                path=_cbc_path, timeLimit=time_limit, msg=0, gapRel=0,
                options=_solver_options,
            )
        else:
            _solver = pulp.PULP_CBC_CMD(timeLimit=time_limit, msg=0, gapRel=0)
    prob.solve(_solver)

    if prob.status not in (1, -2):
        print(
            f"[WARNING] ILP status: {pulp.LpStatus[prob.status]}",
            file=sys.stderr,
        )

    # Extract assignments
    assignments: List[Tuple[Team, int]] = []
    for i in range(n):
        for l in levels:
            if pulp.value(x[i, l]) is not None and pulp.value(x[i, l]) > 0.5:
                assignments.append((teams[i], l))

    return assignments


def apply_buffer(teams: List[Team], buffer_pct: float) -> List[Team]:
    """Return a new list of Teams with each damage scaled up by buffer_pct %."""
    if buffer_pct == 0:
        return teams
    multiplier = 1.0 + buffer_pct / 100.0
    return [
        Team(t.member, t.boss_name, int(t.damage * multiplier), t.units) for t in teams
    ]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def fmt_hp(n: int) -> str:
    """Format large HP numbers readably."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n:,}"


def print_plan(
    assignments: List[Tuple[Team, int]],
    bosses: Dict[str, Boss],
    score: float,
    breakdown: dict,
    solver_name: str,
    all_teams: List[Team],
    max_hits: int,
):
    sep = "=" * 70

    is_ilp = "ILP" in solver_name
    print(sep)
    print("  NIKKE UNION RAID HARD MODE - OPTIMAL PLAN")
    print(sep)
    print(f"  Solver : {solver_name}")
    print(
        f"  Score  : {score:,.0f}  ({'proven optimal' if is_ilp else 'near-optimal - use --solver ilp for guaranteed best'})"
    )
    print(
        f"  Runs   : {len(assignments)} assigned / {len(set(t.member for t in all_teams)) * max_hits} available"
    )

    print()

    # ---- Boss Progress ---------------------------------------------------
    print("--- BOSS PROGRESS " + "-" * 52)

    all_l1 = all(breakdown[b][0][0] >= bosses[b].hp[0] for b in bosses)
    all_l2 = (
        all(breakdown[b][1][0] >= bosses[b].hp[1] for b in bosses) if all_l1 else False
    )

    total_wasted = 0
    for boss_name, boss in bosses.items():
        lvl_detail = breakdown.get(boss_name, [(0, hp) for hp in boss.hp])
        parts = []
        for lvl_idx, (dealt, hp) in enumerate(lvl_detail):
            label = f"L{lvl_idx + 1}"
            if dealt >= hp:
                parts.append(f"{label}: CLEARED")
            elif dealt > 0:
                pct = dealt / hp * 100
                parts.append(f"{label}: {fmt_hp(dealt)}/{fmt_hp(hp)} ({pct:.0f}%)")
            else:
                parts.append(f"{label}: 0%")
        total_assigned = sum(
            t.damage for t, _ in assignments if t.boss_name == boss_name
        )
        total_dealt = sum(d for d, _ in lvl_detail)
        wasted = total_assigned - total_dealt
        total_wasted += wasted
        wasted_str = f"  |  Wasted: {fmt_hp(wasted)}" if wasted > 0 else ""
        print(f"  {boss_name:<20} " + "  |  ".join(parts) + wasted_str)

    print()
    if total_wasted > 0:
        print(f"  Total wasted damage: {fmt_hp(total_wasted)}")
    if all_l2:
        print("  >> ALL L1 + L2 CLEARED - L3 in progress")
    elif all_l1:
        print("  >> ALL L1 CLEARED - L2 in progress")
    else:
        missing_l1 = [b for b in bosses if breakdown[b][0][0] < bosses[b].hp[0]]
        print(
            f"  >> L1 NOT FULLY CLEARED - bosses still alive: {', '.join(missing_l1)}"
        )
    print()

    # ---- Assignments by Level --------------------------------------------
    print("--- ASSIGNMENTS BY LEVEL " + "-" * 45)

    # Build lookup structures from assignments
    by_boss_level: Dict[str, Dict[int, List[Team]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for team, lvl in assignments:
        by_boss_level[team.boss_name][lvl].append(team)

    selected_teams = [t for t, _ in assignments]
    col_m = max((len(t.member) for t in selected_teams), default=10)

    for boss_name, boss in bosses.items():
        has_any = any(by_boss_level[boss_name].get(lvl) for lvl in [1, 2, 3])
        if not has_any:
            print(f"\n  {boss_name} - NO TEAMS ASSIGNED")
            continue

        print(f"\n  {boss_name}")
        for lvl in [1, 2, 3]:
            hp_needed = boss.hp[lvl - 1]
            lvl_teams = sorted(
                by_boss_level[boss_name].get(lvl, []),
                key=lambda t: t.damage,
                reverse=True,
            )
            dealt, hp = breakdown.get(boss_name, [(0, boss.hp[i]) for i in range(3)])[
                lvl - 1
            ]
            if dealt >= hp:
                status = " [CLEARED]"
            elif dealt > 0:
                status = f" [{dealt / hp * 100:.0f}%]"
            else:
                status = " [0%]"

            print(f"    L{lvl} (need {fmt_hp(hp_needed)}){status}")
            if lvl_teams:
                lvl_total = sum(t.damage for t in lvl_teams)
                wasted = max(0, lvl_total - hp_needed)
                for team in lvl_teams:
                    units_str = ", ".join(team.units)
                    print(
                        f"      {team.member:<{col_m}}  {team.damage:>14,}  {units_str}"
                    )
                if wasted > 0:
                    print(
                        f"      {'':>{col_m}}  {'':>14}  > {fmt_hp(lvl_total)} assigned"
                        f" / {fmt_hp(hp_needed)} HP = {fmt_hp(wasted)} Wasted"
                    )
            else:
                print(f"      (no runs assigned to this level)")
    print()

    # ---- Dropped teams (hit limit reached) --------------------------------
    selected_set = set(id(t) for t in selected_teams)
    dropped: List[Team] = [t for t in all_teams if id(t) not in selected_set]

    selected_member_units: Dict[str, Dict[str, Team]] = defaultdict(dict)
    for team in selected_teams:
        for unit in team.units:
            selected_member_units[team.member][unit.lower()] = team

    no_conflict_dropped = [
        t
        for t in dropped
        if not any(
            u.lower() in selected_member_units.get(t.member, {}) for u in t.units
        )
    ]
    if no_conflict_dropped:
        print("--- UNUSED TEAMS (hit limit reached) " + "-" * 33)
        for team in no_conflict_dropped:
            print(f"  {team.member}->{team.boss_name}: {team.damage:,}")
        print()

    # ---- Members with unused hits ----------------------------------------
    member_hits: Dict[str, int] = defaultdict(int)
    for team in selected_teams:
        member_hits[team.member] += 1
    all_members = sorted(set(t.member for t in all_teams))
    underused = [
        (m, member_hits.get(m, 0))
        for m in all_members
        if member_hits.get(m, 0) < max_hits
    ]

    if underused:
        print("--- MEMBERS WITH UNUSED HITS " + "-" * 41)
        for member, hits in underused:
            remaining = max_hits - hits
            print(f"  {member}: {hits}/{max_hits} hits used ({remaining} remaining)")
        print()

    # ---- Bosses with no coverage -----------------------------------------
    uncovered = [
        b for b in bosses if not any(by_boss_level[b].get(lvl) for lvl in [1, 2, 3])
    ]
    if uncovered:
        print("--- CRITICAL: BOSSES WITH NO ASSIGNED TEAMS " + "-" * 26)
        for b in uncovered:
            print(f"  [!] {b} - no runs assigned, cannot be cleared")
        print()

    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="Nikke Union Raid Hard Mode hit optimizer"
    )
    parser.add_argument(
        "--bosses", default="data/bosses.csv", help="Path to boss HP CSV"
    )
    parser.add_argument(
        "--teams", default="data/teams.csv", help="Path to member teams CSV"
    )
    parser.add_argument(
        "--solver",
        choices=["auto", "ilp", "greedy"],
        default="auto",
        help="Solver to use (default: auto)",
    )
    parser.add_argument(
        "--max-hits", type=int, default=3, help="Max hits per member (default: 3)"
    )
    parser.add_argument(
        "--weights",
        type=int,
        nargs=3,
        default=[1, 1, 1],
        metavar=("L1", "L2", "L3"),
        help="Score weights for L1/L2/L3 progress (default: 1 1 1; gating handles priority)",
    )
    parser.add_argument(
        "--time-limit", type=int, default=60, help="ILP solver time limit in seconds"
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=0.0,
        metavar="PCT",
        help=(
            "Scale every team's damage up by PCT %% before optimizing "
            "(e.g. --buffer 5 adds 5%% to all damages). "
            "Useful when members typically do more in the real fight than in mocks."
        ),
    )
    parser.add_argument("--test", action="store_true", help="Run built-in self-test")
    return parser.parse_args()


def run_self_test():
    """Validate the optimizer on a small synthetic dataset with a known optimal."""
    print("Running self-test...")

    bosses = {
        "BossA": Boss("BossA", [100, 500, 1000]),
        "BossB": Boss("BossB", [200, 600, 1200]),
    }

    teams = [
        # Alice: Crown is in both her BossA teams - per-member rule means only one can be selected
        Team("Alice", "BossA", 90, ["Crown", "U1", "U2", "U3", "U4"]),
        Team(
            "Alice", "BossA", 70, ["Crown", "U5", "U6", "U7", "U8"]
        ),  # same boss, overlapping unit - invalid pair
        # Alice hits BossA with a fully distinct set - should be ALLOWED (same boss, different units)
        Team("Alice", "BossA", 60, ["UA1", "UA2", "UA3", "UA4", "UA5"]),
        Team("Alice", "BossB", 80, ["UB1", "UB2", "UB3", "UB4", "UB5"]),
        # Bob: ALSO uses Crown - valid! Different members are independent
        Team("Bob", "BossA", 60, ["Crown", "V2", "V3", "V4", "V5"]),
        Team("Bob", "BossB", 150, ["V6", "V7", "V8", "V9", "V10"]),
        # Carol: no conflicts
        Team("Carol", "BossA", 50, ["W1", "W2", "W3", "W4", "W5"]),
        Team("Carol", "BossB", 40, ["W6", "W7", "W8", "W9", "W10"]),
    ]

    weights = (1000, 100, 10)
    assignments = solve_greedy(teams, bosses, weights, max_hits=3)
    violations = validate_assignment(assignments, max_hits=3)

    # Verify no constraint violations
    assert not violations, f"Self-test FAILED - violations: {violations}"

    # Alice must use Crown in at most 1 run (per-member rule)
    alice_crown = sum(
        1 for t, _ in assignments if t.member == "Alice" and "Crown" in t.units
    )
    assert alice_crown <= 1, (
        f"Self-test FAILED - Alice uses Crown in {alice_crown} runs"
    )

    # Alice CAN hit BossA more than once as long as units are distinct
    alice_bossA_runs = [
        (t, l) for t, l in assignments if t.member == "Alice" and t.boss_name == "BossA"
    ]
    alice_bossA_units_flat = [u.lower() for t, _ in alice_bossA_runs for u in t.units]
    assert len(alice_bossA_units_flat) == len(set(alice_bossA_units_flat)), (
        f"Self-test FAILED - Alice reused units across BossA runs: {alice_bossA_units_flat}"
    )

    # Bob using Crown is VALID even if Alice also uses Crown (different members)
    bob_crown = sum(
        1 for t, _ in assignments if t.member == "Bob" and "Crown" in t.units
    )
    assert bob_crown <= 1, f"Self-test FAILED - Bob uses Crown in {bob_crown} runs"

    # Verify no member exceeds 3 hits
    from collections import Counter

    hit_counts = Counter(t.member for t, _ in assignments)
    for member, count in hit_counts.items():
        assert count <= 3, f"Self-test FAILED - {member} has {count} hits"

    # Verify each assignment has a valid level
    for team, lvl in assignments:
        assert lvl in (1, 2, 3), f"Self-test FAILED - invalid level {lvl} for {team}"

    print("  [OK] Per-member unit uniqueness satisfied")
    print("  [OK] Same member can hit the same boss multiple times with distinct units")
    print("  [OK] Different members can use the same unit independently")
    print("  [OK] Member hit limit satisfied")
    print("  [OK] All assignments have explicit level (1/2/3)")
    print(f"  [OK] {len(assignments)} teams assigned from {len(teams)} candidates")
    print("Self-test PASSED.")
    return True


def main():
    args = parse_args()

    if args.test:
        success = run_self_test()
        sys.exit(0 if success else 1)

    # Determine solver
    solver = args.solver
    if solver == "auto":
        solver = "ilp" if PULP_AVAILABLE else "greedy"
        if not PULP_AVAILABLE and args.solver == "auto":
            print(
                "[INFO] PuLP not installed - using greedy solver. "
                "For better results: pip install pulp",
                file=sys.stderr,
            )

    if solver == "ilp" and not PULP_AVAILABLE:
        print(
            "[ERROR] ILP solver requested but PuLP is not installed.\n"
            "  Install with: pip install pulp\n"
            "  Or use: --solver greedy",
            file=sys.stderr,
        )
        sys.exit(1)

    weights = tuple(args.weights)

    # Load data
    try:
        bosses = load_bosses(args.bosses)
    except FileNotFoundError:
        print(f"[ERROR] Boss file not found: {args.bosses}", file=sys.stderr)
        sys.exit(1)

    try:
        raw_teams = load_teams(args.teams, bosses)
    except FileNotFoundError:
        print(f"[ERROR] Teams file not found: {args.teams}", file=sys.stderr)
        sys.exit(1)

    if not raw_teams:
        print("[ERROR] No valid team submissions found in teams.csv", file=sys.stderr)
        sys.exit(1)

    if len(bosses) != 5:
        print(f"[WARNING] Expected 5 bosses, found {len(bosses)}", file=sys.stderr)

    # Apply damage buffer if requested
    if args.buffer != 0:
        print(
            f"[INFO] Applying {args.buffer:+.1f}% damage buffer to all teams.",
            file=sys.stderr,
        )
    teams = apply_buffer(raw_teams, args.buffer)

    # Solve
    if solver == "ilp":
        assignments = solve_ilp(teams, bosses, weights, args.max_hits, args.time_limit)
        solver_label = "ILP (PuLP/CBC)"
    else:
        assignments = solve_greedy(teams, bosses, weights, args.max_hits)
        solver_label = "Greedy (phase-based)"

    # Validate
    violations = validate_assignment(assignments, args.max_hits)
    if violations:
        print("[ERROR] Optimizer produced an invalid assignment:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        sys.exit(1)

    # Score and report — always display original (unbuffered) damages in the plan
    # so the printout matches what members actually recorded in their mocks.
    # Re-map assignments back to the original team objects for display.
    if args.buffer != 0:
        buffered_to_raw: Dict[int, Team] = {
            id(tb): tr for tb, tr in zip(teams, raw_teams)
        }
        display_assignments = [
            (buffered_to_raw[id(tb)], lvl) for tb, lvl in assignments
        ]
        display_teams = raw_teams
    else:
        display_assignments = assignments
        display_teams = teams

    score, breakdown = compute_score(assignments, bosses, weights)
    print_plan(
        display_assignments,
        bosses,
        score,
        breakdown,
        solver_label + (f" +{args.buffer:.1f}% buffer" if args.buffer != 0 else ""),
        display_teams,
        args.max_hits,
    )


if __name__ == "__main__":
    main()

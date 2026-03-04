"""
Tests for nikke_optimizer.py

Run with:
  python -m pytest tests/
  python -m unittest discover tests/
  python tests/test_optimizer.py
"""

import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from nikke_optimizer import (
    Boss,
    Team,
    compute_score,
    load_bosses,
    load_teams,
    solve_greedy,
    validate_assignment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_bosses(**kwargs):
    """Create bosses dict from keyword args.  make_bosses(A=[100,500,1000])"""
    return {name: Boss(name, hp) for name, hp in kwargs.items()}


def t(member, boss, damage, *units):
    """Shorthand Team factory. Units default to unique placeholders if fewer than 5 given."""
    all_units = list(units)
    while len(all_units) < 5:
        all_units.append(f"_filler_{member}_{boss}_{len(all_units)}")
    return Team(member, boss, damage, all_units[:5])


def write_csv(rows, fieldnames, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------


class TestComputeScore(unittest.TestCase):
    def setUp(self):
        self.bosses = make_bosses(
            A=[1000, 5000, 10000],
            B=[2000, 6000, 12000],
            C=[1500, 4500, 9000],
            D=[1200, 3600, 7200],
            E=[800, 2400, 4800],
        )
        self.weights = (1000, 100, 10)

    def test_empty_selection_scores_zero(self):
        score, breakdown = compute_score([], self.bosses, self.weights)
        self.assertEqual(score, 0.0)
        for boss, lvls in breakdown.items():
            self.assertEqual(lvls[0][0], 0)

    def test_partial_l1_only(self):
        # Each boss gets some damage below L1 threshold
        teams = [
            (t("Alice", "A", 500), 1),
            (t("Bob", "B", 800), 1),
        ]
        score, breakdown = compute_score(teams, self.bosses, self.weights)
        # L1 contributes 500 + 800 = 1300 * 1000
        self.assertEqual(score, 1_300_000)
        self.assertEqual(breakdown["A"][0], (500, 1000))
        self.assertEqual(breakdown["B"][0], (800, 2000))

    def test_damage_capped_at_l1_hp(self):
        # Deal more than L1 HP — excess must not count as extra L1 score
        teams = [(t("Alice", "A", 99999), 1)]  # Way more than 1000 (L1 HP)
        score, breakdown = compute_score(teams, self.bosses, self.weights)
        l1_dealt, l1_hp = breakdown["A"][0]
        self.assertEqual(l1_dealt, 1000)  # Capped at L1 HP
        # Score from A L1 only (other bosses get 0)
        self.assertGreaterEqual(score, 1000 * 1000)

    def test_l2_unlocks_only_when_all_l1_cleared(self):
        # Clear L1 on all except boss E — L2 should not count at all
        teams = [
            (t("Alice", "A", 2000), 1),  # clears L1 of A (overflow to L2)
            (t("Bob", "B", 2000), 1),  # clears L1 of B
            (t("Carol", "C", 2000), 1),  # clears L1 of C
            (t("David", "D", 2000), 1),  # clears L1 of D
            # E is NOT cleared
        ]
        score, breakdown = compute_score(teams, self.bosses, self.weights)
        # L2 should contribute nothing since E is not cleared
        l2_score_contribution = sum(lvl[1][0] for boss_name, lvl in breakdown.items())
        # We can't directly assert on score since weights mix L1+L2,
        # so verify by checking that breakdown L2 values are not credited
        # (score should be exactly sum of L1 credits only)
        expected_l1 = (
            min(2000, 1000) * 1000  # A
            + min(2000, 2000) * 1000  # B
            + min(2000, 1500) * 1000  # C
            + min(2000, 1200) * 1000  # D
            + 0  # E
        )
        self.assertEqual(score, expected_l1)

    def test_l2_unlocks_when_all_l1_cleared(self):
        # Clear L1 on all 5 bosses and deal some L2 damage — L2 score should be counted
        teams = [
            (t("Alice", "A", 1000), 1),  # clears L1 of A exactly
            (t("Bob", "B", 2000), 1),  # clears L1 of B exactly
            (t("Carol", "C", 1500), 1),  # clears L1 of C exactly
            (t("David", "D", 1200), 1),  # clears L1 of D exactly
            (t("Eve", "E", 800), 1),  # clears L1 of E exactly
            (t("Alice2", "A", 1000), 2),  # L2 damage on A
        ]
        score, breakdown = compute_score(teams, self.bosses, self.weights)
        # All L1 should be cleared
        for boss_name in self.bosses:
            l1_dealt, l1_hp = breakdown[boss_name][0]
            self.assertEqual(l1_dealt, l1_hp, f"{boss_name} L1 not cleared")
        # Score should include L2 contribution from A (1000 * 100 = 100_000 on top of L1)
        expected_l1 = (1000 + 2000 + 1500 + 1200 + 800) * 1000
        self.assertGreaterEqual(score, expected_l1 + 1000 * 100)

    def test_l3_only_when_all_l2_cleared(self):
        # Explicitly assign L1, L2, and L3 hits to clear all levels
        bosses = make_bosses(
            A=[100, 200, 300],
            B=[100, 200, 300],
            C=[100, 200, 300],
            D=[100, 200, 300],
            E=[100, 200, 300],
        )
        teams = (
            [(t(f"M1_{boss}", boss, 100), 1) for boss in "ABCDE"]  # clear L1 all bosses
            + [
                (t(f"M2_{boss}", boss, 200), 2) for boss in "ABCDE"
            ]  # clear L2 all bosses
            + [(t(f"M3_{boss}", boss, 200), 3) for boss in "ABCDE"]  # partial L3
        )
        score, breakdown = compute_score(teams, bosses, self.weights)
        # Each boss L1 and L2 cleared; L3 partial (200/300)
        for boss_name in bosses:
            self.assertEqual(breakdown[boss_name][0], (100, 100))
            self.assertEqual(breakdown[boss_name][1], (200, 200))
            self.assertEqual(breakdown[boss_name][2], (200, 300))

    def test_explicit_level_damage_does_not_overflow(self):
        # Damage assigned to L1 stays at L1 — no automatic overflow to L2/L3
        bosses = make_bosses(
            A=[100, 200, 300],
            B=[1000, 1000, 1000],
            C=[1000, 1000, 1000],
            D=[1000, 1000, 1000],
            E=[1000, 1000, 1000],
        )
        # Assign 450 explicitly to L1 of A: only L1 gets the damage (capped at 100)
        teams = [(t("Alice", "A", 450), 1)]
        score, breakdown = compute_score(teams, bosses, self.weights)
        self.assertEqual(breakdown["A"][0], (100, 100))  # capped at L1 HP
        self.assertEqual(breakdown["A"][1], (0, 200))  # L2 untouched
        self.assertEqual(breakdown["A"][2], (0, 300))  # L3 untouched

        # Separately assign to each level to fill them all
        teams_all_levels = [
            (t("Alice", "A", 450), 1),  # L1 of A
            (t("Bob", "A", 200), 2),  # L2 of A
            (t("Carol", "A", 150), 3),  # partial L3 of A
            # Clear other bosses L1 so L2 scoring unlocks
            (t("M_B", "B", 1000), 1),
            (t("M_C", "C", 1000), 1),
            (t("M_D", "D", 1000), 1),
            (t("M_E", "E", 1000), 1),
        ]
        score2, breakdown2 = compute_score(teams_all_levels, bosses, self.weights)
        self.assertEqual(breakdown2["A"][0], (100, 100))
        self.assertEqual(breakdown2["A"][1], (200, 200))
        self.assertEqual(breakdown2["A"][2], (150, 300))


# ---------------------------------------------------------------------------
# validate_assignment
# ---------------------------------------------------------------------------


class TestValidateAssignment(unittest.TestCase):
    def test_empty_is_valid(self):
        self.assertEqual(validate_assignment([]), [])

    def test_no_conflict_no_violation(self):
        teams = [
            (Team("Alice", "A", 100, ["C1", "C2", "C3", "C4", "C5"]), 1),
            (Team("Alice", "B", 100, ["C6", "C7", "C8", "C9", "C10"]), 1),
            (Team("Bob", "A", 100, ["C1", "C2", "C3", "C4", "C5"]), 1),  # Bob can reuse
        ]
        self.assertEqual(validate_assignment(teams), [])

    def test_per_member_unit_conflict(self):
        # Alice uses Crown in two runs - violation
        teams = [
            (Team("Alice", "A", 100, ["Crown", "U1", "U2", "U3", "U4"]), 1),
            (Team("Alice", "B", 100, ["Crown", "U5", "U6", "U7", "U8"]), 1),
        ]
        violations = validate_assignment(teams)
        self.assertEqual(len(violations), 1)
        self.assertIn("Alice", violations[0])
        self.assertIn("crown", violations[0].lower())

    def test_different_members_same_unit_is_valid(self):
        # Alice and Bob both use Crown - NOT a violation
        teams = [
            (Team("Alice", "A", 100, ["Crown", "U1", "U2", "U3", "U4"]), 1),
            (Team("Bob", "A", 100, ["Crown", "V1", "V2", "V3", "V4"]), 1),
            (Team("Carol", "B", 100, ["Crown", "W1", "W2", "W3", "W4"]), 1),
        ]
        self.assertEqual(validate_assignment(teams), [])

    def test_hit_limit_exceeded(self):
        teams = [
            (t("Alice", "A", 100), 1),
            (t("Alice", "B", 100), 1),
            (t("Alice", "C", 100), 1),
            (t("Alice", "D", 100), 1),  # 4th hit - violation with max_hits=3
        ]
        violations = validate_assignment(teams, max_hits=3)
        self.assertTrue(any("Alice" in v for v in violations))

    def test_hit_limit_exact(self):
        teams = [(t("Alice", c, 100), 1) for c in ["A", "B", "C"]]
        self.assertEqual(validate_assignment(teams, max_hits=3), [])

    def test_multiple_unit_conflicts_same_member(self):
        # Alice shares 2 different units across different pairs of runs
        teams = [
            (Team("Alice", "A", 100, ["Crown", "U1", "U2", "U3", "U4"]), 1),
            (
                Team("Alice", "B", 100, ["Crown", "U5", "U6", "U7", "U8"]),
                2,
            ),  # Crown conflict
            (
                Team("Alice", "C", 100, ["Liter", "U9", "U10", "Crown", "U12"]),
                3,
            ),  # Crown conflict again
        ]
        violations = validate_assignment(teams, max_hits=3)
        unit_violations = [v for v in violations if "Alice" in v and "run" in v]
        self.assertGreater(len(unit_violations), 0)

    def test_no_hit_limit_violation_different_members(self):
        # 4 members each with 3 hits - no violation
        teams = []
        for member in ["Alice", "Bob", "Carol", "Dave"]:
            for boss in ["A", "B", "C"]:
                teams.append((t(member, boss, 100), 1))
        self.assertEqual(validate_assignment(teams, max_hits=3), [])


# ---------------------------------------------------------------------------
# solve_greedy
# ---------------------------------------------------------------------------


class TestSolveGreedy(unittest.TestCase):
    def setUp(self):
        self.bosses = make_bosses(
            A=[1000, 5000, 10000],
            B=[2000, 6000, 12000],
        )
        self.weights = (1000, 100, 10)

    def test_no_conflicts_all_selected(self):
        teams = [
            t("Alice", "A", 900),
            t("Alice", "B", 800),
            t("Bob", "A", 700),
            t("Bob", "B", 600),
        ]
        selected = solve_greedy(teams, self.bosses, self.weights, max_hits=3)
        self.assertEqual(len(selected), 4)

    def test_per_member_unit_conflict_drops_one(self):
        # Alice uses Crown in both teams - greedy should keep only the better one
        teams = [
            Team("Alice", "A", 900, ["Crown", "U1", "U2", "U3", "U4"]),  # higher damage
            Team(
                "Alice", "B", 400, ["Crown", "V1", "V2", "V3", "V4"]
            ),  # lower - should drop
        ]
        selected = solve_greedy(teams, self.bosses, self.weights, max_hits=3)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [])
        alice_teams = [team for team, _ in selected if team.member == "Alice"]
        self.assertEqual(len(alice_teams), 1)

    def test_different_members_same_unit_both_selected(self):
        # Alice and Bob both use Crown - both should be selected
        teams = [
            Team("Alice", "A", 900, ["Crown", "U1", "U2", "U3", "U4"]),
            Team("Bob", "B", 800, ["Crown", "V1", "V2", "V3", "V4"]),
        ]
        selected = solve_greedy(teams, self.bosses, self.weights, max_hits=3)
        self.assertEqual(len(selected), 2)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [])

    def test_hit_limit_respected(self):
        # Give Alice 5 non-conflicting teams but max_hits=3
        bosses = make_bosses(
            A=[10, 10, 10],
            B=[10, 10, 10],
            C=[10, 10, 10],
            D=[10, 10, 10],
            E=[10, 10, 10],
        )
        teams = [t("Alice", boss, 100) for boss in ["A", "B", "C", "D", "E"]]
        selected = solve_greedy(teams, bosses, self.weights, max_hits=3)
        alice_hits = sum(1 for team, _ in selected if team.member == "Alice")
        self.assertLessEqual(alice_hits, 3)

    def test_output_passes_validation(self):
        teams = [
            Team("Alice", "A", 900, ["Crown", "Siren", "Scarlet", "Liter", "Noir"]),
            Team("Alice", "B", 700, ["Crown", "Tia", "Naga", "Privaty", "Rapi"]),
            Team("Bob", "A", 800, ["Crown", "Siren", "Scarlet", "Liter", "Noir"]),
            Team("Bob", "B", 600, ["Helm", "Anchor", "Maxwell", "Noise", "Mast"]),
            Team("Carol", "A", 750, ["U1", "U2", "U3", "U4", "U5"]),
            Team("Carol", "B", 550, ["U6", "U7", "U8", "U9", "U10"]),
        ]
        selected = solve_greedy(teams, self.bosses, self.weights, max_hits=3)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [], f"Greedy produced invalid plan: {violations}")

    def test_prefers_higher_damage_when_conflict(self):
        # Alice has Crown conflict: 900 vs 400. Greedy should keep the 900 team.
        teams = [
            Team("Alice", "A", 900, ["Crown", "U1", "U2", "U3", "U4"]),
            Team("Alice", "B", 400, ["Crown", "V1", "V2", "V3", "V4"]),
        ]
        selected = solve_greedy(teams, self.bosses, self.weights, max_hits=3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0].damage, 900)

    def test_multiple_members_crown_all_valid(self):
        # 4 members all using Crown — all should be selectable since per-member rule
        bosses = make_bosses(
            A=[100, 100, 100],
            B=[100, 100, 100],
            C=[100, 100, 100],
            D=[100, 100, 100],
            E=[100, 100, 100],
        )
        teams = [
            Team("Alice", "A", 100, ["Crown", "A1", "A2", "A3", "A4"]),
            Team("Bob", "B", 100, ["Crown", "B1", "B2", "B3", "B4"]),
            Team("Carol", "C", 100, ["Crown", "C1", "C2", "C3", "C4"]),
            Team("David", "D", 100, ["Crown", "D1", "D2", "D3", "D4"]),
        ]
        selected = solve_greedy(teams, bosses, self.weights, max_hits=3)
        self.assertEqual(len(selected), 4)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [])

    def test_empty_teams_returns_empty(self):
        selected = solve_greedy([], self.bosses, self.weights, max_hits=3)
        self.assertEqual(selected, [])

    def test_member_can_use_three_different_boss_teams(self):
        bosses = make_bosses(
            A=[10, 10, 10],
            B=[10, 10, 10],
            C=[10, 10, 10],
            D=[10, 10, 10],
            E=[10, 10, 10],
        )
        teams = [
            t("Alice", "A", 100),
            t("Alice", "B", 90),
            t("Alice", "C", 80),
        ]
        selected = solve_greedy(teams, bosses, self.weights, max_hits=3)
        self.assertEqual(len(selected), 3)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [])


# ---------------------------------------------------------------------------
# load_bosses / load_teams
# ---------------------------------------------------------------------------


class TestLoadData(unittest.TestCase):
    def _write_bosses_csv(self, path, rows):
        write_csv(rows, ["boss_name", "hp_level1", "hp_level2", "hp_level3"], path)

    def _write_teams_csv(self, path, rows):
        write_csv(
            rows,
            [
                "member",
                "boss_name",
                "damage",
                "unit1",
                "unit2",
                "unit3",
                "unit4",
                "unit5",
            ],
            path,
        )

    def test_load_bosses_basic(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            path = f.name
        self._write_bosses_csv(
            path,
            [
                {
                    "boss_name": "BossA",
                    "hp_level1": "1000",
                    "hp_level2": "5000",
                    "hp_level3": "10000",
                },
                {
                    "boss_name": "BossB",
                    "hp_level1": "2000",
                    "hp_level2": "6000",
                    "hp_level3": "12000",
                },
            ],
        )
        bosses = load_bosses(path)
        self.assertEqual(len(bosses), 2)
        self.assertEqual(bosses["BossA"].hp, [1000, 5000, 10000])
        self.assertEqual(bosses["BossB"].hp, [2000, 6000, 12000])
        os.unlink(path)

    def test_load_bosses_with_commas_in_numbers(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            path = f.name
        self._write_bosses_csv(
            path,
            [
                {
                    "boss_name": "BossA",
                    "hp_level1": "1,000,000",
                    "hp_level2": "5,000,000",
                    "hp_level3": "10,000,000",
                },
            ],
        )
        bosses = load_bosses(path)
        self.assertEqual(bosses["BossA"].hp, [1_000_000, 5_000_000, 10_000_000])
        os.unlink(path)

    def test_load_teams_basic(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            boss_path = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            team_path = f.name
        self._write_bosses_csv(
            boss_path,
            [
                {
                    "boss_name": "BossA",
                    "hp_level1": "1000",
                    "hp_level2": "5000",
                    "hp_level3": "10000",
                },
            ],
        )
        self._write_teams_csv(
            team_path,
            [
                {
                    "member": "Alice",
                    "boss_name": "BossA",
                    "damage": "900",
                    "unit1": "Crown",
                    "unit2": "U1",
                    "unit3": "U2",
                    "unit4": "U3",
                    "unit5": "U4",
                },
            ],
        )
        bosses = load_bosses(boss_path)
        teams = load_teams(team_path, bosses)
        self.assertEqual(len(teams), 1)
        self.assertEqual(teams[0].member, "Alice")
        self.assertEqual(teams[0].boss_name, "BossA")
        self.assertEqual(teams[0].damage, 900)
        self.assertEqual(teams[0].units, ["Crown", "U1", "U2", "U3", "U4"])
        os.unlink(boss_path)
        os.unlink(team_path)

    def test_load_teams_unknown_boss_skipped(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            boss_path = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            team_path = f.name
        self._write_bosses_csv(
            boss_path,
            [
                {
                    "boss_name": "BossA",
                    "hp_level1": "1000",
                    "hp_level2": "5000",
                    "hp_level3": "10000",
                },
            ],
        )
        self._write_teams_csv(
            team_path,
            [
                {
                    "member": "Alice",
                    "boss_name": "UnknownBoss",
                    "damage": "900",
                    "unit1": "Crown",
                    "unit2": "U1",
                    "unit3": "U2",
                    "unit4": "U3",
                    "unit5": "U4",
                },
            ],
        )
        bosses = load_bosses(boss_path)
        import io
        from contextlib import redirect_stderr

        with redirect_stderr(io.StringIO()):
            teams = load_teams(team_path, bosses)
        self.assertEqual(len(teams), 0)
        os.unlink(boss_path)
        os.unlink(team_path)

    def test_load_teams_same_member_same_boss_different_units_both_kept(self):
        """Same member hitting the same boss with different unit sets = two valid teams."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            boss_path = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            team_path = f.name
        self._write_bosses_csv(
            boss_path,
            [
                {
                    "boss_name": "BossA",
                    "hp_level1": "1000",
                    "hp_level2": "5000",
                    "hp_level3": "10000",
                },
            ],
        )
        self._write_teams_csv(
            team_path,
            [
                {
                    "member": "Alice",
                    "boss_name": "BossA",
                    "damage": "700",
                    "unit1": "C1",
                    "unit2": "C2",
                    "unit3": "C3",
                    "unit4": "C4",
                    "unit5": "C5",
                },
                {
                    "member": "Alice",
                    "boss_name": "BossA",
                    "damage": "900",
                    "unit1": "D1",
                    "unit2": "D2",
                    "unit3": "D3",
                    "unit4": "D4",
                    "unit5": "D5",
                },
            ],
        )
        bosses = load_bosses(boss_path)
        teams = load_teams(team_path, bosses)
        # Both are distinct teams (different units) - both should be kept
        self.assertEqual(len(teams), 2)
        damages = sorted(t.damage for t in teams)
        self.assertEqual(damages, [700, 900])
        os.unlink(boss_path)
        os.unlink(team_path)

    def test_load_teams_exact_duplicate_keeps_higher_damage(self):
        """Exact duplicate (same member, boss, AND same unit set) deduplicates to the higher damage entry."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            boss_path = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            team_path = f.name
        self._write_bosses_csv(
            boss_path,
            [
                {
                    "boss_name": "BossA",
                    "hp_level1": "1000",
                    "hp_level2": "5000",
                    "hp_level3": "10000",
                },
            ],
        )
        self._write_teams_csv(
            team_path,
            [
                {
                    "member": "Alice",
                    "boss_name": "BossA",
                    "damage": "700",
                    "unit1": "C1",
                    "unit2": "C2",
                    "unit3": "C3",
                    "unit4": "C4",
                    "unit5": "C5",
                },
                {
                    "member": "Alice",
                    "boss_name": "BossA",
                    "damage": "900",  # same units, higher damage
                    "unit1": "C1",
                    "unit2": "C2",
                    "unit3": "C3",
                    "unit4": "C4",
                    "unit5": "C5",
                },
            ],
        )
        bosses = load_bosses(boss_path)
        import io
        from contextlib import redirect_stderr

        with redirect_stderr(io.StringIO()):
            teams = load_teams(team_path, bosses)
        # Exact same unit set = true duplicate, deduplicated to the stronger one
        self.assertEqual(len(teams), 1)
        self.assertEqual(teams[0].damage, 900)
        os.unlink(boss_path)
        os.unlink(team_path)

    def test_load_teams_case_insensitive_boss_match(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            boss_path = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            team_path = f.name
        self._write_bosses_csv(
            boss_path,
            [
                {
                    "boss_name": "Storm Bringer",
                    "hp_level1": "1000",
                    "hp_level2": "5000",
                    "hp_level3": "10000",
                },
            ],
        )
        self._write_teams_csv(
            team_path,
            [
                {
                    "member": "Alice",
                    "boss_name": "storm bringer",
                    "damage": "900",
                    "unit1": "C1",
                    "unit2": "C2",
                    "unit3": "C3",
                    "unit4": "C4",
                    "unit5": "C5",
                },
            ],
        )
        bosses = load_bosses(boss_path)
        teams = load_teams(team_path, bosses)
        self.assertEqual(len(teams), 1)
        self.assertEqual(teams[0].boss_name, "Storm Bringer")  # uses canonical name
        os.unlink(boss_path)
        os.unlink(team_path)


# ---------------------------------------------------------------------------
# Integration: full greedy run on realistic data
# ---------------------------------------------------------------------------


class TestIntegration(unittest.TestCase):
    def _five_bosses(self):
        return make_bosses(**{f"Boss{c}": [1000, 5000, 10000] for c in "ABCDE"})

    def test_all_l1_cleared_with_enough_members(self):
        bosses = self._five_bosses()
        # 5 members each with one team per boss, damage = 200 each
        # 5 members * 200 = 1000 per boss = exactly L1 HP
        teams = []
        for i, member in enumerate(["M1", "M2", "M3", "M4", "M5"]):
            for boss in "ABCDE":
                teams.append(t(member, f"Boss{boss}", 200))
        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=3)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [])

    def test_greedy_result_never_violates_constraints(self):
        """Fuzz: generate random team submissions and verify greedy always produces valid output."""
        import random

        random.seed(99)
        bosses = self._five_bosses()
        unit_pool = [f"Unit{i}" for i in range(30)]
        teams = []
        for member in [f"Player{i}" for i in range(10)]:
            for boss in "ABCDE":
                units = random.sample(unit_pool, 5)
                dmg = random.randint(100, 2000)
                teams.append(Team(member, f"Boss{boss}", dmg, units))
        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=3)
        violations = validate_assignment(selected, max_hits=3)
        self.assertEqual(violations, [], f"Greedy produced violations: {violations}")

    def test_score_improves_or_stays_with_more_members(self):
        bosses = self._five_bosses()
        teams_2 = [t("Alice", f"Boss{b}", 500) for b in "ABCDE"] + [
            t("Bob", f"Boss{b}", 500) for b in "ABCDE"
        ]
        teams_4 = (
            teams_2
            + [t("Carol", f"Boss{b}", 500) for b in "ABCDE"]
            + [t("Dave", f"Boss{b}", 500) for b in "ABCDE"]
        )
        w = (1000, 100, 10)
        sel2 = solve_greedy(teams_2, bosses, w, max_hits=3)
        sel4 = solve_greedy(teams_4, bosses, w, max_hits=3)
        score2, _ = compute_score(sel2, bosses, w)
        score4, _ = compute_score(sel4, bosses, w)
        self.assertGreaterEqual(score4, score2)


# ---------------------------------------------------------------------------
# Level gating: L2/L3 must not be assigned if any boss hasn't cleared L1/L2
# ---------------------------------------------------------------------------


class TestLevelGating(unittest.TestCase):
    """
    Regression tests for the bug where the solver would assign hits to L2/L3
    even when one or more bosses hadn't cleared the prior level.

    Real-world symptom observed:
        Porter      L1: CLEARED | L2: CLEARED | L3: 28%
        Plate       L1: CLEARED | L2: CLEARED | L3: 54%
        Land Eater  L1: CLEARED | L2: CLEARED | L3: 8%
        Rebuild     L1: CLEARED | L2: CLEARED | L3: 36%
        Material H  L1: 0%      | L2: 0%      | L3: 0%   <-- never cleared!
    """

    def _bosses_with_weak_material_h(self):
        """4 easy bosses + Material H with a much higher L1 HP that won't be cleared."""
        return make_bosses(
            Porter=[100, 500, 1000],
            Plate=[100, 500, 1000],
            LandEater=[100, 500, 1000],
            Rebuild=[100, 500, 1000],
            MaterialH=[99999, 200000, 400000],  # too high to clear with given teams
        )

    def test_no_l2_assignments_when_one_boss_l1_uncleared(self):
        """If Material H L1 is not cleared, no team should be assigned to L2 of any boss."""
        bosses = self._bosses_with_weak_material_h()
        # 5 members, each with a team for every boss — enough to clear the 4 easy bosses
        # but nowhere near enough damage for Material H L1
        teams = []
        for i, member in enumerate(["M1", "M2", "M3", "M4", "M5"]):
            teams.append(t(member, "Porter", 100))
            teams.append(t(member, "Plate", 100))
            teams.append(t(member, "LandEater", 100))
            teams.append(t(member, "Rebuild", 100))
            teams.append(t(member, "MaterialH", 10))  # tiny — can't clear L1

        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=5)

        levels_assigned = [lvl for _, lvl in selected]
        self.assertNotIn(
            2,
            levels_assigned,
            "No team should be assigned to L2 while Material H L1 is uncleared",
        )
        self.assertNotIn(
            3,
            levels_assigned,
            "No team should be assigned to L3 while Material H L1 is uncleared",
        )

    def test_no_l3_assignments_when_one_boss_l2_uncleared(self):
        """If one boss L2 is not cleared, no team should be assigned to L3 of any boss."""
        bosses = make_bosses(
            Porter=[100, 500, 1000],
            Plate=[100, 500, 1000],
            LandEater=[100, 500, 1000],
            Rebuild=[100, 500, 1000],
            MaterialH=[100, 99999, 400000],  # L1 clearable, L2 not
        )
        teams = []
        for i, member in enumerate(["M1", "M2", "M3", "M4", "M5"]):
            teams.append(t(member, "Porter", 200))  # clears L1, contributes to L2
            teams.append(t(member, "Plate", 200))
            teams.append(t(member, "LandEater", 200))
            teams.append(t(member, "Rebuild", 200))
            teams.append(t(member, "MaterialH", 20))  # clears L1, but L2 HP = 99999

        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=5)

        levels_assigned = [lvl for _, lvl in selected]
        self.assertNotIn(
            3,
            levels_assigned,
            "No team should be assigned to L3 while Material H L2 is uncleared",
        )

    def test_l1_only_score_when_material_h_uncleared(self):
        """Score must only count L1 damage when Material H L1 is not cleared."""
        bosses = self._bosses_with_weak_material_h()
        teams = []
        for i, member in enumerate(["M1", "M2", "M3", "M4", "M5"]):
            teams.append(t(member, "Porter", 100))
            teams.append(t(member, "Plate", 100))
            teams.append(t(member, "LandEater", 100))
            teams.append(t(member, "Rebuild", 100))
            teams.append(t(member, "MaterialH", 10))

        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=5)
        score, breakdown = compute_score(selected, bosses, (1000, 100, 10))

        # L1 of the 4 easy bosses should be cleared
        for boss_name in ["Porter", "Plate", "LandEater", "Rebuild"]:
            l1_dealt, l1_hp = breakdown[boss_name][0]
            self.assertEqual(l1_dealt, l1_hp, f"{boss_name} L1 should be cleared")

        # Material H L1 should NOT be cleared
        mh_l1_dealt, mh_l1_hp = breakdown["MaterialH"][0]
        self.assertLess(mh_l1_dealt, mh_l1_hp, "Material H L1 should not be cleared")

        # L2 score for all bosses must be zero (gated)
        for boss_name in bosses:
            l2_dealt, _ = breakdown[boss_name][1]
            self.assertEqual(
                l2_dealt,
                0,
                f"{boss_name} L2 should have 0 damage assigned (L2 gated by Material H)",
            )

    def test_l2_and_l3_assigned_only_when_all_l1_cleared(self):
        """When every boss clears L1, the solver should proceed to assign L2 hits."""
        bosses = make_bosses(
            Porter=[100, 500, 1000],
            Plate=[100, 500, 1000],
            LandEater=[100, 500, 1000],
            Rebuild=[100, 500, 1000],
            MaterialH=[100, 500, 1000],
        )
        # 10 members, 3 hits each = 30 hits total; plenty to clear all L1s and push L2
        teams = []
        for i, member in enumerate([f"M{i}" for i in range(10)]):
            for boss in ["Porter", "Plate", "LandEater", "Rebuild", "MaterialH"]:
                teams.append(t(member, boss, 200))

        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=3)
        levels_assigned = set(lvl for _, lvl in selected)

        # With 10 members * 3 hits and 100 L1 HP per boss, L1 should clear
        # and the solver should advance to L2
        self.assertIn(
            2,
            levels_assigned,
            "Solver should assign L2 hits once all L1 bosses are cleared",
        )

    def test_validate_catches_no_violations_for_gated_plan(self):
        """A plan that only assigns L1 hits due to gating must still pass validation."""
        bosses = self._bosses_with_weak_material_h()
        teams = []
        for member in ["M1", "M2", "M3"]:
            for boss in ["Porter", "Plate", "LandEater", "Rebuild", "MaterialH"]:
                teams.append(t(member, boss, 50))

        selected = solve_greedy(teams, bosses, (1000, 100, 10), max_hits=5)
        violations = validate_assignment(selected, max_hits=5)
        self.assertEqual(
            violations,
            [],
            f"Gated plan should have no constraint violations: {violations}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

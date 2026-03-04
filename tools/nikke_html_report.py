"""
Nikke Union Raid Hard Mode - HTML Report Generator
====================================================
Generates a professional, self-contained HTML report from optimizer output.

Usage:
  python tools/nikke_html_report.py --bosses data/bosses.csv --teams data/teams.csv
  python tools/nikke_html_report.py --output report.html
  python tools/nikke_html_report.py --solver greedy --buffer 5
"""

import argparse
import html
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Import core logic from the optimizer
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent))
from nikke_optimizer import (
    Boss,
    Team,
    apply_buffer,
    compute_score,
    fmt_hp,
    load_bosses,
    load_teams,
    solve_greedy,
    validate_assignment,
)

try:
    from nikke_optimizer import PULP_AVAILABLE, solve_ilp
except ImportError:
    PULP_AVAILABLE = False

    def solve_ilp(*args, **kwargs):
        raise RuntimeError("PuLP not available")


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def pct(dealt: int, hp: int) -> float:
    if hp == 0:
        return 0.0
    return min(dealt / hp * 100, 100.0)


def level_status(dealt: int, hp: int) -> str:
    if dealt >= hp:
        return "cleared"
    if dealt > 0:
        return "partial"
    return "empty"


def damage_color_class(dealt: int, hp: int) -> str:
    p = pct(dealt, hp)
    if p >= 100:
        return "bar-cleared"
    if p >= 75:
        return "bar-high"
    if p >= 40:
        return "bar-mid"
    return "bar-low"


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------


CSS = """
:root {
  --bg:        #0d0f14;
  --surface:   #161b26;
  --surface2:  #1e2535;
  --border:    #2a3348;
  --accent:    #4f8ef7;
  --accent2:   #7b5cf0;
  --cleared:   #22c55e;
  --partial:   #f59e0b;
  --empty:     #ef4444;
  --text:      #e2e8f0;
  --text-dim:  #8898aa;
  --font:      'Segoe UI', system-ui, -apple-system, sans-serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.55;
}

a { color: var(--accent); text-decoration: none; }

/* ---- Layout ---- */
.container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px 16px 64px;
}

/* ---- Header ---- */
.header {
  border-bottom: 1px solid var(--border);
  padding-bottom: 20px;
  margin-bottom: 32px;
}
.header-title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text);
}
.header-subtitle {
  font-size: 13px;
  color: var(--text-dim);
  margin-top: 4px;
}

/* ---- Meta strip ---- */
.meta-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 32px;
}
.meta-pill {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 16px;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.meta-pill .label {
  font-size: 11px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.meta-pill .value {
  font-weight: 600;
  font-size: 15px;
}
.value-score { color: var(--accent); }
.value-ok    { color: var(--cleared); }
.value-warn  { color: var(--partial); }

/* ---- Section ---- */
.section {
  margin-bottom: 40px;
}
.section-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-dim);
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
  margin-bottom: 20px;
}

/* ---- Boss grid ---- */
.boss-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}
.boss-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
}
.boss-card-name {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.badge {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 4px;
}
.badge-cleared { background: rgba(34,197,94,.18); color: var(--cleared); }
.badge-partial { background: rgba(245,158,11,.15); color: var(--partial); }
.badge-empty   { background: rgba(239,68,68,.15);  color: var(--empty);   }

/* ---- Level bars ---- */
.level-row {
  margin-bottom: 12px;
}
.level-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 5px;
}
.level-label .lvl-name { font-weight: 600; color: var(--text-dim); }
.level-label .lvl-stat { color: var(--text); font-variant-numeric: tabular-nums; }

.bar-track {
  height: 8px;
  background: var(--surface2);
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s ease;
}
.bar-cleared { background: var(--cleared); }
.bar-high    { background: #84cc16; }
.bar-mid     { background: var(--partial); }
.bar-low     { background: var(--empty); }

.overflow-line {
  margin-top: 5px;
  font-size: 11px;
  font-weight: 600;
  color: var(--partial);
  letter-spacing: 0.02em;
}

/* ---- Assignment section ---- */
.boss-section {
  margin-bottom: 28px;
}
.boss-section-title {
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.level-block {
  margin-bottom: 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.level-block-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  background: var(--surface2);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.level-block-header .lvl-tag {
  font-weight: 700;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
}
.level-block-header .need-hp {
  color: var(--text-dim);
  font-size: 12px;
}
.level-block-header .status-tag {
  margin-left: auto;
}

.assign-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.assign-table th {
  text-align: left;
  padding: 7px 14px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-dim);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.assign-table td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.assign-table tbody tr:last-child td {
  border-bottom: none;
}
.assign-table tbody tr:hover td {
  background: var(--surface2);
}
.member-name { font-weight: 600; }
.damage-val  { font-variant-numeric: tabular-nums; color: var(--accent); font-weight: 600; }
.units-list  { color: var(--text-dim); font-size: 12px; }
.unit-tag {
  display: inline-block;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
  margin: 1px;
  font-size: 11px;
  white-space: nowrap;
}
.no-teams-row td {
  text-align: center;
  color: var(--text-dim);
  font-style: italic;
  padding: 14px;
}

/* ---- Level summary footer ---- */
.level-footer {
  display: flex;
  align-items: stretch;
  border-top: 1px solid var(--border);
  background: var(--surface2);
  font-size: 12px;
}
.level-footer-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 18px;
  border-right: 1px solid var(--border);
}
.level-footer-stat:last-child {
  border-right: none;
}
.level-footer-stat .stat-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--text-dim);
}
.level-footer-stat .stat-value {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.stat-assigned { color: var(--text); }
.stat-hp       { color: var(--text-dim); }
.stat-wasted   { color: var(--partial); }
.stat-clean    { color: var(--cleared); }

/* ---- Member table ---- */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th {
  text-align: left;
  padding: 8px 14px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-dim);
  background: var(--surface2);
  border-bottom: 1px solid var(--border);
}
.data-table td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover td { background: var(--surface2); }

.hit-bar {
  display: flex;
  gap: 4px;
}
.hit-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  background: var(--border);
}
.hit-dot.used   { background: var(--accent); }
.hit-dot.unused { background: var(--border); }

.boss-tag {
  display: inline-block;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 11px;
  margin: 1px;
  white-space: nowrap;
}
.total-dmg { font-variant-numeric: tabular-nums; color: var(--accent); font-weight: 600; }

/* ---- Warnings ---- */
.warning-box {
  background: rgba(239,68,68,.08);
  border: 1px solid rgba(239,68,68,.3);
  border-radius: 8px;
  padding: 14px 18px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--empty);
}
.info-box {
  background: rgba(245,158,11,.08);
  border: 1px solid rgba(245,158,11,.25);
  border-radius: 8px;
  padding: 14px 18px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--partial);
}
.empty-state {
  color: var(--text-dim);
  font-size: 13px;
  font-style: italic;
}

/* ---- Footer ---- */
.footer {
  margin-top: 48px;
  border-top: 1px solid var(--border);
  padding-top: 16px;
  font-size: 12px;
  color: var(--text-dim);
}
"""


def _h(s: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(s))


def build_html(
    assignments: List[Tuple[Team, int]],
    bosses: Dict[str, Boss],
    score: float,
    breakdown: dict,
    solver_label: str,
    all_teams: List[Team],
    max_hits: int,
    generated_at: str,
) -> str:
    selected_teams = [t for t, _ in assignments]

    # ---- Derived data ----
    by_boss_level: Dict[str, Dict[int, List[Team]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for team, lvl in assignments:
        by_boss_level[team.boss_name][lvl].append(team)

    all_l1 = all(breakdown[b][0][0] >= bosses[b].hp[0] for b in bosses)
    all_l2 = all_l1 and all(breakdown[b][1][0] >= bosses[b].hp[1] for b in bosses)
    all_l3 = all_l2 and all(breakdown[b][2][0] >= bosses[b].hp[2] for b in bosses)

    member_hits: Dict[str, int] = defaultdict(int)
    member_bosses: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    member_total_dmg: Dict[str, int] = defaultdict(int)
    for team, _ in assignments:
        member_hits[team.member] += 1
        member_bosses[team.member][team.boss_name] += 1
        member_total_dmg[team.member] += team.damage

    all_members = sorted(set(t.member for t in all_teams))

    total_assigned = len(assignments)
    total_available = len(all_members) * max_hits

    # Overall raid status label
    if all_l3:
        raid_status = "L3 Cleared"
        raid_class = "value-ok"
    elif all_l2:
        raid_status = "L2 Cleared / L3 in progress"
        raid_class = "value-ok"
    elif all_l1:
        raid_status = "L1 Cleared / L2 in progress"
        raid_class = "value-warn"
    else:
        raid_status = "L1 in progress"
        raid_class = "value-warn"

    # Effective damage: sum of damage actually dealt (capped at each level's HP)
    effective_dmg = sum(
        dealt for lvl_detail in breakdown.values() for dealt, _ in lvl_detail
    )

    # ---- Build sections ----
    meta = _build_meta(
        generated_at,
        solver_label,
        effective_dmg,
        total_assigned,
        total_available,
        len(all_members),
        raid_status,
        raid_class,
    )
    boss_overview = _build_boss_overview(bosses, breakdown, by_boss_level)
    assignments_section = _build_assignments(bosses, by_boss_level, breakdown, max_hits)
    members_section = _build_members(
        all_members, member_hits, member_bosses, member_total_dmg, max_hits
    )
    warnings_section = _build_warnings(
        bosses, by_boss_level, all_teams, selected_teams, member_hits, max_hits
    )

    body = f"""
<div class="container">
  <div class="header">
    <div class="header-title">Nikke Union Raid Hard Mode &mdash; Assignment Report</div>
    <div class="header-subtitle">Optimal hit plan for guild progression</div>
  </div>

  {meta}

  <div class="section">
    <div class="section-title">Boss Progress Overview</div>
    {boss_overview}
  </div>

  <div class="section">
    <div class="section-title">Assignments by Boss &amp; Level</div>
    {assignments_section}
  </div>

  <div class="section">
    <div class="section-title">Member Statistics</div>
    {members_section}
  </div>

  <div class="section">
    <div class="section-title">Notices</div>
    {warnings_section}
  </div>

  <div class="footer">
    Generated {_h(generated_at)} &nbsp;&bull;&nbsp; Nikke Union Raid Planner
  </div>
</div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nikke Union Raid &mdash; Assignment Report</title>
<style>
{CSS}
</style>
</head>
<body>
{body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_meta(
    generated_at,
    solver_label,
    effective_dmg,
    total_assigned,
    total_available,
    num_members,
    raid_status,
    raid_class,
) -> str:
    return f"""
<div class="meta-strip">
  <div class="meta-pill">
    <span class="label">Generated</span>
    <span class="value">{_h(generated_at)}</span>
  </div>
  <div class="meta-pill">
    <span class="label">Solver</span>
    <span class="value">{_h(solver_label)}</span>
  </div>
  <div class="meta-pill">
    <span class="label">Effective Damage</span>
    <span class="value value-score">{_h(fmt_hp(effective_dmg))}</span>
  </div>
  <div class="meta-pill">
    <span class="label">Runs Assigned</span>
    <span class="value">{total_assigned} / {total_available}</span>
  </div>
  <div class="meta-pill">
    <span class="label">Members</span>
    <span class="value">{num_members}</span>
  </div>
  <div class="meta-pill">
    <span class="label">Raid Status</span>
    <span class="value {_h(raid_class)}">{_h(raid_status)}</span>
  </div>
</div>"""


def _build_boss_overview(
    bosses: Dict[str, Boss],
    breakdown: dict,
    by_boss_level: Dict[str, Dict[int, List[Team]]],
) -> str:
    cards = []
    for boss_name, boss in bosses.items():
        lvl_detail = breakdown.get(boss_name, [(0, hp) for hp in boss.hp])

        # Overall card badge
        dealt_l1, hp_l1 = lvl_detail[0]
        if dealt_l1 >= hp_l1:
            badge = '<span class="badge badge-cleared">Cleared L1+</span>'
        elif dealt_l1 > 0:
            badge = '<span class="badge badge-partial">In Progress</span>'
        else:
            badge = '<span class="badge badge-empty">No Damage</span>'

        levels_html = ""
        for lvl_idx, (dealt, hp) in enumerate(lvl_detail):
            lvl_num = lvl_idx + 1
            p = pct(dealt, hp)
            color = damage_color_class(dealt, hp)
            if dealt >= hp:
                stat_text = "CLEARED"
            elif dealt > 0:
                stat_text = f"{fmt_hp(dealt)} / {fmt_hp(hp)} ({p:.0f}%)"
            else:
                stat_text = f"0 / {fmt_hp(hp)}"

            lvl_teams = by_boss_level[boss_name].get(lvl_num, [])
            lvl_total = sum(t.damage for t in lvl_teams)
            wasted = max(0, lvl_total - hp)
            overflow_html = (
                f'<div class="overflow-line">'
                f'Wasted &nbsp;&mdash;&nbsp; {_h(fmt_hp(wasted))}'
                f'</div>'
                if wasted > 0 else ""
            )

            levels_html += f"""
        <div class="level-row">
          <div class="level-label">
            <span class="lvl-name">Level {lvl_num}</span>
            <span class="lvl-stat">{_h(stat_text)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill {_h(color)}" style="width:{p:.2f}%"></div>
          </div>
          {overflow_html}
        </div>"""

        cards.append(f"""
      <div class="boss-card">
        <div class="boss-card-name">
          {_h(boss_name)}
          {badge}
        </div>
        {levels_html}
      </div>""")

    return f'<div class="boss-grid">{"".join(cards)}</div>'


def _build_assignments(
    bosses: Dict[str, Boss],
    by_boss_level: Dict[str, Dict[int, List[Team]]],
    breakdown: dict,
    max_hits: int,
) -> str:
    sections = []
    for boss_name, boss in bosses.items():
        levels_html = ""
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
                status_html = '<span class="badge badge-cleared">Cleared</span>'
            elif dealt > 0:
                status_html = (
                    f'<span class="badge badge-partial">{pct(dealt, hp):.0f}%</span>'
                )
            else:
                status_html = '<span class="badge badge-empty">No Damage</span>'

            rows_html = ""
            footer_html = ""
            if lvl_teams:
                lvl_total = sum(t.damage for t in lvl_teams)
                wasted = max(0, lvl_total - hp_needed)
                for team in lvl_teams:
                    units_html = "".join(
                        f'<span class="unit-tag">{_h(u)}</span>' for u in team.units
                    )
                    rows_html += f"""
              <tr>
                <td class="member-name">{_h(team.member)}</td>
                <td class="damage-val">{team.damage:,}</td>
                <td class="units-list">{units_html}</td>
              </tr>"""

                wasted_stat = (
                    f'<div class="level-footer-stat">'
                    f'<span class="stat-label">Wasted</span>'
                    f'<span class="stat-value stat-wasted">{_h(fmt_hp(wasted))}</span>'
                    f"</div>"
                    if wasted > 0
                    else f'<div class="level-footer-stat">'
                    f'<span class="stat-label">Wasted</span>'
                    f'<span class="stat-value stat-clean">None</span>'
                    f"</div>"
                )
                footer_html = f"""
          <div class="level-footer">
            <div class="level-footer-stat">
              <span class="stat-label">Total Assigned</span>
              <span class="stat-value stat-assigned">{_h(fmt_hp(lvl_total))}</span>
            </div>
            <div class="level-footer-stat">
              <span class="stat-label">Boss HP</span>
              <span class="stat-value stat-hp">{_h(fmt_hp(hp_needed))}</span>
            </div>
            {wasted_stat}
          </div>"""
            else:
                rows_html = """
              <tr class="no-teams-row"><td colspan="3">No runs assigned to this level</td></tr>"""

            levels_html += f"""
          <div class="level-block">
            <div class="level-block-header">
              <span class="lvl-tag">L{lvl}</span>
              <span class="need-hp">HP: {fmt_hp(hp_needed)}</span>
              <span class="status-tag">{status_html}</span>
            </div>
            <table class="assign-table">
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Damage</th>
                  <th>Units</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
            {footer_html}
          </div>"""

        sections.append(f"""
      <div class="boss-section">
        <div class="boss-section-title">{_h(boss_name)}</div>
        {levels_html}
      </div>""")

    return "\n".join(sections)


def _build_members(
    all_members: List[str],
    member_hits: Dict[str, int],
    member_bosses: Dict[str, Dict[str, int]],
    member_total_dmg: Dict[str, int],
    max_hits: int,
) -> str:
    rows = ""
    for member in sorted(all_members):
        hits = member_hits.get(member, 0)
        dots = "".join(
            f'<div class="hit-dot {"used" if i < hits else "unused"}"></div>'
            for i in range(max_hits)
        )
        bosses_targeted = member_bosses.get(member, {})
        boss_tags = (
            "".join(
                f'<span class="boss-tag">{_h(b)}&nbsp;x{n}</span>'
                for b, n in sorted(bosses_targeted.items())
            )
            or '<span style="color:var(--text-dim)">None</span>'
        )
        total = member_total_dmg.get(member, 0)
        total_str = fmt_hp(total) if total > 0 else "&mdash;"

        rows += f"""
      <tr>
        <td class="member-name">{_h(member)}</td>
        <td>
          <div class="hit-bar">{dots}</div>
          <span style="font-size:11px;color:var(--text-dim);margin-top:3px;display:block">
            {hits} / {max_hits}
          </span>
        </td>
        <td>{boss_tags}</td>
        <td class="total-dmg">{total_str}</td>
      </tr>"""

    return f"""
<table class="data-table">
  <thead>
    <tr>
      <th>Member</th>
      <th>Hits Used</th>
      <th>Targets</th>
      <th>Total Damage</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""


def _build_warnings(
    bosses: Dict[str, Boss],
    by_boss_level: Dict[str, Dict[int, List[Team]]],
    all_teams: List[Team],
    selected_teams: List[Team],
    member_hits: Dict[str, int],
    max_hits: int,
) -> str:
    blocks = []

    # Uncovered bosses
    uncovered = [
        b for b in bosses if not any(by_boss_level[b].get(lvl) for lvl in [1, 2, 3])
    ]
    for b in uncovered:
        blocks.append(
            f'<div class="warning-box"><strong>Critical:</strong> '
            f"{_h(b)} has no assigned runs and cannot be cleared.</div>"
        )

    # Members with unused hits
    all_member_names = sorted(set(t.member for t in all_teams))
    underused = [
        (m, member_hits.get(m, 0))
        for m in all_member_names
        if member_hits.get(m, 0) < max_hits
    ]
    if underused:
        rows = "".join(
            f"<tr><td>{_h(m)}</td>"
            f"<td>{hits}/{max_hits} hits used &mdash; {max_hits - hits} remaining</td></tr>"
            for m, hits in underused
        )
        blocks.append(f"""
      <div class="info-box">
        <strong>Members with unused hits:</strong>
        <table class="data-table" style="margin-top:10px">
          <thead><tr><th>Member</th><th>Hit Budget</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>""")

    # Dropped teams (available but not assigned due to hit limit)
    selected_set = set(id(t) for t in selected_teams)
    selected_member_units: Dict[str, set] = defaultdict(set)
    for team in selected_teams:
        for unit in team.units:
            selected_member_units[team.member].add(unit.lower())

    dropped_no_conflict = [
        t
        for t in all_teams
        if id(t) not in selected_set
        and not any(
            u.lower() in selected_member_units.get(t.member, set()) for u in t.units
        )
    ]
    if dropped_no_conflict:
        rows = "".join(
            f"<tr>"
            f"<td>{_h(t.member)}</td>"
            f"<td>{_h(t.boss_name)}</td>"
            f"<td class='damage-val'>{t.damage:,}</td>"
            f"</tr>"
            for t in dropped_no_conflict
        )
        blocks.append(f"""
      <div class="info-box">
        <strong>Unused teams (hit limit reached, no unit conflict):</strong>
        <table class="data-table" style="margin-top:10px">
          <thead><tr><th>Member</th><th>Boss</th><th>Damage</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>""")

    if not blocks:
        blocks.append('<div class="empty-state">No warnings or notices.</div>')

    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a professional HTML report for the Nikke Union Raid optimizer"
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
        default=[1000, 100, 10],
        metavar=("L1", "L2", "L3"),
        help="Score weights for L1/L2/L3 (default: 1000 100 10)",
    )
    parser.add_argument(
        "--time-limit", type=int, default=60, help="ILP solver time limit (s)"
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=0.0,
        metavar="PCT",
        help="Scale all team damages up by PCT%% before optimizing",
    )
    parser.add_argument(
        "--output",
        default="report.html",
        help="Output HTML file path (default: report.html)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    solver = args.solver
    if solver == "auto":
        solver = "ilp" if PULP_AVAILABLE else "greedy"
        if not PULP_AVAILABLE:
            print(
                "[INFO] PuLP not installed - using greedy solver.",
                file=sys.stderr,
            )

    if solver == "ilp" and not PULP_AVAILABLE:
        print(
            "[ERROR] ILP solver requested but PuLP is not installed.", file=sys.stderr
        )
        sys.exit(1)

    weights = tuple(args.weights)

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
        print("[ERROR] No valid team submissions found.", file=sys.stderr)
        sys.exit(1)

    if args.buffer != 0:
        print(f"[INFO] Applying {args.buffer:+.1f}% damage buffer.", file=sys.stderr)
    teams = apply_buffer(raw_teams, args.buffer)

    if solver == "ilp":
        assignments = solve_ilp(teams, bosses, weights, args.max_hits, args.time_limit)
        solver_label = "ILP (PuLP/CBC)"
    else:
        assignments = solve_greedy(teams, bosses, weights, args.max_hits)
        solver_label = "Greedy (phase-based)"

    if args.buffer != 0:
        solver_label += f" +{args.buffer:.1f}% buffer"

    violations = validate_assignment(assignments, args.max_hits)
    if violations:
        print("[ERROR] Optimizer produced an invalid assignment:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        sys.exit(1)

    # Use original (unbuffered) damages for display
    if args.buffer != 0:
        buffered_to_raw = {id(tb): tr for tb, tr in zip(teams, raw_teams)}
        display_assignments = [
            (buffered_to_raw[id(tb)], lvl) for tb, lvl in assignments
        ]
        display_teams = raw_teams
    else:
        display_assignments = assignments
        display_teams = teams

    score, breakdown = compute_score(assignments, bosses, weights)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_content = build_html(
        display_assignments,
        bosses,
        score,
        breakdown,
        solver_label,
        display_teams,
        args.max_hits,
        generated_at,
    )

    out_path = Path(args.output)
    out_path.write_text(html_content, encoding="utf-8")
    print(f"Report saved to: {out_path.resolve()}")


if __name__ == "__main__":
    main()

"""
Nikke Union Raid Hard Mode - Streamlit Frontend
================================================
A simple web UI that wraps nikke_optimizer + nikke_html_report.
"""

import csv
import io
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Path setup — allow importing from tools/
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))

from nikke_html_report import build_html
from nikke_optimizer import (
    PULP_AVAILABLE,
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
    from nikke_optimizer import solve_ilp
except ImportError:
    solve_ilp = None

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Nikke Union Raid Planner",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ELEMENTS = ["fire", "wind", "iron", "electric", "water"]
ELEMENT_EMOJIS = {
    "fire": "🔥 Fire",
    "wind": "💨 Wind",
    "iron": "⚙️ Iron",
    "electric": "⚡ Electric",
    "water": "💧 Water",
}

HP_PRESETS = {
    "Standard (99.8B / 149.7B / 292.4B)": [
        99_856_279_200,
        149_784_418_800,
        292_445_295_750,
    ],
    "Large   (150.8B / 226.2B / 292.4B)": [
        150_841_813_600,
        226_262_720_400,
        292_445_295_750,
    ],
    "Largest (150.8B / 226.2B / 349.2B)": [
        150_841_813_600,
        226_262_720_400,
        349_230_901_500,
    ],
    "Custom": None,
}

TEAMS_TXT_HELP = """\
Paste your **teams.txt** raw export here (tab or 2-space separated).

Expected columns (in order):
```
MEMBER   RANK   ELEMENT   RUN#   DAMAGE   UNIT1   UNIT2   UNIT3   UNIT4   UNIT5   [X-flag]
```

Example lines:
```
POCHI\t766\tIron\t2\t30,500,109,232\tB.Ade\tRRH\tSiren\tRaven\tS.Helm
DAVID\t682\tFire\t1\t36,668,968,562\tRRH\tX.Brid\tHelm\tX.Diesel\tMih:bc
```
The element column is mapped to the matching boss via the **element weakness** field in your boss list.
"""


def split_line(line: str) -> list[str]:
    """Split on tab, falling back to 2+ spaces."""
    if "\t" in line:
        return [p.strip() for p in line.split("\t")]
    return [p.strip() for p in re.split(r" {2,}", line)]


def parse_teams_txt(
    raw_text: str, element_map: dict[str, str]
) -> tuple[list[Team], list[str]]:
    """
    Convert raw teams.txt text into Team objects using element -> boss mapping.
    Returns (teams, warnings).
    """
    teams: list[Team] = []
    warnings: list[str] = []
    seen_exact: dict[tuple, int] = {}

    for line_num, raw in enumerate(raw_text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue

        parts = split_line(line)
        parts = [p for p in parts if p]

        if len(parts) < 10:
            warnings.append(
                f"Line {line_num}: only {len(parts)} columns (need at least 10) — skipped"
            )
            continue

        member = parts[0]
        element = parts[2].strip().lower()
        damage_raw = parts[4].replace(",", "")
        units = parts[5:10]

        try:
            damage = int(damage_raw)
        except ValueError:
            warnings.append(
                f"Line {line_num}: invalid damage value '{parts[4]}' — skipped"
            )
            continue

        boss_name = element_map.get(element)
        if boss_name is None:
            warnings.append(
                f"Line {line_num}: unknown element '{parts[2]}' "
                f"(known: {', '.join(element_map.keys())}) — skipped"
            )
            continue

        if len(units) != 5:
            warnings.append(
                f"Line {line_num}: expected 5 units, got {len(units)} — skipped"
            )
            continue

        exact_key = (
            member.lower(),
            boss_name.lower(),
            frozenset(u.lower() for u in units),
        )
        if exact_key in seen_exact:
            idx = seen_exact[exact_key]
            if damage > teams[idx].damage:
                warnings.append(
                    f"Line {line_num}: duplicate {member}/{boss_name} — keeping higher damage ({damage:,})"
                )
                teams[idx] = Team(member, boss_name, damage, units)
            else:
                warnings.append(
                    f"Line {line_num}: duplicate {member}/{boss_name} — keeping existing ({teams[idx].damage:,})"
                )
        else:
            seen_exact[exact_key] = len(teams)
            teams.append(Team(member, boss_name, damage, units))

    return teams, warnings


def bosses_from_state(boss_rows: list[dict]) -> dict[str, Boss]:
    """Build a Boss dict from the UI boss table."""
    bosses = {}
    for row in boss_rows:
        name = row["name"].strip()
        if not name:
            continue
        bosses[name] = Boss(name, [row["hp1"], row["hp2"], row["hp3"]])
    return bosses


def build_element_map(boss_rows: list[dict]) -> dict[str, str]:
    """element_lowercase -> boss_name"""
    return {
        row["element"].lower(): row["name"] for row in boss_rows if row["name"].strip()
    }


def teams_to_csv_bytes(teams: list[Team]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["member", "boss_name", "damage", "unit1", "unit2", "unit3", "unit4", "unit5"]
    )
    for t in teams:
        writer.writerow([t.member, t.boss_name, t.damage] + t.units)
    return buf.getvalue().encode()


def bosses_to_csv_bytes(boss_rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["boss_name", "hp_level1", "hp_level2", "hp_level3", "element_weakness"]
    )
    for row in boss_rows:
        if row["name"].strip():
            writer.writerow(
                [row["name"], row["hp1"], row["hp2"], row["hp3"], row["element"]]
            )
    return buf.getvalue().encode()


def pct_str(dealt: int, hp: int) -> str:
    if hp == 0:
        return "0%"
    return f"{min(dealt / hp * 100, 100.0):.1f}%"


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
def _init_state():
    if "boss_rows" not in st.session_state:
        st.session_state.boss_rows = [
            {
                "name": "Porter",
                "hp1": 99_856_279_200,
                "hp2": 149_784_418_800,
                "hp3": 292_445_295_750,
                "element": "iron",
            },
            {
                "name": "Plate",
                "hp1": 99_856_279_200,
                "hp2": 149_784_418_800,
                "hp3": 292_445_295_750,
                "element": "water",
            },
            {
                "name": "Land Eater",
                "hp1": 150_841_813_600,
                "hp2": 226_262_720_400,
                "hp3": 292_445_295_750,
                "element": "electric",
            },
            {
                "name": "Rebuild Fingers",
                "hp1": 99_856_279_200,
                "hp2": 149_784_418_800,
                "hp3": 292_445_295_750,
                "element": "fire",
            },
            {
                "name": "Material H",
                "hp1": 150_841_813_600,
                "hp2": 226_262_720_400,
                "hp3": 349_230_901_500,
                "element": "wind",
            },
        ]
    if "teams" not in st.session_state:
        st.session_state.teams = []
    if "manual_rows" not in st.session_state:
        st.session_state.manual_rows = []
    if "run_result" not in st.session_state:
        st.session_state.run_result = None


_init_state()

# ===========================================================================
# SIDEBAR
# ===========================================================================
with st.sidebar:
    st.title("Nikke Union Raid Planner")
    st.caption("Union Raid Hard Mode — Hit Assignment Optimizer")
    st.divider()

    st.subheader("Solver Settings")

    solver_options = ["auto", "greedy"]
    if PULP_AVAILABLE:
        solver_options = ["auto", "ilp", "greedy"]

    solver = st.selectbox(
        "Solver",
        solver_options,
        index=0,
        help="ILP is globally optimal but requires `pulp`. Greedy is fast and near-optimal.",
    )
    if not PULP_AVAILABLE:
        st.caption("Install `pulp` to unlock the ILP solver.")

    max_hits = st.number_input(
        "Max hits per member", min_value=1, max_value=10, value=3, step=1
    )

    buffer_pct = st.slider(
        "Damage buffer %",
        min_value=0.0,
        max_value=30.0,
        value=0.0,
        step=0.5,
        help=(
            "Scale all damages up by this % before optimizing. "
            "Useful when members typically score higher in the real fight than in mocks."
        ),
    )

    if PULP_AVAILABLE:
        time_limit = st.number_input(
            "ILP time limit (s)", min_value=10, max_value=600, value=120, step=10
        )
    else:
        time_limit = 120

    st.divider()
    st.subheader("Score Weights")
    st.caption("Relative importance of each level's progress in the objective.")
    w1 = st.number_input("L1 weight", value=1000, min_value=1)
    w2 = st.number_input("L2 weight", value=100, min_value=1)
    w3 = st.number_input("L3 weight", value=10, min_value=1)

    st.divider()
    st.caption("Made by Davidd")

# ===========================================================================
# MAIN TABS
# ===========================================================================
tab_bosses, tab_teams, tab_run = st.tabs(["Bosses", "Teams", "Run Optimizer"])

# ---------------------------------------------------------------------------
# TAB 1 — BOSSES
# ---------------------------------------------------------------------------
with tab_bosses:
    st.header("Boss Configuration")
    st.write(
        "Configure the five raid bosses. Each boss must have a **unique element weakness** "
        "so the teams.txt element column can be mapped correctly."
    )

    col_import, col_reset = st.columns([3, 1])

    with col_import:
        uploaded_bosses = st.file_uploader(
            "Import bosses.csv",
            type=["csv"],
            key="upload_bosses",
            help="Upload a bosses.csv to populate the table below.",
        )
        if uploaded_bosses is not None:
            try:
                content = uploaded_bosses.read().decode("utf-8")
                reader = csv.DictReader(io.StringIO(content))
                new_rows = []
                for row in reader:
                    new_rows.append(
                        {
                            "name": row["boss_name"].strip(),
                            "hp1": int(row["hp_level1"].replace(",", "")),
                            "hp2": int(row["hp_level2"].replace(",", "")),
                            "hp3": int(row["hp_level3"].replace(",", "")),
                            "element": row.get("element_weakness", "fire")
                            .strip()
                            .lower(),
                        }
                    )
                st.session_state.boss_rows = new_rows
                st.success(f"Loaded {len(new_rows)} bosses from CSV.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to parse bosses.csv: {e}")

    with col_reset:
        st.write("")
        st.write("")
        if st.button("Reset to defaults", use_container_width=True):
            st.session_state.boss_rows = [
                {
                    "name": "Porter",
                    "hp1": 99_856_279_200,
                    "hp2": 149_784_418_800,
                    "hp3": 292_445_295_750,
                    "element": "iron",
                },
                {
                    "name": "Plate",
                    "hp1": 99_856_279_200,
                    "hp2": 149_784_418_800,
                    "hp3": 292_445_295_750,
                    "element": "water",
                },
                {
                    "name": "Land Eater",
                    "hp1": 150_841_813_600,
                    "hp2": 226_262_720_400,
                    "hp3": 292_445_295_750,
                    "element": "electric",
                },
                {
                    "name": "Rebuild Fingers",
                    "hp1": 99_856_279_200,
                    "hp2": 149_784_418_800,
                    "hp3": 292_445_295_750,
                    "element": "fire",
                },
                {
                    "name": "Material H",
                    "hp1": 150_841_813_600,
                    "hp2": 226_262_720_400,
                    "hp3": 349_230_901_500,
                    "element": "wind",
                },
            ]
            st.rerun()

    st.divider()
    st.caption(
        "Columns: **Name** | **Element Weakness** | **HP Preset** | **HP L1** | **HP L2** | **HP L3**\n\n"
        "Tick **Cleared** to mark a level as done (sets HP to 0) — the optimizer will skip it."
    )

    # Render one row per boss
    updated_rows = []
    for i, row in enumerate(st.session_state.boss_rows):
        st.markdown(f"**Boss {i + 1}**")
        c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 2, 2, 2, 2])

        with c1:
            name = st.text_input(
                "Name",
                value=row["name"],
                key=f"bname_{i}",
                label_visibility="collapsed",
                placeholder="Boss name",
            )
        with c2:
            element = st.selectbox(
                "Element",
                ELEMENTS,
                index=ELEMENTS.index(row["element"])
                if row["element"] in ELEMENTS
                else 0,
                key=f"belem_{i}",
                label_visibility="collapsed",
                format_func=lambda e: ELEMENT_EMOJIS.get(e, e.capitalize()),
            )
        with c3:
            preset_names = list(HP_PRESETS.keys())
            current_hp = [row["hp1"], row["hp2"], row["hp3"]]
            current_preset = "Custom"
            for pname, php in HP_PRESETS.items():
                if php == current_hp:
                    current_preset = pname
                    break
            preset = st.selectbox(
                "HP Preset",
                preset_names,
                index=preset_names.index(current_preset),
                key=f"bpreset_{i}",
                label_visibility="collapsed",
            )

        if HP_PRESETS[preset] is not None:
            base_hp1, base_hp2, base_hp3 = HP_PRESETS[preset]
        else:
            base_hp1, base_hp2, base_hp3 = row["hp1"], row["hp2"], row["hp3"]

        with c4:
            cleared_l1 = st.checkbox(
                "L1 Cleared",
                value=row.get("cleared_l1", False),
                key=f"bclr1_{i}",
            )
            hp1 = st.number_input(
                "HP L1",
                value=0 if cleared_l1 else base_hp1,
                min_value=0,
                step=1_000_000_000,
                key=f"bhp1_{i}",
                label_visibility="collapsed",
                disabled=(HP_PRESETS[preset] is not None) or cleared_l1,
                format="%d",
            )
            if cleared_l1:
                hp1 = 0
        with c5:
            cleared_l2 = st.checkbox(
                "L2 Cleared",
                value=row.get("cleared_l2", False),
                key=f"bclr2_{i}",
            )
            hp2 = st.number_input(
                "HP L2",
                value=0 if cleared_l2 else base_hp2,
                min_value=0,
                step=1_000_000_000,
                key=f"bhp2_{i}",
                label_visibility="collapsed",
                disabled=(HP_PRESETS[preset] is not None) or cleared_l2,
                format="%d",
            )
            if cleared_l2:
                hp2 = 0
        with c6:
            cleared_l3 = st.checkbox(
                "L3 Cleared",
                value=row.get("cleared_l3", False),
                key=f"bclr3_{i}",
            )
            hp3 = st.number_input(
                "HP L3",
                value=0 if cleared_l3 else base_hp3,
                min_value=0,
                step=1_000_000_000,
                key=f"bhp3_{i}",
                label_visibility="collapsed",
                disabled=(HP_PRESETS[preset] is not None) or cleared_l3,
                format="%d",
            )
            if cleared_l3:
                hp3 = 0

        updated_rows.append(
            {
                "name": name,
                "element": element,
                "hp1": hp1,
                "hp2": hp2,
                "hp3": hp3,
                "cleared_l1": cleared_l1,
                "cleared_l2": cleared_l2,
                "cleared_l3": cleared_l3,
            }
        )

    st.session_state.boss_rows = updated_rows

    # Validation: duplicate elements
    elements_used = [r["element"] for r in updated_rows if r["name"].strip()]
    if len(elements_used) != len(set(elements_used)):
        dupes = [e for e in set(elements_used) if elements_used.count(e) > 1]
        st.warning(
            f"Duplicate element(s) detected: {', '.join(dupes)}. "
            "Each boss must have a unique element weakness."
        )

    st.divider()

    col_dl, _ = st.columns([1, 3])
    with col_dl:
        st.download_button(
            "Download bosses.csv",
            data=bosses_to_csv_bytes(st.session_state.boss_rows),
            file_name="bosses.csv",
            mime="text/csv",
        )

# ---------------------------------------------------------------------------
# TAB 2 — TEAMS
# ---------------------------------------------------------------------------
with tab_teams:
    st.header("Team Data")

    input_mode = st.radio(
        "Input method",
        ["Paste teams.txt (raw)", "Upload teams.csv", "Manual Entry"],
        horizontal=True,
        key="teams_input_mode",
    )

    if input_mode == "Paste teams.txt (raw)":
        st.info(TEAMS_TXT_HELP)

        raw_txt = st.text_area(
            "teams.txt content",
            height=320,
            placeholder="POCHI\t766\tIron\t2\t30,500,109,232\tB.Ade\tRRH\tSiren\tRaven\tS.Helm\n...",
            key="teams_raw_input",
        )

        if st.button("Parse teams.txt", type="primary", use_container_width=True):
            boss_rows = st.session_state.boss_rows
            element_map = build_element_map(boss_rows)
            if not element_map:
                st.error("No bosses configured. Set up bosses in the Bosses tab first.")
            elif not raw_txt.strip():
                st.warning("Nothing to parse — paste your teams.txt content above.")
            else:
                parsed, warnings = parse_teams_txt(raw_txt, element_map)
                st.session_state.teams = parsed
                if warnings:
                    with st.expander(
                        f"{len(warnings)} warning(s) during parse", expanded=False
                    ):
                        for w in warnings:
                            st.caption(f"  {w}")
                if parsed:
                    st.success(
                        f"Parsed {len(parsed)} valid team entries "
                        f"from {len(set(t.member for t in parsed))} members."
                    )
                else:
                    st.error(
                        "No valid entries found. "
                        "Check your input and the boss element mapping."
                    )

    elif input_mode == "Upload teams.csv":
        uploaded_teams = st.file_uploader(
            "Upload teams.csv",
            type=["csv"],
            key="upload_teams",
        )
        if uploaded_teams is not None:
            try:
                content = uploaded_teams.read().decode("utf-8")

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".csv", delete=False, encoding="utf-8"
                ) as tmp_t:
                    tmp_t.write(content)
                    tmp_path = tmp_t.name

                bosses = bosses_from_state(st.session_state.boss_rows)
                if not bosses:
                    st.error(
                        "No bosses configured. Set up bosses in the Bosses tab first."
                    )
                else:
                    import io as _io

                    old_stderr = sys.stderr
                    sys.stderr = _io.StringIO()
                    loaded = load_teams(tmp_path, bosses)
                    captured = sys.stderr.getvalue()
                    sys.stderr = old_stderr

                    st.session_state.teams = loaded
                    Path(tmp_path).unlink(missing_ok=True)

                    if captured.strip():
                        with st.expander("Parse warnings", expanded=False):
                            for line in captured.strip().splitlines():
                                st.caption(line)
                    if loaded:
                        st.success(
                            f"Loaded {len(loaded)} team entries "
                            f"from {len(set(t.member for t in loaded))} members."
                        )
                    else:
                        st.error("No valid team entries found in the uploaded CSV.")
            except Exception as e:
                st.error(f"Failed to load teams.csv: {e}")

    else:  # Manual Entry
        boss_names = [
            r["name"] for r in st.session_state.boss_rows if r["name"].strip()
        ]

        if not boss_names:
            st.warning("No bosses configured. Set up bosses in the Bosses tab first.")
        else:
            # Auto-populate from existing teams on first visit
            if not st.session_state.manual_rows and st.session_state.teams:
                st.session_state.manual_rows = [
                    {
                        "member": t.member,
                        "boss_name": t.boss_name,
                        "damage": str(t.damage),
                        "unit1": t.units[0] if len(t.units) > 0 else "",
                        "unit2": t.units[1] if len(t.units) > 1 else "",
                        "unit3": t.units[2] if len(t.units) > 2 else "",
                        "unit4": t.units[3] if len(t.units) > 3 else "",
                        "unit5": t.units[4] if len(t.units) > 4 else "",
                    }
                    for t in st.session_state.teams
                ]

            # Header row
            h0, h1, h2, h3, h4, h5, h6, h7, h8 = st.columns(
                [2, 2, 2, 1, 1, 1, 1, 1, 0.4]
            )
            h0.markdown("**Member**")
            h1.markdown("**Boss**")
            h2.markdown("**Damage**")
            h3.markdown("**Unit 1**")
            h4.markdown("**Unit 2**")
            h5.markdown("**Unit 3**")
            h6.markdown("**Unit 4**")
            h7.markdown("**Unit 5**")

            delete_idx = None
            updated_rows = []

            for i, row in enumerate(st.session_state.manual_rows):
                c0, c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(
                    [2, 2, 2, 1, 1, 1, 1, 1, 0.4]
                )
                member = c0.text_input(
                    "Member",
                    value=row["member"],
                    key=f"me_member_{i}",
                    label_visibility="collapsed",
                    placeholder="Member name",
                )
                boss_idx = (
                    boss_names.index(row["boss_name"])
                    if row["boss_name"] in boss_names
                    else 0
                )
                boss_name = c1.selectbox(
                    "Boss",
                    boss_names,
                    index=boss_idx,
                    key=f"me_boss_{i}",
                    label_visibility="collapsed",
                )
                damage_str = c2.text_input(
                    "Damage",
                    value=row["damage"],
                    key=f"me_damage_{i}",
                    label_visibility="collapsed",
                    placeholder="30000000000",
                )
                unit1 = c3.text_input(
                    "U1",
                    value=row["unit1"],
                    key=f"me_u1_{i}",
                    label_visibility="collapsed",
                    placeholder="Unit 1",
                )
                unit2 = c4.text_input(
                    "U2",
                    value=row["unit2"],
                    key=f"me_u2_{i}",
                    label_visibility="collapsed",
                    placeholder="Unit 2",
                )
                unit3 = c5.text_input(
                    "U3",
                    value=row["unit3"],
                    key=f"me_u3_{i}",
                    label_visibility="collapsed",
                    placeholder="Unit 3",
                )
                unit4 = c6.text_input(
                    "U4",
                    value=row["unit4"],
                    key=f"me_u4_{i}",
                    label_visibility="collapsed",
                    placeholder="Unit 4",
                )
                unit5 = c7.text_input(
                    "U5",
                    value=row["unit5"],
                    key=f"me_u5_{i}",
                    label_visibility="collapsed",
                    placeholder="Unit 5",
                )
                if c8.button("✕", key=f"me_del_{i}", help="Delete this row"):
                    delete_idx = i

                updated_rows.append(
                    {
                        "member": member,
                        "boss_name": boss_name,
                        "damage": damage_str,
                        "unit1": unit1,
                        "unit2": unit2,
                        "unit3": unit3,
                        "unit4": unit4,
                        "unit5": unit5,
                    }
                )

            if delete_idx is not None:
                st.session_state.manual_rows.pop(delete_idx)
                st.rerun()
            else:
                st.session_state.manual_rows = updated_rows

            if st.button("+ Add Team", use_container_width=True):
                st.session_state.manual_rows.append(
                    {
                        "member": "",
                        "boss_name": boss_names[0],
                        "damage": "",
                        "unit1": "",
                        "unit2": "",
                        "unit3": "",
                        "unit4": "",
                        "unit5": "",
                    }
                )
                st.rerun()

            # Parse and sync to teams
            parse_errors = []
            new_teams = []
            for i, row in enumerate(st.session_state.manual_rows):
                if not row["member"].strip():
                    continue
                try:
                    dmg = int(row["damage"].replace(",", "").replace(".", ""))
                except ValueError:
                    parse_errors.append(
                        f"Row {i + 1} ({row['member']}): invalid damage '{row['damage']}'"
                    )
                    continue
                units = [row[f"unit{j}"] for j in range(1, 6)]
                new_teams.append(
                    Team(row["member"].strip(), row["boss_name"], dmg, units)
                )

            st.session_state.teams = new_teams
            if parse_errors:
                for e in parse_errors:
                    st.warning(e)

    # Preview loaded teams
    if st.session_state.teams:
        st.divider()
        st.subheader(f"Loaded Teams — {len(st.session_state.teams)} entries")

        members = sorted(set(t.member for t in st.session_state.teams))
        bosses_present = sorted(set(t.boss_name for t in st.session_state.teams))

        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("Members", len(members))
        fc2.metric("Boss targets", len(bosses_present))
        fc3.metric("Total entries", len(st.session_state.teams))

        filter_member = st.selectbox(
            "Filter by member",
            ["All"] + members,
            key="team_preview_filter",
        )

        preview_teams = st.session_state.teams
        if filter_member != "All":
            preview_teams = [t for t in preview_teams if t.member == filter_member]

        table_data = [
            {
                "Member": t.member,
                "Boss": t.boss_name,
                "Damage": f"{t.damage:,}",
                "Units": " / ".join(t.units),
            }
            for t in preview_teams
        ]
        st.dataframe(table_data, use_container_width=True, height=300, hide_index=True)

        col_dl2, _ = st.columns([1, 3])
        with col_dl2:
            st.download_button(
                "Download teams.csv",
                data=teams_to_csv_bytes(st.session_state.teams),
                file_name="teams.csv",
                mime="text/csv",
            )

# ---------------------------------------------------------------------------
# TAB 3 — RUN
# ---------------------------------------------------------------------------
with tab_run:
    st.header("Run Optimizer")

    ready_bosses = bool(
        st.session_state.boss_rows
        and any(r["name"].strip() for r in st.session_state.boss_rows)
    )
    ready_teams = bool(st.session_state.teams)

    if not ready_bosses:
        st.warning("No bosses configured. Complete the Bosses tab first.")
    elif not ready_teams:
        st.warning("No teams loaded. Complete the Teams tab first.")
    else:
        bosses = bosses_from_state(st.session_state.boss_rows)
        n_members = len(set(t.member for t in st.session_state.teams))
        n_teams = len(st.session_state.teams)

        st.write(
            f"**{n_members}** members  |  **{n_teams}** team entries  |  "
            f"**{len(bosses)}** bosses configured"
        )

        run_col, _ = st.columns([1, 3])
        with run_col:
            run_pressed = st.button(
                "Run Optimizer", type="primary", use_container_width=True
            )

        if run_pressed:
            with st.spinner("Optimizing assignment plan..."):
                try:
                    actual_solver = solver
                    if actual_solver == "auto":
                        actual_solver = "ilp" if PULP_AVAILABLE else "greedy"

                    weights = (int(w1), int(w2), int(w3))
                    raw_teams = st.session_state.teams
                    teams_buffered = apply_buffer(raw_teams, buffer_pct)

                    if (
                        actual_solver == "ilp"
                        and PULP_AVAILABLE
                        and solve_ilp is not None
                    ):
                        assignments = solve_ilp(
                            teams_buffered,
                            bosses,
                            weights,
                            int(max_hits),
                            int(time_limit),
                        )
                        solver_label = "ILP (PuLP/CBC)"
                    else:
                        assignments = solve_greedy(
                            teams_buffered, bosses, weights, int(max_hits)
                        )
                        solver_label = "Greedy (phase-based)"

                    if buffer_pct != 0:
                        solver_label += f" +{buffer_pct:.1f}% buffer"

                    violations = validate_assignment(assignments, int(max_hits))

                    if buffer_pct != 0:
                        buffered_to_raw = {
                            id(tb): tr for tb, tr in zip(teams_buffered, raw_teams)
                        }
                        display_assignments = [
                            (buffered_to_raw[id(tb)], lvl) for tb, lvl in assignments
                        ]
                        display_teams = raw_teams
                    else:
                        display_assignments = assignments
                        display_teams = raw_teams

                    score, breakdown = compute_score(assignments, bosses, weights)

                    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                    html_content = build_html(
                        display_assignments,
                        bosses,
                        score,
                        breakdown,
                        solver_label,
                        display_teams,
                        int(max_hits),
                        generated_at,
                    )

                    st.session_state.run_result = {
                        "assignments": display_assignments,
                        "bosses": bosses,
                        "breakdown": breakdown,
                        "score": score,
                        "solver_label": solver_label,
                        "violations": violations,
                        "html": html_content,
                        "all_teams": display_teams,
                        "max_hits": int(max_hits),
                    }
                except Exception as e:
                    st.error(f"Optimizer failed: {e}")
                    import traceback

                    st.code(traceback.format_exc())

        # ---- Display results ----
        result = st.session_state.run_result
        if result:
            assignments = result["assignments"]
            bosses = result["bosses"]
            breakdown = result["breakdown"]
            score = result["score"]
            violations = result["violations"]
            all_teams = result["all_teams"]
            max_hits_r = result["max_hits"]

            st.divider()

            # Violations banner
            if violations:
                with st.expander(
                    f"{len(violations)} constraint violation(s) detected — plan may be invalid",
                    expanded=True,
                ):
                    for v in violations:
                        st.error(v)
            else:
                st.success("All constraints satisfied. Plan is valid.")

            # --- Summary metrics ---
            all_l1 = all(breakdown[b][0][0] >= bosses[b].hp[0] for b in bosses)
            all_l2 = all_l1 and all(
                breakdown[b][1][0] >= bosses[b].hp[1] for b in bosses
            )
            all_l3 = all_l2 and all(
                breakdown[b][2][0] >= bosses[b].hp[2] for b in bosses
            )

            if all_l3:
                raid_status = "L3 Fully Cleared"
            elif all_l2:
                raid_status = "L1 + L2 Cleared — L3 in progress"
            elif all_l1:
                raid_status = "L1 Cleared — L2 in progress"
            else:
                raid_status = "L1 in progress"

            effective_dmg = sum(
                dealt for lvl_detail in breakdown.values() for dealt, _ in lvl_detail
            )
            member_hits_count: dict[str, int] = defaultdict(int)
            for team, _ in assignments:
                member_hits_count[team.member] += 1
            total_available = len(set(t.member for t in all_teams)) * max_hits_r

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Raid Status", raid_status)
            mc2.metric("Effective Damage", fmt_hp(effective_dmg))
            mc3.metric("Runs Assigned", f"{len(assignments)} / {total_available}")
            mc4.metric("Solver", result["solver_label"])

            # --- Boss progress ---
            st.subheader("Boss Progress")
            boss_cols = st.columns(len(bosses))
            for col, (boss_name, boss) in zip(boss_cols, bosses.items()):
                lvl_detail = breakdown.get(boss_name, [(0, hp) for hp in boss.hp])
                with col:
                    st.markdown(f"**{boss_name}**")
                    for lvl_idx, (dealt, hp) in enumerate(lvl_detail):
                        label = f"L{lvl_idx + 1}"
                        p = min(dealt / hp * 100, 100) if hp else 0
                        if dealt >= hp:
                            st.progress(1.0, text=f"{label}: Cleared")
                        elif dealt > 0:
                            st.progress(
                                p / 100,
                                text=f"{label}: {pct_str(dealt, hp)} ({fmt_hp(dealt)} / {fmt_hp(hp)})",
                            )
                        else:
                            st.progress(0.0, text=f"{label}: 0%")

            # --- Assignments by boss & level ---
            st.subheader("Assignments by Boss & Level")

            by_boss_level: dict[str, dict[int, list]] = defaultdict(
                lambda: defaultdict(list)
            )
            for team, lvl in assignments:
                by_boss_level[team.boss_name][lvl].append(team)

            for boss_name, boss in bosses.items():
                with st.expander(boss_name, expanded=True):
                    for lvl in [1, 2, 3]:
                        hp_needed = boss.hp[lvl - 1]
                        lvl_teams = sorted(
                            by_boss_level[boss_name].get(lvl, []),
                            key=lambda t: t.damage,
                            reverse=True,
                        )
                        dealt, hp = breakdown.get(boss_name, [(0, h) for h in boss.hp])[
                            lvl - 1
                        ]

                        if dealt >= hp:
                            status = "Cleared"
                        elif dealt > 0:
                            status = f"{pct_str(dealt, hp)}"
                        else:
                            status = "No damage"

                        st.markdown(
                            f"**L{lvl}** — HP: `{fmt_hp(hp_needed)}` — {status}"
                        )
                        if lvl_teams:
                            lvl_data = [
                                {
                                    "Member": t.member,
                                    "Damage": f"{t.damage:,}",
                                    "Units": " / ".join(t.units),
                                }
                                for t in lvl_teams
                            ]
                            lvl_total = sum(t.damage for t in lvl_teams)
                            wasted = max(0, lvl_total - hp_needed)
                            st.dataframe(
                                lvl_data, use_container_width=True, hide_index=True
                            )
                            sub1, sub2 = st.columns(2)
                            sub1.caption(f"Total assigned: {fmt_hp(lvl_total)}")
                            if wasted > 0:
                                sub2.caption(f"Wasted: {fmt_hp(wasted)}")
                            else:
                                sub2.caption("No wasted damage")
                        else:
                            st.caption("No runs assigned to this level.")

            # --- Member summary ---
            st.subheader("Member Summary")
            all_member_names = sorted(set(t.member for t in all_teams))
            member_boss_map: dict[str, dict[str, int]] = defaultdict(
                lambda: defaultdict(int)
            )
            member_dmg_map: dict[str, int] = defaultdict(int)
            for team, _ in assignments:
                member_boss_map[team.member][team.boss_name] += 1
                member_dmg_map[team.member] += team.damage

            member_table = []
            for m in all_member_names:
                hits = member_hits_count.get(m, 0)
                remaining = max_hits_r - hits
                targets = (
                    ", ".join(
                        f"{b} x{n}" for b, n in sorted(member_boss_map[m].items())
                    )
                    or "—"
                )
                total = fmt_hp(member_dmg_map[m]) if member_dmg_map[m] else "—"
                member_table.append(
                    {
                        "Member": m,
                        "Hits Used": f"{hits} / {max_hits_r}",
                        "Remaining": remaining,
                        "Targets": targets,
                        "Total Damage": total,
                    }
                )
            st.dataframe(member_table, use_container_width=True, hide_index=True)

            # --- Members with unused hits ---
            underused = [
                (m, member_hits_count.get(m, 0))
                for m in all_member_names
                if member_hits_count.get(m, 0) < max_hits_r
            ]
            if underused:
                with st.expander(
                    f"{len(underused)} member(s) with unused hits", expanded=False
                ):
                    for m, h in underused:
                        st.caption(
                            f"  {m}: {h}/{max_hits_r} hits used "
                            f"({max_hits_r - h} remaining)"
                        )

            # --- Download HTML report ---
            st.divider()
            st.download_button(
                "Download Full HTML Report",
                data=result["html"].encode("utf-8"),
                file_name=f"nikke_raid_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                mime="text/html",
                type="primary",
                use_container_width=True,
            )

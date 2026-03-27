"""Reads Layout_Productivity_Clean.xlsx — all 4 sheets."""

import pandas as pd
from config import (
    TEMPLATE_FILE,
    SHEET_LAYOUT_MAPPING,
    SHEET_VOLUMETRIC_PCT,
    SHEET_LAYOUT_PRODUCTIVITY,
    SHEET_ACTIVITY_PRODUCTIVITY,
)


def read_layout_mapping(path=None):
    """Sheet 1: layout_name → Layout Type + DC.  Returns DataFrame."""
    path = path or TEMPLATE_FILE
    df = pd.read_excel(path, sheet_name=SHEET_LAYOUT_MAPPING, header=1)
    # Drop unnamed leading column if present
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df = df.dropna(subset=["layout_name"])
    df["layout_name"] = df["layout_name"].str.strip()
    df["Layout Type"] = df["Layout Type"].str.strip()
    df["DC"] = df["DC"].str.strip()
    return df


def read_volumetric_pct(path=None):
    """Sheet 2: DC → Forward %, Reverse %.  Returns DataFrame."""
    path = path or TEMPLATE_FILE
    df = pd.read_excel(path, sheet_name=SHEET_VOLUMETRIC_PCT, header=1)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df = df.dropna(subset=["DC"])
    df["DC"] = df["DC"].str.strip()
    return df


def read_layout_productivity(path=None):
    """Sheet 3: Layout Type → activity load factors + total manhours/ship.
    Returns (layout_prod_df, activity_names, manhours_per_ship_dict).
    """
    path = path or TEMPLATE_FILE
    df = pd.read_excel(path, sheet_name=SHEET_LAYOUT_PRODUCTIVITY, header=0)

    # Row 0 is 'Relative Effort →' — store and drop
    effort_row = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)

    total_col = [c for c in df.columns if "Total" in str(c) and "Manhours" in str(c)]
    total_col_name = total_col[0] if total_col else df.columns[-1]

    # Build manhours/ship dict: Layout Type → float
    manhours_per_ship = dict(
        zip(df["Layout Type"].str.strip(), pd.to_numeric(df[total_col_name], errors="coerce"))
    )

    # Activity names (all columns except Layout Type and Total)
    activity_names = [
        c for c in df.columns if c != "Layout Type" and c != total_col_name
    ]

    # Clean layout type names
    df["Layout Type"] = df["Layout Type"].str.strip()

    # Build effort-per-activity dict from header row (activity → manhours)
    activity_manhours = {}
    for act in activity_names:
        val = effort_row[act]
        activity_manhours[act] = float(val) if pd.notna(val) else 0.0

    return df, activity_names, manhours_per_ship, activity_manhours


def read_activity_productivity(path=None):
    """Sheet 4: Activity-level reference — manhours, time offsets.
    Returns DataFrame with columns: Activity, Ops Type, Manhours per activity, Minutes, Relative.
    """
    path = path or TEMPLATE_FILE
    df = pd.read_excel(path, sheet_name=SHEET_ACTIVITY_PRODUCTIVITY, header=0)
    df["Activity"] = df["Activity"].str.strip()
    return df


def read_xd_list(path=None):
    """XD List sheet: Site → activity effort multipliers for cross-dock sites.

    Returns:
        xd_sites: set of DC names that are XD sites
        xd_multipliers: dict DC → {activity_name: multiplier}
    """
    path = path or TEMPLATE_FILE
    try:
        df = pd.read_excel(path, sheet_name="XD List", header=1)
    except (ValueError, KeyError):
        return set(), {}

    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    df = df.dropna(subset=["Site"])
    df["Site"] = df["Site"].str.strip()

    xd_sites = set(df["Site"].tolist())
    xd_multipliers = {}
    activity_cols = [c for c in df.columns if c not in ("Type", "Site")]

    for _, row in df.iterrows():
        site = row["Site"]
        mults = {}
        for act in activity_cols:
            val = row.get(act, 1.0)
            if pd.notna(val):
                mults[act] = float(val)
            else:
                mults[act] = 1.0
        xd_multipliers[site] = mults

    return xd_sites, xd_multipliers


def load_all_template(path=None):
    """Load everything from the template file.  Returns a dict of all pieces."""
    path = path or TEMPLATE_FILE
    layout_mapping = read_layout_mapping(path)
    volumetric_pct = read_volumetric_pct(path)
    layout_prod_df, activity_names, manhours_per_ship, activity_manhours = read_layout_productivity(path)
    activity_prod = read_activity_productivity(path)
    xd_sites, xd_multipliers = read_xd_list(path)

    # Normalise Layout Type names in mapping to match productivity sheet
    # Build case-insensitive lookup from productivity
    prod_types_lower = {k.lower(): k for k in manhours_per_ship}
    layout_mapping["Layout Type"] = layout_mapping["Layout Type"].apply(
        lambda x: prod_types_lower.get(x.lower(), x) if isinstance(x, str) else x
    )

    return {
        "layout_mapping": layout_mapping,
        "volumetric_pct": volumetric_pct,
        "layout_prod_df": layout_prod_df,
        "activity_names": activity_names,
        "manhours_per_ship": manhours_per_ship,
        "activity_manhours": activity_manhours,
        "activity_prod": activity_prod,
        "xd_sites": xd_sites,
        "xd_multipliers": xd_multipliers,
    }

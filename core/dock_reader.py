"""Read dock load data (IB, OB, Cross-Dock) from Excel.

The dock file has DC-level (not layout-level) hourly volumes.
Columns: DC, date, time (hour), process, awb_count

Processes: IB Regular, IB Cross-Dock, IB Cross-Dock Bag Sorter, OB
"""

import pandas as pd
import numpy as np
from datetime import datetime, date as date_type
from config import DAILY_LOADS_DIR


def read_dock_file(date_start=None, date_end=None):
    """Read dock load data from the parent folder."""
    import glob
    pattern = str(DAILY_LOADS_DIR / "Location_wise_Layout_data_Dock*")
    files = glob.glob(pattern)
    if not files:
        return pd.DataFrame(columns=["DC", "Date of created", "hour", "process", "volume"])

    frames = []
    for f in files:
        if "~$" in f:
            continue
        df = pd.read_excel(f)
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["DC", "Date of created", "hour", "process", "volume"])

    df = pd.concat(frames, ignore_index=True)

    # Standardize column names
    col_map = {}
    for c in df.columns:
        cl = str(c).lower().strip()
        if cl == "dc":
            col_map[c] = "DC"
        elif cl in ("date", "date of created"):
            col_map[c] = "Date of created"
        elif cl in ("time", "hour"):
            col_map[c] = "hour"
        elif cl == "process":
            col_map[c] = "process"
        elif cl in ("awb_count", "total awb_number", "volume"):
            col_map[c] = "volume"
    df = df.rename(columns=col_map)

    required = ["DC", "Date of created", "hour", "process", "volume"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Dock file missing column: {col}")

    # Parse dates (handle Excel serial numbers)
    df["Date of created"] = df["Date of created"].apply(_parse_date)
    df = df.dropna(subset=["Date of created"])

    df["hour"] = df["hour"].astype(int)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

    # Filter date range
    if date_start:
        df = df[df["Date of created"] >= date_start]
    if date_end:
        df = df[df["Date of created"] <= date_end]

    return df


def _parse_date(val):
    """Parse date value, handling Excel serial numbers."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, (datetime,)):
        return val.date()
    if isinstance(val, date_type):
        return val
    # Excel serial number
    if isinstance(val, (int, float)):
        serial = int(val)
        if 40000 < serial < 60000:
            # Excel serial: days since 1899-12-30
            from datetime import timedelta
            base = datetime(1899, 12, 30)
            return (base + timedelta(days=serial)).date()
    # Try string parsing
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None


# ── Dock MH calculation ──────────────────────────────────

# Mapping from dock process names to activity names and their effort
# hour_offset: how many hours AFTER the IB hour this activity occurs
DOCK_PROCESS_MAP = {
    "IB Regular":              {"activity": "IB",               "hour_offset": 0},
    "IB Cross-Dock":           {"activity": "VIA Bag Sorting",  "hour_offset": 1},
    "IB Cross-Dock Bag Sorter":{"activity": "Bag Sorter Design","hour_offset": 1},
    "OB":                      {"activity": "OB",               "hour_offset": 0},
}


def compute_dock_mh(dock_df, activity_manhours):
    """Compute dock manhours from actual dock volumes. Vectorized.

    VIA Bag Sorting and Bag Sorter Design are offset +1 hour from the IB data hour.
    Returns DataFrame with: DC, Date of created, hour, process, activity, volume, manhours
    """
    if dock_df.empty:
        return pd.DataFrame(columns=[
            "DC", "Date of created", "hour", "process", "activity", "volume", "manhours"
        ])

    # Build lookup DataFrame from DOCK_PROCESS_MAP
    map_rows = []
    for proc, info in DOCK_PROCESS_MAP.items():
        map_rows.append({
            "process": proc,
            "activity": info["activity"],
            "hour_offset": info["hour_offset"],
            "effort": activity_manhours.get(info["activity"], 0),
        })
    map_df = pd.DataFrame(map_rows)

    result = dock_df.merge(map_df, on="process", how="inner")
    result["hour"] = (result["hour"].astype(int) + result["hour_offset"]) % 24
    result["manhours"] = result["volume"] * result["effort"]
    return result[["DC", "Date of created", "hour", "process", "activity", "volume", "manhours"]]


def compute_derived_dock_mh(processing_load_df, template):
    """Compute derived dock activities (OSC Bag Sorting, Bag Staging) from processing volume.
    Vectorized version.
    """
    from core.time_shifter import (
        _build_proportional_offset_map,
        _build_activity_mh_rates,
        VOLUMETRIC_ACTIVITIES,
    )

    DERIVED_DOCK_ACTIVITIES = [
        "OSC Bag Sorting", "Bag Staging",
        "OSC Bag Sorting Volumetric", "Bag Staging Volumetric",
    ]

    offset_map = _build_proportional_offset_map(template["activity_prod"])
    lt_rates = _build_activity_mh_rates(
        template["layout_prod_df"], template["activity_names"], template["activity_manhours"]
    )
    vol_rates = {}
    for act in VOLUMETRIC_ACTIVITIES:
        e = template["activity_manhours"].get(act, 0)
        if e > 0:
            vol_rates[act] = float(e)

    base = processing_load_df[
        ["DC", "layout_name", "Layout Type", "Date of created", "hour",
         "regular_volume", "volumetric_volume"]
    ].copy()
    base["hour"] = base["hour"].astype(int)

    # Build explosion table: (Layout Type, activity, hour_delta, frac, rate, is_vol)
    exp_rows = []
    for act in DERIVED_DOCK_ACTIVITIES:
        is_vol = act in VOLUMETRIC_ACTIVITIES
        offsets = offset_map.get(act, [(0, 1.0)])

        for lt in base["Layout Type"].unique():
            if is_vol:
                rate = vol_rates.get(act, 0)
            else:
                rate = lt_rates.get(lt, {}).get(act, 0)

            if rate == 0:
                continue

            for (delta, frac) in offsets:
                if frac <= 0:
                    continue
                exp_rows.append({
                    "Layout Type": lt,
                    "activity": act,
                    "hour_delta": delta,
                    "frac": frac,
                    "rate": rate * frac,
                    "is_vol": is_vol,
                })

    if not exp_rows:
        return pd.DataFrame(columns=[
            "DC", "layout_name", "Layout Type", "Date of created",
            "hour", "activity", "manhours",
        ])

    explosion = pd.DataFrame(exp_rows)

    # Split regular vs volumetric
    results = []

    exp_reg = explosion[~explosion["is_vol"]]
    if not exp_reg.empty:
        merged = base.merge(exp_reg[["Layout Type", "activity", "hour_delta", "rate"]], on="Layout Type")
        merged["target_hour"] = (merged["hour"] + merged["hour_delta"]) % 24
        merged["manhours"] = merged["regular_volume"] * merged["rate"]
        merged = merged[merged["manhours"] > 0]
        results.append(merged[["DC", "layout_name", "Layout Type", "Date of created",
                                "target_hour", "activity", "manhours"]].rename(columns={"target_hour": "hour"}))

    exp_vol = explosion[explosion["is_vol"]]
    if not exp_vol.empty:
        merged = base.merge(exp_vol[["Layout Type", "activity", "hour_delta", "rate"]], on="Layout Type")
        merged["target_hour"] = (merged["hour"] + merged["hour_delta"]) % 24
        merged["manhours"] = merged["volumetric_volume"] * merged["rate"]
        merged = merged[merged["manhours"] > 0]
        results.append(merged[["DC", "layout_name", "Layout Type", "Date of created",
                                "target_hour", "activity", "manhours"]].rename(columns={"target_hour": "hour"}))

    if results:
        return pd.concat(results, ignore_index=True)
    return pd.DataFrame(columns=[
        "DC", "layout_name", "Layout Type", "Date of created",
        "hour", "activity", "manhours",
    ])

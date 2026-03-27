"""Converts volume × manhours/shipment → manhours per layout per hour.

Two modes:
  - compute_manhours(): Simple aggregate (no time-shifting). Used as fallback.
  - add_layout_type():  Just adds Layout Type column so time_shifter can take over.
"""

import pandas as pd


def add_layout_type(load_df, layout_mapping):
    """Add Layout Type column to load_df (prerequisite for time_shifter).

    Returns load_df with 'Layout Type' column added.
    """
    layout_to_type = dict(zip(layout_mapping["layout_name"], layout_mapping["Layout Type"]))
    load_df = load_df.copy()
    load_df["Layout Type"] = load_df["layout_name"].map(layout_to_type)
    return load_df


def compute_manhours(load_df, layout_mapping, manhours_per_ship):
    """Simple (non-shifted) manhours calculation. Kept as fallback.

    For each row:
      - regular_volume × layout_type_manhours/ship  → regular_mh
      - volumetric_volume × volumetric_manhours/ship → volumetric_mh
      - total_mh = regular_mh + volumetric_mh
    """
    layout_to_type = dict(zip(layout_mapping["layout_name"], layout_mapping["Layout Type"]))
    volumetric_mh_per_ship = manhours_per_ship.get("Volumetric", 0.060095)

    load_df = load_df.copy()
    load_df["Layout Type"] = load_df["layout_name"].map(layout_to_type)
    load_df["mh_per_ship"] = load_df["Layout Type"].map(manhours_per_ship).fillna(0)
    load_df["regular_mh"] = load_df["regular_volume"] * load_df["mh_per_ship"]
    load_df["volumetric_mh"] = load_df["volumetric_volume"] * volumetric_mh_per_ship
    load_df["total_mh"] = load_df["regular_mh"] + load_df["volumetric_mh"]

    return load_df

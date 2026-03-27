"""Computes permanent/flex headcount per layout per shift, with overlap optimization."""

import pandas as pd
import numpy as np
from config import SHIFTS, OVERLAP_SHIFTS


def _heads_from_mh(mh, flex_pct, flex_efficiency):
    """Convert manhours to perm + flex headcount."""
    perm_mh = mh * (1 - flex_pct)
    flex_mh = mh * flex_pct
    perm = int(np.ceil(perm_mh))
    flex = int(np.ceil(flex_mh / flex_efficiency))
    return perm, flex


def compute_shift_headcount(shift_peaks, overlap_peaks, flex_pct=0.10, flex_efficiency=0.80):
    """Compute per-shift headcount per layout with overlap optimization.

    Logic:
    1. Size each shift based on its exclusive-hours peak MH.
    2. For overlap hours (12-16): check if Shift 2 + Shift 3 MH capacity
       covers the overlap peak. If not, increase the smaller shift.

    Returns DataFrame: DC, layout_name, Layout Type, shift, peak_mh,
                        perm_heads, flex_heads, total_heads
    """
    # Pivot shift_peaks so each layout has one row per shift
    df = shift_peaks.copy()

    # Ensure all layouts have all shifts (fill missing with 0)
    all_layouts = df[["DC", "layout_name", "Layout Type"]].drop_duplicates()
    all_shifts = pd.DataFrame({"shift": list(SHIFTS.keys())})
    scaffold = all_layouts.merge(all_shifts, how="cross")
    df = scaffold.merge(df, on=["DC", "layout_name", "Layout Type", "shift"], how="left")
    df["peak_mh"] = df["peak_mh"].fillna(0)

    # Merge overlap peak
    df = df.merge(overlap_peaks, on=["DC", "layout_name", "Layout Type"], how="left")
    df["overlap_peak_mh"] = df["overlap_peak_mh"].fillna(0)

    # For each layout, apply overlap optimization
    # Group by layout
    results = []
    for (dc, layout, lt), group in df.groupby(["DC", "layout_name", "Layout Type"]):
        shift_mh = {}
        for _, row in group.iterrows():
            shift_mh[row["shift"]] = row["peak_mh"]

        overlap_peak = group["overlap_peak_mh"].iloc[0]

        # Check overlap coverage
        s2_name, s3_name = OVERLAP_SHIFTS
        s2_mh = shift_mh.get(s2_name, 0)
        s3_mh = shift_mh.get(s3_name, 0)

        if s2_mh + s3_mh < overlap_peak:
            deficit = overlap_peak - (s2_mh + s3_mh)
            # Add deficit to the shift with lower exclusive peak (more room to grow)
            if s2_mh <= s3_mh:
                shift_mh[s2_name] = s2_mh + deficit
            else:
                shift_mh[s3_name] = s3_mh + deficit

        # Compute heads per shift
        for shift_name in SHIFTS:
            mh = shift_mh.get(shift_name, 0)
            perm, flex = _heads_from_mh(mh, flex_pct, flex_efficiency)
            results.append({
                "DC": dc,
                "layout_name": layout,
                "Layout Type": lt,
                "shift": shift_name,
                "peak_mh": mh,
                "perm_heads": perm,
                "flex_heads": flex,
                "total_heads": perm + flex,
            })

    return pd.DataFrame(results)


def compute_daily_flex(load_df, staffing_df, top_n=7, flex_efficiency=0.80):
    """Compute daily flex requirements per DC per shift.

    For each day × DC × shift:
    1. Compute that day's top-N hour MH (per layout, then summed to DC level)
    2. Compare against permanent capacity (from staffing_df)
    3. Flex = gap / flex_efficiency if day MH > perm capacity, else 0

    Returns DataFrame: DC, Date, shift, day_mh, perm_capacity, flex_needed
    """
    if load_df.empty or staffing_df.empty:
        return pd.DataFrame(columns=[
            "DC", "Date of created", "shift", "day_mh", "perm_capacity", "flex_needed"
        ])

    # Aggregate hourly MH per layout × date × hour
    hourly = (
        load_df.groupby(["DC", "layout_name", "Layout Type", "Date of created", "hour"])["total_mh"]
        .sum()
        .reset_index()
    )

    # Get perm capacity per layout × shift (perm_heads ≈ perm MH capacity)
    perm_cap = staffing_df[["DC", "layout_name", "Layout Type", "shift", "perm_heads"]].copy()

    all_dates = sorted(load_df["Date of created"].unique())
    group_cols = ["DC", "layout_name", "Layout Type"]

    results = []
    for shift_name, shift_def in SHIFTS.items():
        exclusive_hours = shift_def["exclusive"]
        shift_hourly = hourly[hourly["hour"].isin(exclusive_hours)]

        if shift_hourly.empty:
            continue

        # For each day, compute top-N hour mean per layout
        shift_hourly = shift_hourly.copy()
        day_group = group_cols + ["Date of created"]
        shift_hourly["_rank"] = (
            shift_hourly.groupby(day_group)["total_mh"]
            .rank(method="first", ascending=False)
        )
        top = shift_hourly[shift_hourly["_rank"] <= top_n]
        daily_layout_mh = top.groupby(day_group)["total_mh"].mean().reset_index()

        # Sum across layouts to DC level per day
        daily_dc_mh = (
            daily_layout_mh.groupby(["DC", "Date of created"])["total_mh"]
            .sum()
            .reset_index()
            .rename(columns={"total_mh": "day_mh"})
        )

        # Perm capacity for this shift at DC level
        shift_perm = perm_cap[perm_cap["shift"] == shift_name]
        dc_perm = shift_perm.groupby("DC")["perm_heads"].sum().reset_index()
        dc_perm = dc_perm.rename(columns={"perm_heads": "perm_capacity"})

        # Merge and compute flex
        merged = daily_dc_mh.merge(dc_perm, on="DC", how="left")
        merged["perm_capacity"] = merged["perm_capacity"].fillna(0)
        merged["gap_mh"] = (merged["day_mh"] - merged["perm_capacity"]).clip(lower=0)
        merged["flex_needed"] = np.ceil(merged["gap_mh"] / flex_efficiency).astype(int)
        merged["shift"] = shift_name

        results.append(merged[["DC", "Date of created", "shift", "day_mh",
                                "perm_capacity", "flex_needed"]])

    if results:
        return pd.concat(results, ignore_index=True).sort_values(
            ["DC", "Date of created", "shift"]
        )
    return pd.DataFrame(columns=[
        "DC", "Date of created", "shift", "day_mh", "perm_capacity", "flex_needed"
    ])


def rollup_by_dc(staffing_df):
    """Roll up headcount to DC level (summed across all shifts and layouts).

    Returns DataFrame: DC, layouts_count, total_perm, total_flex, total_heads.
    """
    dc_summary = (
        staffing_df.groupby("DC")
        .agg(
            layouts_count=("layout_name", "nunique"),
            total_perm=("perm_heads", "sum"),
            total_flex=("flex_heads", "sum"),
            total_heads=("total_heads", "sum"),
        )
        .reset_index()
        .sort_values("total_heads", ascending=False)
    )
    return dc_summary


def rollup_by_dc_shift(staffing_df):
    """Roll up headcount per DC per shift.

    Returns DataFrame: DC, shift, perm_heads, flex_heads, total_heads.
    """
    return (
        staffing_df.groupby(["DC", "shift"])
        .agg(
            perm_heads=("perm_heads", "sum"),
            flex_heads=("flex_heads", "sum"),
            total_heads=("total_heads", "sum"),
        )
        .reset_index()
        .sort_values(["DC", "shift"])
    )


def rollup_by_layout_type(staffing_df):
    """Roll up headcount by Layout Type within each DC (across all shifts).

    Returns DataFrame: DC, Layout Type, layout_count, total_peak_mh, perm, flex, total.
    """
    lt_summary = (
        staffing_df.groupby(["DC", "Layout Type"])
        .agg(
            layout_count=("layout_name", "nunique"),
            total_peak_mh=("peak_mh", "sum"),
            perm_heads=("perm_heads", "sum"),
            flex_heads=("flex_heads", "sum"),
            total_heads=("total_heads", "sum"),
        )
        .reset_index()
        .sort_values(["DC", "total_heads"], ascending=[True, False])
    )
    return lt_summary

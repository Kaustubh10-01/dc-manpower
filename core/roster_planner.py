"""Roster Planning Module.

Takes staffing plan (perm + flex per DC × shift) and produces a daily roster
that accounts for:
  - 1 mandatory day off per 7 days (Indian labour code)
  - Weekly offs assigned to lightest load day per week
  - Absenteeism: 15% for day 1-10, 7% for day 11+
  - Each person works exactly 6 out of 7 days
"""

import pandas as pd
import numpy as np
from config import SHIFTS


# ── Absenteeism rules ──
def _absenteeism_rate(day_of_month):
    """Return absenteeism % based on day of month."""
    if day_of_month <= 10:
        return 0.15
    return 0.07


def compute_daily_load_index(load_df, dc_col="DC", date_col="Date of created", mh_col="total_mh"):
    """Compute daily load index per DC.

    Returns DataFrame: DC, date, daily_mh, load_index (relative to DC's average)
    """
    daily = load_df.groupby([dc_col, date_col])[mh_col].sum().reset_index()
    daily.columns = ["DC", "date", "daily_mh"]

    # Load index = day's MH / DC's average daily MH
    dc_avg = daily.groupby("DC")["daily_mh"].transform("mean")
    daily["load_index"] = daily["daily_mh"] / dc_avg
    daily["dow"] = pd.to_datetime(daily["date"]).dt.day_name()
    daily["dom"] = pd.to_datetime(daily["date"]).dt.day
    daily["week_num"] = pd.to_datetime(daily["date"]).dt.isocalendar().week.astype(int)

    return daily


def assign_weekly_offs(daily_load, roster_size):
    """For a single DC, assign weekly offs to lightest days.

    Args:
        daily_load: DataFrame with date, daily_mh, load_index, week_num for ONE DC
        roster_size: total people to roster (int)

    Returns:
        DataFrame with: date, dow, daily_mh, load_index, offs_assigned, on_floor
    """
    result = daily_load.copy()
    result["offs_assigned"] = 0

    # For each week, distribute roster_size offs across 7 days
    # More offs on lighter days, fewer on heavier days
    for week in result["week_num"].unique():
        week_mask = result["week_num"] == week
        week_data = result[week_mask].copy()
        n_days = len(week_data)

        if n_days == 0:
            continue

        # Total offs this week = roster_size (each person gets 1 off)
        # But if partial week (< 7 days), prorate
        total_offs = int(round(roster_size * n_days / 7))

        # Distribute offs inversely proportional to load
        # Lighter days get more offs
        loads = week_data["daily_mh"].values
        if loads.sum() == 0:
            # Equal distribution if no load data
            inv_weights = np.ones(n_days) / n_days
        else:
            # Inverse load weighting
            inv_loads = 1.0 / (loads + 1e-6)  # avoid div by zero
            inv_weights = inv_loads / inv_loads.sum()

        # Allocate offs proportionally
        raw_offs = inv_weights * total_offs
        offs = np.floor(raw_offs).astype(int)

        # Distribute remainder to lightest days
        remainder = total_offs - offs.sum()
        if remainder > 0:
            fractional = raw_offs - offs
            top_indices = np.argsort(-fractional)[:int(remainder)]
            offs[top_indices] += 1

        # Cap: can't have more offs than roster_size on any day
        offs = np.minimum(offs, roster_size)

        # Also ensure on_floor doesn't go below 0
        result.loc[week_mask, "offs_assigned"] = offs

    result["on_floor_after_offs"] = roster_size - result["offs_assigned"]

    return result


def compute_roster_plan(staffing_df, load_df, absenteeism_early=0.15, absenteeism_late=0.07):
    """Full roster plan for all DCs.

    Args:
        staffing_df: output from compute_shift_headcount (DC, shift, perm_heads, etc.)
        load_df: time-shifted load data (DC, Date of created, hour, total_mh)
        absenteeism_early: rate for day 1-10 (default 15%)
        absenteeism_late: rate for day 11+ (default 7%)

    Returns:
        roster_df: DC, date, dow, dom, shift, perm_heads, roster_size,
                   offs_assigned, on_floor, absent_expected, required_on_floor,
                   flex_needed
    """
    if staffing_df.empty or load_df.empty:
        return pd.DataFrame()

    # Daily load per DC
    daily_load = compute_daily_load_index(load_df)

    # Get perm heads per DC × shift
    dc_shift_perm = staffing_df.groupby(["DC", "shift"])["perm_heads"].sum().reset_index()

    results = []

    for _, row in dc_shift_perm.iterrows():
        dc = row["DC"]
        shift = row["shift"]
        perm = int(row["perm_heads"])

        if perm == 0:
            continue

        # Get shift hours
        shift_def = SHIFTS.get(shift, {})
        shift_hours = shift_def.get("hours", [])

        # Filter load to this DC and shift hours
        dc_load = daily_load[daily_load["DC"] == dc].copy()

        if dc_load.empty:
            continue

        # Compute shift-specific daily load
        dc_shift_load = load_df[
            (load_df["DC"] == dc) & (load_df["hour"].isin(shift_hours))
        ].groupby("Date of created")["total_mh"].sum().reset_index()
        dc_shift_load.columns = ["date", "shift_mh"]

        # Merge with daily metadata
        dc_daily = dc_load[["date", "dow", "dom", "week_num"]].drop_duplicates()
        dc_daily = dc_daily.merge(dc_shift_load, on="date", how="left").fillna(0)
        dc_daily["daily_mh"] = dc_daily["shift_mh"]

        # Roster size: need enough people so that after 1/7 off, we have perm on floor
        # roster × (6/7) = perm → roster = perm × 7/6
        roster_size = int(np.ceil(perm * 7 / 6))

        # Assign weekly offs
        dc_daily_with_offs = assign_weekly_offs(dc_daily, roster_size)

        for _, d in dc_daily_with_offs.iterrows():
            dom = int(d["dom"])
            abs_rate = absenteeism_early if dom <= 10 else absenteeism_late

            on_floor = int(d["on_floor_after_offs"])
            absent_expected = int(np.ceil(on_floor * abs_rate))
            available = on_floor - absent_expected

            # How many do we actually need for this day's load?
            # Use the shift MH and the average MH per head
            # Simple: perm is sized for peak, so required = perm × (day_load / peak_load)
            avg_shift_mh = dc_daily["shift_mh"].mean()
            peak_shift_mh = dc_daily["shift_mh"].nlargest(3).mean()  # top 3 days

            if peak_shift_mh > 0:
                day_ratio = d["shift_mh"] / peak_shift_mh
            else:
                day_ratio = 1.0

            required = int(np.ceil(perm * min(day_ratio, 1.0)))  # cap at perm
            flex_needed = max(0, required - available)
            surplus = max(0, available - required)

            # Ensure: working + surplus + absent + offs = roster_size
            # working = min(required, available)  (actual people working from roster)
            working = min(required, available)
            # surplus = available - working
            surplus = available - working
            # These 4 always sum to roster_size:
            #   working + surplus + absent_expected + offs_assigned = roster_size

            results.append({
                "DC": dc,
                "date": d["date"],
                "dow": d["dow"],
                "dom": dom,
                "week_num": int(d["week_num"]),
                "shift": shift,
                "perm_heads": perm,
                "roster_size": roster_size,
                "offs_assigned": int(d["offs_assigned"]),
                "on_floor_before_absent": on_floor,
                "absent_expected": absent_expected,
                "absenteeism_rate": abs_rate,
                "working": working,
                "available": available,
                "required_on_floor": required,
                "flex_needed": flex_needed,
                "surplus": surplus,
                "day_load_ratio": round(day_ratio, 3),
            })

    return pd.DataFrame(results)


def roster_summary(roster_df):
    """Summarize roster plan per DC.

    Returns DataFrame: DC, roster_size, avg_on_floor, avg_flex, max_flex,
                       total_off_days, avg_surplus
    """
    if roster_df.empty:
        return pd.DataFrame()

    summary = roster_df.groupby("DC").agg(
        roster_size=("roster_size", "first"),
        perm_heads=("perm_heads", "first"),
        avg_available=("available", "mean"),
        avg_required=("required_on_floor", "mean"),
        avg_flex=("flex_needed", "mean"),
        max_flex=("flex_needed", "max"),
        avg_surplus=("surplus", "mean"),
        total_days=("date", "nunique"),
    ).reset_index()

    summary["avg_flex"] = summary["avg_flex"].round(1)
    summary["avg_surplus"] = summary["avg_surplus"].round(1)
    summary["avg_available"] = summary["avg_available"].round(0).astype(int)
    summary["avg_required"] = summary["avg_required"].round(0).astype(int)

    return summary

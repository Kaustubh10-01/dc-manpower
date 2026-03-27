"""Identifies peak days and peak hourly manhours per DC and layout."""

import pandas as pd
import numpy as np
from config import SHIFTS, OVERLAP_HOURS

# Top-N hours to average for shift staffing
TOP_N_HOURS = 7


def select_peak_days(load_df, peak_days_count=3):
    """For each DC, find the top N days by total manhours.

    Returns dict: DC -> list of peak dates (sorted descending by total MH).
    """
    dc_daily = (
        load_df.groupby(["DC", "Date of created"])["total_mh"]
        .sum()
        .reset_index()
    )

    peak_days = {}
    for dc in dc_daily["DC"].unique():
        dc_data = dc_daily[dc_daily["DC"] == dc].sort_values("total_mh", ascending=False)
        top_dates = dc_data.head(peak_days_count)["Date of created"].tolist()
        peak_days[dc] = top_dates

    return peak_days


def _filter_to_peak_days(load_df, peak_days):
    """Filter load_df to only peak days per DC. Returns concatenated DataFrame."""
    rows = []
    for dc, dates in peak_days.items():
        mask = (load_df["DC"] == dc) & (load_df["Date of created"].isin(dates))
        rows.append(load_df[mask])

    if not rows:
        return pd.DataFrame()

    return pd.concat(rows, ignore_index=True)


def _aggregate_hourly(peak_df):
    """Aggregate manhours per layout per date per hour (sum fwd + rev)."""
    return (
        peak_df.groupby(["DC", "layout_name", "Layout Type", "Date of created", "hour"])["total_mh"]
        .sum()
        .reset_index()
    )


def _top_n_mean_vectorized(hourly_df, group_cols, value_col, n=TOP_N_HOURS):
    """Per-day top-N mean, then averaged across days.

    For each (group × date): pick top N hours → mean (= that day's staffing MH).
    Then average across dates → final staffing MH.

    This ensures adding more (less extreme) peak days LOWERS the result.
    """
    hourly_df = hourly_df.copy()
    date_col = "Date of created"

    # Step 1: rank within (group + date)
    day_group = group_cols + [date_col]
    hourly_df["_rank"] = (
        hourly_df.groupby(day_group)[value_col]
        .rank(method="first", ascending=False)
    )
    # Keep only top N per day
    top = hourly_df[hourly_df["_rank"] <= n]

    # Step 2: mean per (group + date) = each day's staffing MH
    daily_mean = top.groupby(day_group)[value_col].mean().reset_index()

    # Step 3: average across days = final staffing MH
    result = daily_mean.groupby(group_cols)[value_col].mean().reset_index()
    return result


def compute_peak_hourly_mh(load_df, peak_days, method="max", percentile=90):
    """For each layout, across its DC's peak days, find peak hourly manhours.

    Returns DataFrame with columns: DC, layout_name, Layout Type, peak_hourly_mh.
    """
    peak_df = _filter_to_peak_days(load_df, peak_days)
    if peak_df.empty:
        return pd.DataFrame(columns=["DC", "layout_name", "Layout Type", "peak_hourly_mh"])

    hourly = _aggregate_hourly(peak_df)

    if method == "max":
        result = (
            hourly.groupby(["DC", "layout_name", "Layout Type"])["total_mh"]
            .max()
            .reset_index()
            .rename(columns={"total_mh": "peak_hourly_mh"})
        )
    else:
        result = (
            hourly.groupby(["DC", "layout_name", "Layout Type"])["total_mh"]
            .quantile(percentile / 100.0)
            .reset_index()
            .rename(columns={"total_mh": "peak_hourly_mh"})
        )

    return result


def compute_shift_peak_mh(load_df, peak_days, top_n=TOP_N_HOURS):
    """For each layout, compute staffing MH per shift as the
    average of the top-5 hours (across all peak days in that shift).

    For each shift, only considers the shift's EXCLUSIVE hours to size it.
    Also computes the overlap peak (hours 12-16) for the overlap optimization.

    Returns:
        shift_peaks: DataFrame with columns:
            DC, layout_name, Layout Type, shift, peak_mh
        overlap_peaks: DataFrame with columns:
            DC, layout_name, Layout Type, overlap_peak_mh
    """
    peak_df = _filter_to_peak_days(load_df, peak_days)
    if peak_df.empty:
        empty_shifts = pd.DataFrame(columns=["DC", "layout_name", "Layout Type", "shift", "peak_mh"])
        empty_overlap = pd.DataFrame(columns=["DC", "layout_name", "Layout Type", "overlap_peak_mh"])
        return empty_shifts, empty_overlap

    hourly = _aggregate_hourly(peak_df)
    group_cols = ["DC", "layout_name", "Layout Type"]

    # Compute average-of-top-5 MH per shift (exclusive hours only)
    shift_results = []
    for shift_name, shift_def in SHIFTS.items():
        exclusive_hours = shift_def["exclusive"]
        shift_hourly = hourly[hourly["hour"].isin(exclusive_hours)]

        if shift_hourly.empty:
            continue

        peak = _top_n_mean_vectorized(shift_hourly, group_cols, "total_mh", n=top_n)
        peak = peak.rename(columns={"total_mh": "peak_mh"})
        peak["shift"] = shift_name
        shift_results.append(peak)

    shift_peaks = pd.concat(shift_results, ignore_index=True) if shift_results else pd.DataFrame(
        columns=["DC", "layout_name", "Layout Type", "shift", "peak_mh"]
    )

    # Compute overlap peak (top-5 average for hours 12-16)
    overlap_hourly = hourly[hourly["hour"].isin(OVERLAP_HOURS)]
    if not overlap_hourly.empty:
        overlap_peaks = _top_n_mean_vectorized(overlap_hourly, group_cols, "total_mh", n=top_n)
        overlap_peaks = overlap_peaks.rename(columns={"total_mh": "overlap_peak_mh"})
    else:
        overlap_peaks = pd.DataFrame(columns=["DC", "layout_name", "Layout Type", "overlap_peak_mh"])

    return shift_peaks, overlap_peaks

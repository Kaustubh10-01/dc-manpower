"""Export detailed manhour breakdown as downloadable Excel.

Shows HOW the peak manhour requirement was derived:
  - Which hour, which date, which shift
  - Activity-level decomposition with time offsets
  - Full hourly MH profile per layout across peak days
"""

import io
import math
import pandas as pd
import numpy as np
from config import SHIFTS, OVERLAP_HOURS


def _hour_to_shift(hour):
    """Map an hour (0-23) to shift name(s). Returns primary shift."""
    for shift_name, shift_def in SHIFTS.items():
        if hour in shift_def["exclusive"]:
            return shift_name
    if hour in OVERLAP_HOURS:
        return "Overlap (Shift 2+3)"
    return "Uncovered"


def _format_hour(h):
    """Format hour as readable string: 0 → '12 AM', 14 → '2 PM'."""
    if h == 0:
        return "12 AM"
    elif h < 12:
        return f"{h} AM"
    elif h == 12:
        return "12 PM"
    else:
        return f"{h - 12} PM"


def generate_manhour_excel(load_df, activity_detail_df, peak_days, staffing_df):
    """Generate Excel with detailed manhour breakdown.

    Parameters
    ----------
    load_df : DataFrame
        Time-shifted load with total_mh per (layout, hour, date).
    activity_detail_df : DataFrame
        Per-activity detail from compute_activity_detail().
    peak_days : dict
        DC → list of peak dates.
    staffing_df : DataFrame
        Staffing output with shift, peak_mh, headcount per layout.

    Returns
    -------
    bytes : Excel file content.
    """
    buf = io.BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:

        # ═══════════════════════════════════════════════════
        # Sheet 1: Peak Hour Summary
        # For each layout × shift: which hour/date is the peak, total MH, heads
        # ═══════════════════════════════════════════════════
        _write_peak_hour_summary(writer, load_df, peak_days, staffing_df)

        # ═══════════════════════════════════════════════════
        # Sheet 2: Activity Breakdown at Peak Hour
        # For each layout × shift: activity-level MH at the peak hour
        # ═══════════════════════════════════════════════════
        _write_activity_breakdown(writer, load_df, activity_detail_df, peak_days)

        # ═══════════════════════════════════════════════════
        # Sheet 3: Hourly MH Profile (per layout, across peak days)
        # Full 24-hour curve so user can see the shape
        # ═══════════════════════════════════════════════════
        _write_hourly_profile(writer, load_df, peak_days)

        # ═══════════════════════════════════════════════════
        # Sheet 4: Activity Offset Reference
        # ═══════════════════════════════════════════════════
        _write_offset_reference(writer, activity_detail_df)

    buf.seek(0)
    return buf.getvalue()


def _write_peak_hour_summary(writer, load_df, peak_days, staffing_df):
    """Sheet 1: For each layout × shift, show peak hour details."""
    rows = []

    for dc in sorted(load_df["DC"].unique()):
        dc_dates = peak_days.get(dc, [])
        dc_load = load_df[
            (load_df["DC"] == dc) & (load_df["Date of created"].isin(dc_dates))
        ]

        if dc_load.empty:
            continue

        for layout in sorted(dc_load["layout_name"].unique()):
            layout_load = dc_load[dc_load["layout_name"] == layout]
            lt = layout_load["Layout Type"].iloc[0] if not layout_load.empty else ""

            for shift_name, shift_def in SHIFTS.items():
                # All hours for this shift (exclusive + overlap)
                shift_hours = shift_def["hours"]
                shift_load = layout_load[layout_load["hour"].isin(shift_hours)]

                if shift_load.empty or shift_load["total_mh"].sum() == 0:
                    continue

                # Find the peak hour within this shift
                peak_idx = shift_load["total_mh"].idxmax()
                peak_row = shift_load.loc[peak_idx]
                peak_hour = int(peak_row["hour"])
                peak_date = peak_row["Date of created"]
                peak_mh = float(peak_row["total_mh"])

                # Get staffing for this layout × shift
                staff_match = staffing_df[
                    (staffing_df["DC"] == dc)
                    & (staffing_df["layout_name"] == layout)
                    & (staffing_df["shift"] == shift_name)
                ]
                perm = int(staff_match["perm_heads"].iloc[0]) if not staff_match.empty else 0
                flex = int(staff_match["flex_heads"].iloc[0]) if not staff_match.empty else 0
                total = int(staff_match["total_heads"].iloc[0]) if not staff_match.empty else 0

                rows.append({
                    "DC": dc,
                    "Layout": layout,
                    "Layout Type": lt,
                    "Shift": shift_name,
                    "Peak Date": peak_date,
                    "Peak Hour": peak_hour,
                    "Peak Hour Label": _format_hour(peak_hour),
                    "Hour Belongs To": _hour_to_shift(peak_hour),
                    "Peak MH": round(peak_mh, 2),
                    "Permanent": perm,
                    "Flex": flex,
                    "Total Heads": total,
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["DC", "Layout", "Shift"])
    df.to_excel(writer, sheet_name="Peak Hour Summary", index=False)


def _write_activity_breakdown(writer, load_df, activity_detail_df, peak_days):
    """Sheet 2: Activity-level MH breakdown at each layout's peak hour per shift."""
    all_rows = []

    for dc in sorted(load_df["DC"].unique()):
        dc_dates = peak_days.get(dc, [])
        dc_load = load_df[
            (load_df["DC"] == dc) & (load_df["Date of created"].isin(dc_dates))
        ]
        dc_detail = activity_detail_df[
            (activity_detail_df["DC"] == dc)
            & (activity_detail_df["Date of created"].isin(dc_dates))
        ]

        if dc_load.empty:
            continue

        for layout in sorted(dc_load["layout_name"].unique()):
            layout_load = dc_load[dc_load["layout_name"] == layout]
            layout_detail = dc_detail[dc_detail["layout_name"] == layout]
            lt = layout_load["Layout Type"].iloc[0] if not layout_load.empty else ""

            for shift_name, shift_def in SHIFTS.items():
                shift_hours = shift_def["hours"]
                shift_load = layout_load[layout_load["hour"].isin(shift_hours)]

                if shift_load.empty or shift_load["total_mh"].sum() == 0:
                    continue

                # Find peak hour
                peak_idx = shift_load["total_mh"].idxmax()
                peak_row = shift_load.loc[peak_idx]
                peak_hour = int(peak_row["hour"])
                peak_date = peak_row["Date of created"]

                # Get activity detail for this peak hour
                hour_detail = layout_detail[
                    (layout_detail["target_hour"] == peak_hour)
                    & (layout_detail["Date of created"] == peak_date)
                ]

                if hour_detail.empty:
                    continue

                # Aggregate by activity
                agg_cols = {"manhours": "sum"}
                group_cols = ["activity", "offset_hours"]
                if "fraction" in hour_detail.columns:
                    group_cols.append("fraction")

                act_summary = (
                    hour_detail.groupby(group_cols)
                    .agg(
                        manhours=("manhours", "sum"),
                        source_hours=("source_hour", lambda x: sorted(x.unique().tolist())),
                    )
                    .reset_index()
                )
                # Re-aggregate to activity level (combine fractions)
                act_agg = (
                    act_summary.groupby("activity")
                    .agg(
                        manhours=("manhours", "sum"),
                        source_hours=("source_hours", "first"),
                        offset_hours=("offset_hours", "first"),
                    )
                    .reset_index()
                    .sort_values("manhours", ascending=False)
                )

                for _, act_row in act_agg.iterrows():
                    src_hours = act_row["source_hours"]
                    src_label = ", ".join(_format_hour(int(h)) for h in src_hours)

                    all_rows.append({
                        "DC": dc,
                        "Layout": layout,
                        "Layout Type": lt,
                        "Shift": shift_name,
                        "Peak Date": peak_date,
                        "Peak Hour": _format_hour(peak_hour),
                        "Activity": act_row["activity"],
                        "Offset (hours)": int(act_row["offset_hours"]),
                        "Source Processing Hour(s)": src_label,
                        "Activity MH": round(float(act_row["manhours"]), 4),
                    })

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.sort_values(["DC", "Layout", "Shift", "Activity MH"], ascending=[True, True, True, False])
    df.to_excel(writer, sheet_name="Activity Breakdown", index=False)


def _write_hourly_profile(writer, load_df, peak_days):
    """Sheet 3: Full 24-hour MH profile per layout on peak days."""
    rows = []

    for dc in sorted(load_df["DC"].unique()):
        dc_dates = peak_days.get(dc, [])
        dc_load = load_df[
            (load_df["DC"] == dc) & (load_df["Date of created"].isin(dc_dates))
        ]

        if dc_load.empty:
            continue

        for layout in sorted(dc_load["layout_name"].unique()):
            layout_load = dc_load[dc_load["layout_name"] == layout]
            lt = layout_load["Layout Type"].iloc[0] if not layout_load.empty else ""

            for date_val in sorted(dc_dates):
                date_load = layout_load[layout_load["Date of created"] == date_val]
                hourly = date_load.set_index("hour")["total_mh"].reindex(range(24), fill_value=0)

                row = {
                    "DC": dc,
                    "Layout": layout,
                    "Layout Type": lt,
                    "Date": date_val,
                }
                for h in range(24):
                    row[_format_hour(h)] = round(float(hourly.get(h, 0)), 2)

                row["Daily Total"] = round(float(hourly.sum()), 2)
                row["Peak Hour"] = _format_hour(int(hourly.idxmax())) if hourly.max() > 0 else "-"
                row["Peak MH"] = round(float(hourly.max()), 2)
                rows.append(row)

    df = pd.DataFrame(rows)
    df.to_excel(writer, sheet_name="Hourly MH Profile", index=False)


def _write_offset_reference(writer, activity_detail_df):
    """Sheet 4: Activity offset reference table."""
    if activity_detail_df.empty:
        pd.DataFrame(columns=["Activity", "Offset Hours", "Direction"]).to_excel(
            writer, sheet_name="Activity Offsets", index=False
        )
        return

    ref = (
        activity_detail_df[["activity", "offset_hours"]]
        .drop_duplicates()
        .sort_values("activity")
    )
    ref["Direction"] = ref["offset_hours"].apply(
        lambda x: "Prior" if x < 0 else ("Post" if x > 0 else "Same hour")
    )
    ref["Offset (abs hours)"] = ref["offset_hours"].abs()
    ref.columns = ["Activity", "Offset Hours (signed)", "Direction", "Offset (abs hours)"]
    ref = ref[["Activity", "Direction", "Offset (abs hours)", "Offset Hours (signed)"]]
    ref.to_excel(writer, sheet_name="Activity Offsets", index=False)

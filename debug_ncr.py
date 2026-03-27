"""Generate detailed working Excel for NCR Bilaspur Night Shift."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, ".")

import pandas as pd
import numpy as np
from datetime import date
from core.load_reader import read_load_files
from core.template_reader import load_all_template
from core.volumetric_splitter import apply_volumetric_split
from core.manhours_calculator import add_layout_type, compute_manhours
from core.time_shifter import (
    apply_time_offsets, compute_activity_detail,
    _build_proportional_offset_map, _build_activity_mh_rates, VOLUMETRIC_ACTIVITIES
)
from core.peak_selector import select_peak_days

template = load_all_template()
load_df = read_load_files(date_start=date(2026, 3, 21), date_end=date(2026, 3, 23))
load_df = apply_volumetric_split(load_df, template["volumetric_pct"], template["layout_mapping"])
load_df = add_layout_type(load_df, template["layout_mapping"])
load_df_pre = load_df.copy()
shifted = apply_time_offsets(load_df_pre, template)

peak_days = select_peak_days(shifted, peak_days_count=3)
ncr_dates = peak_days.get("NCR Bilaspur DC", [])
night_hours = [21, 22, 23, 0, 1, 2, 3, 4, 5]

# Activity detail
detail = compute_activity_detail(load_df_pre, template)
ncr_detail = detail[
    (detail["DC"] == "NCR Bilaspur DC")
    & (detail["Date of created"].isin(ncr_dates))
]

# Offset map
offset_map = _build_proportional_offset_map(template["activity_prod"])
lt_rates = _build_activity_mh_rates(
    template["layout_prod_df"], template["activity_names"], template["activity_manhours"]
)

# NCR Bilaspur subsets
ncr_raw = load_df_pre[
    (load_df_pre["DC"] == "NCR Bilaspur DC")
    & (load_df_pre["Date of created"].isin(ncr_dates))
]
ncr_shifted = shifted[
    (shifted["DC"] == "NCR Bilaspur DC")
    & (shifted["Date of created"].isin(ncr_dates))
]

# Simple (unshifted) for comparison
simple = compute_manhours(load_df_pre.copy(), template["layout_mapping"], template["manhours_per_ship"])
simple_ncr = simple[
    (simple["DC"] == "NCR Bilaspur DC")
    & (simple["Date of created"].isin(ncr_dates))
]

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:

    # ── Sheet 1: Raw Volume ──
    raw_pivot = ncr_raw.pivot_table(
        index=["layout_name", "Layout Type", "Date of created"],
        columns="hour",
        values="regular_volume",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    raw_pivot.to_excel(writer, sheet_name="1-Raw Volume", index=False)

    # ── Sheet 2: Activity Offsets ──
    offset_rows = []
    for act, offsets in sorted(offset_map.items()):
        for (delta, frac) in offsets:
            sign = "+" if delta >= 0 else ""
            offset_rows.append({
                "Activity": act,
                "Hour Delta": delta,
                "Fraction": round(frac, 4),
                "Description": f"{frac:.1%} to hour{sign}{delta}",
            })
    pd.DataFrame(offset_rows).to_excel(writer, sheet_name="2-Activity Offsets", index=False)

    # ── Sheet 3: Activity Rates per Layout Type ──
    rate_rows = []
    for lt, acts in sorted(lt_rates.items()):
        for act, rate in sorted(acts.items()):
            rate_rows.append({"Layout Type": lt, "Activity": act, "MH per Ship": round(rate, 8)})
    # Add volumetric
    vol_effort = template["activity_manhours"]
    for act in VOLUMETRIC_ACTIVITIES:
        eff = vol_effort.get(act, 0)
        if eff > 0:
            rate_rows.append({"Layout Type": "(Volumetric - all LTs)", "Activity": act, "MH per Ship": round(eff, 8)})
    pd.DataFrame(rate_rows).to_excel(writer, sheet_name="3-Activity Rates", index=False)

    # ── Sheet 4: Full Activity Detail (every row traced) ──
    # For each layout x date x source_hour: show volume, each activity, offset, target hour, MH
    detail_rows = []
    for layout in sorted(ncr_raw["layout_name"].unique()):
        layout_raw = ncr_raw[ncr_raw["layout_name"] == layout]
        layout_detail = ncr_detail[ncr_detail["layout_name"] == layout]
        lt = layout_raw["Layout Type"].iloc[0]

        for dt in sorted(ncr_dates):
            day_raw = layout_raw[layout_raw["Date of created"] == dt]
            day_detail = layout_detail[layout_detail["Date of created"] == dt]

            for _, raw_row in day_raw.iterrows():
                src_hour = int(raw_row["hour"])
                reg_vol = raw_row["regular_volume"]
                vol_vol = raw_row["volumetric_volume"]

                hour_acts = day_detail[day_detail["source_hour"] == src_hour]

                if hour_acts.empty:
                    detail_rows.append({
                        "Layout": layout, "Layout Type": lt, "Date": dt,
                        "Source Hour": src_hour, "Regular Vol": reg_vol, "Vol Vol": vol_vol,
                        "Activity": "(none)", "Fraction": 1, "Hour Delta": 0,
                        "Target Hour": src_hour, "Activity MH": 0,
                        "Target in Night?": "Y" if src_hour in night_hours else "",
                    })
                else:
                    for _, act_row in hour_acts.iterrows():
                        tgt = int(act_row["target_hour"])
                        detail_rows.append({
                            "Layout": layout, "Layout Type": lt, "Date": dt,
                            "Source Hour": src_hour, "Regular Vol": reg_vol, "Vol Vol": vol_vol,
                            "Activity": act_row["activity"],
                            "Fraction": round(act_row["fraction"], 4),
                            "Hour Delta": int(act_row["offset_hours"]),
                            "Target Hour": tgt,
                            "Activity MH": round(act_row["manhours"], 4),
                            "Target in Night?": "Y" if tgt in night_hours else "",
                        })

    pd.DataFrame(detail_rows).to_excel(writer, sheet_name="4-Activity Detail", index=False)

    # ── Sheet 5: Shifted Hourly MH Profile ──
    shifted_pivot = ncr_shifted.pivot_table(
        index=["layout_name", "Layout Type", "Date of created"],
        columns="hour",
        values="total_mh",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    shifted_pivot.to_excel(writer, sheet_name="5-Shifted Hourly MH", index=False)

    # ── Sheet 6: Simple (unshifted) Hourly MH Profile ──
    simple_pivot = simple_ncr.pivot_table(
        index=["layout_name", "Layout Type", "Date of created"],
        columns="hour",
        values="total_mh",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    simple_pivot.to_excel(writer, sheet_name="6-Simple Hourly MH", index=False)

    # ── Sheet 7: Night Shift Comparison ──
    night_shifted_df = ncr_shifted[ncr_shifted["hour"].isin(night_hours)]
    night_simple_df = simple_ncr[simple_ncr["hour"].isin(night_hours)]

    summary_rows = []
    for layout in sorted(set(night_shifted_df["layout_name"].unique()) | set(night_simple_df["layout_name"].unique())):
        lt_val = ""
        # Shifted peak
        ls = night_shifted_df[night_shifted_df["layout_name"] == layout]
        if not ls.empty:
            idx = ls["total_mh"].idxmax()
            s_peak_mh = ls.loc[idx, "total_mh"]
            s_peak_hour = int(ls.loc[idx, "hour"])
            s_peak_date = ls.loc[idx, "Date of created"]
            lt_val = ls.loc[idx, "Layout Type"]
        else:
            s_peak_mh, s_peak_hour, s_peak_date = 0, "-", "-"

        # Simple peak
        lsimple = night_simple_df[night_simple_df["layout_name"] == layout]
        if not lsimple.empty:
            idx2 = lsimple["total_mh"].idxmax()
            simple_peak_mh = lsimple.loc[idx2, "total_mh"]
            simple_peak_hour = int(lsimple.loc[idx2, "hour"])
            if not lt_val:
                lt_val = lsimple.loc[idx2, "Layout Type"]
        else:
            simple_peak_mh, simple_peak_hour = 0, "-"

        perm_shifted = int(np.ceil(s_peak_mh * 0.90))
        flex_shifted = int(np.ceil(s_peak_mh * 0.10 / 0.80))
        perm_simple = int(np.ceil(simple_peak_mh * 0.90))
        flex_simple = int(np.ceil(simple_peak_mh * 0.10 / 0.80))

        summary_rows.append({
            "Layout": layout,
            "Layout Type": lt_val,
            "Simple Peak MH": round(simple_peak_mh, 2),
            "Simple Peak Hour": simple_peak_hour,
            "Simple Heads (P+F)": f"{perm_simple}+{flex_simple}={perm_simple+flex_simple}",
            "Shifted Peak MH": round(s_peak_mh, 2),
            "Shifted Peak Hour": s_peak_hour,
            "Shifted Peak Date": s_peak_date,
            "Shifted Heads (P+F)": f"{perm_shifted}+{flex_shifted}={perm_shifted+flex_shifted}",
            "MH Diff": round(s_peak_mh - simple_peak_mh, 2),
            "MH Diff %": f"{(s_peak_mh - simple_peak_mh) / simple_peak_mh * 100:.1f}%" if simple_peak_mh > 0 else "-",
        })

    summary_df = pd.DataFrame(summary_rows)
    # Add totals
    totals = {
        "Layout": "TOTAL",
        "Layout Type": "",
        "Simple Peak MH": summary_df["Simple Peak MH"].sum(),
        "Simple Peak Hour": "",
        "Simple Heads (P+F)": "",
        "Shifted Peak MH": summary_df["Shifted Peak MH"].sum(),
        "Shifted Peak Hour": "",
        "Shifted Peak Date": "",
        "Shifted Heads (P+F)": "",
        "MH Diff": round(summary_df["Shifted Peak MH"].sum() - summary_df["Simple Peak MH"].sum(), 2),
        "MH Diff %": f"{(summary_df['Shifted Peak MH'].sum() - summary_df['Simple Peak MH'].sum()) / summary_df['Simple Peak MH'].sum() * 100:.1f}%",
    }
    summary_df = pd.concat([summary_df, pd.DataFrame([totals])], ignore_index=True)
    summary_df.to_excel(writer, sheet_name="7-Night Shift Compare", index=False)

buf.seek(0)
out_path = "NCR_Bilaspur_Night_Working.xlsx"
with open(out_path, "wb") as f:
    f.write(buf.getvalue())
print(f"Written: {out_path} ({len(buf.getvalue()):,} bytes)")

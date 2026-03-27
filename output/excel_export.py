"""Export staffing plan as downloadable Excel workbook with shift breakdown."""

import io
import pandas as pd
from config import SHIFTS


def generate_excel(dc_summary, staffing_df, lt_summary, dc_shift_summary, peak_days):
    """Generate an in-memory Excel workbook with summary + per-DC sheets + shift details.

    Returns bytes (ready for st.download_button).
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # ── Network Summary sheet ──
        summary = dc_summary.copy()
        # Rename raw column names to display names
        summary = summary.rename(columns={
            "layouts_count": "Layouts",
            "total_perm": "Permanent",
            "total_flex": "Flex",
            "total_heads": "Total Heads",
        })
        summary["Peak Days"] = summary["DC"].map(
            lambda dc: ", ".join(str(d) for d in peak_days.get(dc, []))
        )
        # Add Avg Daily Vol and Productivity
        if "avg_daily_vol" in staffing_df.columns:
            dc_vol = (
                staffing_df.groupby("DC")["avg_daily_vol"].sum().reset_index()
                .rename(columns={"avg_daily_vol": "Avg Daily Vol"})
            )
            dc_vol["Avg Daily Vol"] = dc_vol["Avg Daily Vol"].round(0).astype(int)
            summary = summary.merge(dc_vol, on="DC", how="left")
            summary["Avg Daily Vol"] = summary["Avg Daily Vol"].fillna(0).astype(int)
            dc_heads_col = staffing_df.groupby("DC")["total_heads"].sum().reset_index()
            dc_heads_col.columns = ["DC", "_total_heads"]
            summary = summary.merge(dc_heads_col, on="DC", how="left")
            summary["Productivity"] = (
                summary["Avg Daily Vol"] / summary["_total_heads"].replace(0, float("nan"))
            ).round(1).fillna(0)
            summary.drop(columns=["_total_heads"], inplace=True, errors="ignore")

        out_cols = ["DC", "Layouts", "Permanent", "Flex", "Total Heads"]
        if "Avg Daily Vol" in summary.columns:
            out_cols += ["Avg Daily Vol", "Productivity"]
        out_cols += ["Peak Days"]
        final_cols = [c for c in out_cols if c in summary.columns]
        summary = summary[final_cols]
        summary.to_excel(writer, sheet_name="Network Summary", index=False)

        # ── Network Shift Summary ──
        net_shift = (
            dc_shift_summary.groupby("shift")
            .agg(perm=("perm_heads", "sum"), flex=("flex_heads", "sum"), total=("total_heads", "sum"))
            .reset_index()
        )
        net_shift.columns = ["Shift", "Permanent", "Flex", "Total"]
        net_shift.to_excel(writer, sheet_name="Network by Shift", index=False)

        # ── DC x Shift Summary ──
        dc_shift_display = dc_shift_summary.copy()
        dc_shift_display.columns = ["DC", "Shift", "Permanent", "Flex", "Total"]
        dc_shift_display.to_excel(writer, sheet_name="DC x Shift", index=False)

        # ── Per-DC detail sheets (all shifts combined) ──
        for dc in sorted(staffing_df["DC"].unique()):
            dc_data = staffing_df[staffing_df["DC"] == dc]

            # Combined across shifts
            agg_dict = {
                "peak_mh": ("peak_mh", "sum"),
                "perm": ("perm_heads", "sum"),
                "flex": ("flex_heads", "sum"),
                "total": ("total_heads", "sum"),
            }
            if "avg_daily_vol" in dc_data.columns:
                agg_dict["avg_daily_vol"] = ("avg_daily_vol", "sum")

            combined = (
                dc_data.groupby(["layout_name", "Layout Type"])
                .agg(**agg_dict)
                .reset_index()
            )

            col_rename = {
                "layout_name": "Layout",
                "Layout Type": "Layout Type",
                "peak_mh": "Total Peak MH",
                "perm": "Permanent",
                "flex": "Flex",
                "total": "Total",
            }
            if "avg_daily_vol" in combined.columns:
                col_rename["avg_daily_vol"] = "Avg Daily Vol"

            combined = combined.rename(columns=col_rename)
            combined["Total Peak MH"] = combined["Total Peak MH"].round(2)
            if "Avg Daily Vol" in combined.columns:
                combined["Avg Daily Vol"] = combined["Avg Daily Vol"].round(0).astype(int)
                combined["Productivity"] = (
                    combined["Avg Daily Vol"] / combined["Total"].replace(0, float("nan"))
                ).round(1).fillna(0)

            combined = combined.sort_values("Total", ascending=False)

            # Add per-shift columns
            for shift_name in SHIFTS:
                shift_data = dc_data[dc_data["shift"] == shift_name][["layout_name", "total_heads"]].copy()
                shift_data.columns = ["Layout", f"{shift_name}"]
                combined = combined.merge(shift_data, on="Layout", how="left")
                combined[f"{shift_name}"] = combined[f"{shift_name}"].fillna(0).astype(int)

            # Add totals row
            totals = {"Layout": "TOTAL", "Layout Type": "", "Total Peak MH": combined["Total Peak MH"].sum()}
            for col in ["Permanent", "Flex", "Total"] + [s for s in SHIFTS]:
                totals[col] = combined[col].sum()
            if "Avg Daily Vol" in combined.columns:
                totals["Avg Daily Vol"] = combined["Avg Daily Vol"].sum()
                totals["Productivity"] = (
                    round(totals["Avg Daily Vol"] / totals["Total"], 1)
                    if totals["Total"] > 0 else 0
                )
            combined = pd.concat([combined, pd.DataFrame([totals])], ignore_index=True)

            sheet_name = dc[:31]
            combined.to_excel(writer, sheet_name=sheet_name, index=False)

        # ── Layout Type Summary sheet ──
        if not lt_summary.empty and "Layout Type" in lt_summary.columns:
            lt_display = lt_summary[["DC", "Layout Type", "layout_count", "total_peak_mh", "perm_heads", "flex_heads", "total_heads"]].copy()
            lt_display.columns = ["DC", "Layout Type", "Layouts", "Total Peak MH", "Permanent", "Flex", "Total"]
            lt_display["Total Peak MH"] = lt_display["Total Peak MH"].round(2)
            lt_display.to_excel(writer, sheet_name="By Layout Type", index=False)

    buf.seek(0)
    return buf.getvalue()

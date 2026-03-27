"""Page 1: Network Overview — all DCs, total heads, peak days, shift breakdown."""

import streamlit as st
import plotly.express as px
import pandas as pd
from ui.components import kpi_strip, styled_dataframe
from config import SHIFTS


def render(dc_summary, staffing_df, dc_shift_summary, peak_days, load_df):
    """Render the network overview page."""

    st.header("Network Overview")

    # ── KPI Strip ──
    total_perm = int(dc_summary["total_perm"].sum())
    total_flex = int(dc_summary["total_flex"].sum())
    total_heads = int(dc_summary["total_heads"].sum())
    total_dcs = len(dc_summary)
    total_layouts = int(dc_summary["layouts_count"].sum())

    # Network productivity
    if "avg_daily_vol" in staffing_df.columns:
        net_vol = staffing_df["avg_daily_vol"].sum()
        net_prod = round(net_vol / total_heads, 1) if total_heads > 0 else 0
    else:
        net_vol = 0
        net_prod = 0

    kpi_strip([
        {"label": "Total DCs", "value": total_dcs},
        {"label": "Total Layouts", "value": total_layouts},
        {"label": "Permanent Heads", "value": f"{total_perm:,}"},
        {"label": "Flex Heads", "value": f"{total_flex:,}"},
        {"label": "Total Heads", "value": f"{total_heads:,}"},
        {"label": "Productivity", "value": f"{net_prod:,}"},
    ])

    st.divider()

    # ── Network Shift Summary ──
    st.subheader("Network Headcount by Shift")
    net_shift = (
        dc_shift_summary.groupby("shift")
        .agg(perm=("perm_heads", "sum"), flex=("flex_heads", "sum"), total=("total_heads", "sum"))
        .reset_index()
    )
    net_shift.columns = ["Shift", "Permanent", "Flex", "Total"]
    # Add totals row
    totals_row = pd.DataFrame([{
        "Shift": "TOTAL",
        "Permanent": net_shift["Permanent"].sum(),
        "Flex": net_shift["Flex"].sum(),
        "Total": net_shift["Total"].sum(),
    }])
    net_shift = pd.concat([net_shift, totals_row], ignore_index=True)
    st.dataframe(net_shift, use_container_width=True, hide_index=True)

    st.divider()

    # ── DC Summary Table ──
    display_df = dc_summary.copy()
    display_df["Peak Days"] = display_df["DC"].map(
        lambda dc: ", ".join(str(d) for d in peak_days.get(dc, []))
    )
    # Add productivity from staffing_df (which has avg_daily_vol)
    if "avg_daily_vol" in staffing_df.columns:
        dc_prod = (
            staffing_df.groupby("DC")
            .agg(avg_daily_vol=("avg_daily_vol", "sum"), total_heads=("total_heads", "sum"))
            .reset_index()
        )
        dc_prod["Productivity"] = (dc_prod["avg_daily_vol"] / dc_prod["total_heads"].replace(0, float("nan"))).round(1).fillna(0)
        display_df = display_df.merge(dc_prod[["DC", "avg_daily_vol", "Productivity"]], on="DC", how="left")
        display_df["avg_daily_vol"] = display_df["avg_daily_vol"].fillna(0).round(0).astype(int)
        display_df["Productivity"] = display_df["Productivity"].fillna(0)
        display_df.columns = ["DC", "Layouts", "Permanent", "Flex", "Total Heads", "Peak Days", "Avg Daily Vol", "Productivity"]
        # Add TOTAL row
        _tot_vol = display_df["Avg Daily Vol"].sum()
        _tot_heads = display_df["Total Heads"].sum()
        totals_row = pd.DataFrame([{
            "DC": "TOTAL",
            "Layouts": display_df["Layouts"].sum(),
            "Permanent": display_df["Permanent"].sum(),
            "Flex": display_df["Flex"].sum(),
            "Total Heads": _tot_heads,
            "Peak Days": "",
            "Avg Daily Vol": _tot_vol,
            "Productivity": round(_tot_vol / _tot_heads, 1) if _tot_heads > 0 else 0,
        }])
        display_df = pd.concat([display_df, totals_row], ignore_index=True)
    else:
        display_df.columns = ["DC", "Layouts", "Permanent", "Flex", "Total Heads", "Peak Days"]
    styled_dataframe(display_df, title="Staffing Plan by DC (All Shifts)", height=600)

    st.divider()

    # ── Bar Chart: Headcount by DC (stacked perm/flex) ──
    st.subheader("Headcount by DC")
    chart_df = dc_summary[["DC", "total_perm", "total_flex"]].copy()
    chart_df = chart_df.melt(id_vars="DC", var_name="Type", value_name="Heads")
    chart_df["Type"] = chart_df["Type"].map({"total_perm": "Permanent", "total_flex": "Flex"})

    fig = px.bar(
        chart_df,
        x="DC",
        y="Heads",
        color="Type",
        barmode="stack",
        color_discrete_map={"Permanent": "#2563eb", "Flex": "#f59e0b"},
        height=500,
    )
    fig.update_layout(xaxis_tickangle=-45, xaxis_title="", yaxis_title="Headcount")
    st.plotly_chart(fig, use_container_width=True)

    # ── Bar Chart: Headcount by DC by Shift ──
    st.subheader("Headcount by DC by Shift")
    shift_chart = dc_shift_summary.copy()
    fig2 = px.bar(
        shift_chart,
        x="DC",
        y="total_heads",
        color="shift",
        barmode="group",
        height=500,
        labels={"total_heads": "Total Heads", "shift": "Shift"},
    )
    fig2.update_layout(xaxis_tickangle=-45, xaxis_title="")
    st.plotly_chart(fig2, use_container_width=True)

    # ── Heatmap: DC x Hour ──
    st.subheader("Hourly Manhours Heatmap (All DCs)")
    if "hour" in load_df.columns and "DC" in load_df.columns:
        heatmap_data = (
            load_df.groupby(["DC", "hour"])["total_mh"]
            .sum()
            .reset_index()
            .pivot(index="DC", columns="hour", values="total_mh")
            .fillna(0)
        )
        fig3 = px.imshow(
            heatmap_data,
            labels=dict(x="Hour", y="DC", color="Total MH"),
            aspect="auto",
            color_continuous_scale="YlOrRd",
            height=max(400, len(heatmap_data) * 18),
        )
        fig3.update_layout(xaxis_title="Hour of Day", yaxis_title="")
        st.plotly_chart(fig3, use_container_width=True)

"""Page 2: DC Deep Dive — layout breakdown by shift, hourly curves."""

import streamlit as st
import plotly.express as px
import pandas as pd
from ui.components import kpi_strip, styled_dataframe
from config import SHIFTS


def render(staffing_df, lt_summary, load_df, peak_days, selected_dc, load_df_preship=None):
    """Render the DC deep-dive page for a single DC."""

    st.header(f"DC Detail: {selected_dc}")

    dc_staff_all = staffing_df[staffing_df["DC"] == selected_dc].copy()
    dc_lt = lt_summary[lt_summary["DC"] == selected_dc].copy()

    if dc_staff_all.empty:
        st.warning(f"No staffing data for {selected_dc}")
        return

    # ── Layout Type filter (affects everything) ──
    dc_load_all = load_df[load_df["DC"] == selected_dc].copy()
    all_layout_types = sorted(dc_load_all["Layout Type"].unique()) if not dc_load_all.empty else []
    selected_lt = st.multiselect(
        "Filter by Layout Type",
        options=all_layout_types,
        default=all_layout_types,
        key="lt_filter_proc",
    )

    # Apply filter to staffing, load, and lt_summary
    if selected_lt:
        dc_staff = dc_staff_all[dc_staff_all["Layout Type"].isin(selected_lt)].copy()
        dc_load = dc_load_all[dc_load_all["Layout Type"].isin(selected_lt)].copy()
        dc_lt = dc_lt[dc_lt["Layout Type"].isin(selected_lt)].copy()
    else:
        dc_staff = dc_staff_all.copy()
        dc_load = dc_load_all.copy()

    # ── KPI Strip (totals across all shifts) ──
    kpi_strip([
        {"label": "Layouts", "value": dc_staff["layout_name"].nunique()},
        {"label": "Permanent", "value": int(dc_staff["perm_heads"].sum())},
        {"label": "Flex", "value": int(dc_staff["flex_heads"].sum())},
        {"label": "Total Heads", "value": int(dc_staff["total_heads"].sum())},
        {"label": "Peak Days", "value": len(peak_days.get(selected_dc, []))},
    ])

    st.divider()

    # ── Shift-level summary for this DC ──
    st.subheader("Headcount by Shift")
    shift_summary = (
        dc_staff.groupby("shift")
        .agg(perm=("perm_heads", "sum"), flex=("flex_heads", "sum"), total=("total_heads", "sum"))
        .reset_index()
    )
    shift_summary.columns = ["Shift", "Permanent", "Flex", "Total"]
    totals_row = pd.DataFrame([{
        "Shift": "TOTAL",
        "Permanent": shift_summary["Permanent"].sum(),
        "Flex": shift_summary["Flex"].sum(),
        "Total": shift_summary["Total"].sum(),
    }])
    shift_summary = pd.concat([shift_summary, totals_row], ignore_index=True)
    st.dataframe(shift_summary, use_container_width=True, hide_index=True)

    st.divider()

    # ── Layout-level staffing table (per shift) ──
    st.subheader("Layout-Level Staffing by Shift")
    shift_tab_names = list(SHIFTS.keys()) + ["All Shifts Combined"]
    tabs = st.tabs(shift_tab_names)

    for i, shift_name in enumerate(list(SHIFTS.keys())):
        with tabs[i]:
            _cols = ["layout_name", "Layout Type", "peak_mh", "perm_heads", "flex_heads", "total_heads"]
            if "avg_daily_vol" in dc_staff.columns:
                _cols += ["avg_daily_vol", "productivity"]
            shift_data = dc_staff[dc_staff["shift"] == shift_name][_cols].copy()
            _rename = {
                "layout_name": "Layout", "Layout Type": "Layout Type",
                "peak_mh": "Peak MH", "perm_heads": "Permanent",
                "flex_heads": "Flex", "total_heads": "Total",
                "avg_daily_vol": "Avg Daily Vol", "productivity": "Productivity",
            }
            shift_data = shift_data.rename(columns=_rename)
            shift_data["Peak MH"] = shift_data["Peak MH"].round(2)
            if "Avg Daily Vol" in shift_data.columns:
                shift_data["Avg Daily Vol"] = shift_data["Avg Daily Vol"].round(0).astype(int)
            shift_data = shift_data.sort_values("Total", ascending=False)
            # Filter out zero rows
            shift_data = shift_data[shift_data["Total"] > 0]
            if shift_data.empty:
                st.info(f"No staffing needed for {shift_name}")
            else:
                _totals = {
                    "Layout": "TOTAL", "Layout Type": "",
                    "Peak MH": shift_data["Peak MH"].sum(),
                    "Permanent": shift_data["Permanent"].sum(),
                    "Flex": shift_data["Flex"].sum(),
                    "Total": shift_data["Total"].sum(),
                }
                if "Avg Daily Vol" in shift_data.columns:
                    _totals["Avg Daily Vol"] = shift_data["Avg Daily Vol"].sum()
                    _total_heads = shift_data["Total"].sum()
                    _totals["Productivity"] = round(_totals["Avg Daily Vol"] / _total_heads, 1) if _total_heads > 0 else 0
                totals = pd.DataFrame([_totals])
                shift_data = pd.concat([shift_data, totals], ignore_index=True)
                st.dataframe(shift_data, use_container_width=True, hide_index=True, height=400)

    # All shifts combined tab
    with tabs[-1]:
        _agg_dict = {
            "peak_mh": ("peak_mh", "sum"),
            "perm": ("perm_heads", "sum"),
            "flex": ("flex_heads", "sum"),
            "total": ("total_heads", "sum"),
        }
        if "avg_daily_vol" in dc_staff.columns:
            _agg_dict["avg_daily_vol"] = ("avg_daily_vol", "sum")
        combined = (
            dc_staff.groupby(["layout_name", "Layout Type"])
            .agg(**_agg_dict)
            .reset_index()
        )
        combined = combined.rename(columns={
            "layout_name": "Layout", "peak_mh": "Total Peak MH",
            "perm": "Permanent", "flex": "Flex", "total": "Total",
            "avg_daily_vol": "Avg Daily Vol",
        })
        combined["Total Peak MH"] = combined["Total Peak MH"].round(2)
        if "Avg Daily Vol" in combined.columns:
            combined["Avg Daily Vol"] = combined["Avg Daily Vol"].round(0).astype(int)
            combined["Productivity"] = (combined["Avg Daily Vol"] / combined["Total"].replace(0, float("nan"))).round(1).fillna(0)
        combined = combined.sort_values("Total", ascending=False)
        _totals = {
            "Layout": "TOTAL", "Layout Type": "",
            "Total Peak MH": combined["Total Peak MH"].sum(),
            "Permanent": combined["Permanent"].sum(),
            "Flex": combined["Flex"].sum(),
            "Total": combined["Total"].sum(),
        }
        if "Avg Daily Vol" in combined.columns:
            _totals["Avg Daily Vol"] = combined["Avg Daily Vol"].sum()
            _totals["Productivity"] = round(_totals["Avg Daily Vol"] / _totals["Total"], 1) if _totals["Total"] > 0 else 0
        totals = pd.DataFrame([_totals])
        combined = pd.concat([combined, totals], ignore_index=True)
        styled_dataframe(combined, height=400)

    st.divider()

    # ── Roll-up by Layout Type ──
    if not dc_lt.empty:
        lt_display = dc_lt[["Layout Type", "layout_count", "total_peak_mh", "perm_heads", "flex_heads", "total_heads"]].copy()
        lt_display.columns = ["Layout Type", "Layouts", "Total Peak MH", "Permanent", "Flex", "Total"]
        lt_display["Total Peak MH"] = lt_display["Total Peak MH"].round(2)
        styled_dataframe(lt_display, title="Staffing by Layout Type (All Shifts)")

    st.divider()

    # ── Daily Load bar chart ──
    # Use pre-shift data for actual shipment volumes
    dc_preship = None
    if load_df_preship is not None:
        dc_preship = load_df_preship[load_df_preship["DC"] == selected_dc]
        if selected_lt:
            dc_preship = dc_preship[dc_preship["Layout Type"].isin(selected_lt)]
    if dc_preship is not None and not dc_preship.empty:
        vol_source = dc_preship
    else:
        vol_source = dc_load

    if not vol_source.empty:
        st.subheader("Daily Shipment Volume")
        if "regular_volume" in vol_source.columns:
            daily_vol = vol_source.groupby("Date of created")["regular_volume"].sum().reset_index()
            daily_vol.columns = ["Date", "Shipments"]
        elif "Total awb_number" in vol_source.columns:
            daily_vol = vol_source.groupby("Date of created")["Total awb_number"].sum().reset_index()
            daily_vol.columns = ["Date", "Shipments"]
        else:
            daily_vol = vol_source.groupby("Date of created")["total_mh"].sum().reset_index()
            daily_vol.columns = ["Date", "Shipments"]
        daily_vol["Date"] = daily_vol["Date"].astype(str)
        daily_vol = daily_vol.sort_values("Date")

        fig_daily = px.bar(
            daily_vol, x="Date", y="Shipments",
            labels={"Date": "Date", "Shipments": "Daily Shipments"},
            height=300,
            text="Shipments",
        )
        fig_daily.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
        fig_daily.update_layout(yaxis_title="Shipments", xaxis_tickangle=-45)

        # Mark peak days
        dc_peak_dates_str = [str(d) for d in peak_days.get(selected_dc, [])]
        colors = ["#ef4444" if d in dc_peak_dates_str else "#6366f1" for d in daily_vol["Date"]]
        fig_daily.update_traces(marker_color=colors)
        st.plotly_chart(fig_daily, use_container_width=True)
        st.caption("🔴 Red = Peak days used for staffing calculation")

    st.divider()

    # ── Hourly manhours curve across peak days ──
    st.subheader("Hourly Manhours (Peak Days)")
    dc_peak_dates = peak_days.get(selected_dc, [])

    if not dc_load.empty and dc_peak_dates:
        peak_load = dc_load[dc_load["Date of created"].isin(dc_peak_dates)]
        hourly_by_date = (
            peak_load.groupby(["Date of created", "hour"])["total_mh"]
            .sum()
            .reset_index()
        )
        hourly_by_date["Date of created"] = hourly_by_date["Date of created"].astype(str)

        # Add shift bands as background
        fig = px.line(
            hourly_by_date,
            x="hour",
            y="total_mh",
            color="Date of created",
            markers=True,
            labels={"hour": "Hour of Day", "total_mh": "Total Manhours", "Date of created": "Date"},
            height=400,
        )

        # Add shift region shading
        shift_colors = {"Shift 1 (Night)": "rgba(99,102,241,0.08)", "Shift 2 (Morning)": "rgba(34,197,94,0.08)", "Shift 3 (Afternoon)": "rgba(249,115,22,0.08)"}
        for shift_name, shift_def in SHIFTS.items():
            hours = shift_def["exclusive"]
            if hours:
                fig.add_vrect(
                    x0=min(hours) - 0.5, x1=max(hours) + 0.5,
                    fillcolor=shift_colors.get(shift_name, "rgba(0,0,0,0.05)"),
                    layer="below", line_width=0,
                    annotation_text=shift_name.split("(")[1].rstrip(")"),
                    annotation_position="top left",
                    annotation_font_size=10,
                )

        # Add staffing recommendation dotted lines per shift
        shift_line_colors = {
            "Shift 1 (Night)": "rgb(99,102,241)",
            "Shift 2 (Morning)": "rgb(34,197,94)",
            "Shift 3 (Afternoon)": "rgb(249,115,22)",
        }
        for shift_name, shift_def in SHIFTS.items():
            shift_staff = dc_staff[dc_staff["shift"] == shift_name]
            if shift_staff.empty:
                continue
            staff_mh = shift_staff["peak_mh"].sum()
            hours = shift_def["exclusive"]
            if not hours or staff_mh <= 0:
                continue
            short_name = shift_name.split("(")[1].rstrip(")")
            color = shift_line_colors.get(shift_name, "gray")

            # Split night shift (wraps around midnight) into two segments
            low = [h for h in hours if h <= 5]
            high = [h for h in hours if h >= 21]
            if low and high:
                # Two segments: 21-23 and 0-5
                for seg in [high, low]:
                    fig.add_shape(
                        type="line",
                        x0=min(seg) - 0.5, x1=max(seg) + 0.5,
                        y0=staff_mh, y1=staff_mh,
                        line=dict(color=color, width=2, dash="dot"),
                    )
                # Label on the early-morning segment
                fig.add_annotation(
                    x=max(low) + 0.3, y=staff_mh,
                    text=f"{short_name}: {staff_mh:.0f} MH",
                    showarrow=False,
                    font=dict(size=9, color=color),
                    xanchor="right", yanchor="bottom",
                )
            else:
                # Contiguous shift
                fig.add_shape(
                    type="line",
                    x0=min(hours) - 0.5, x1=max(hours) + 0.5,
                    y0=staff_mh, y1=staff_mh,
                    line=dict(color=color, width=2, dash="dot"),
                )
                fig.add_annotation(
                    x=max(hours) + 0.3, y=staff_mh,
                    text=f"{short_name}: {staff_mh:.0f} MH",
                    showarrow=False,
                    font=dict(size=9, color=color),
                    xanchor="right", yanchor="bottom",
                )

        fig.update_layout(xaxis=dict(dtick=1))
        st.plotly_chart(fig, use_container_width=True)

    # ── Stacked area: manhours by layout type ──
    st.subheader("Manhours by Layout Type (All Days)")
    if not dc_load.empty:
        lt_hourly = (
            dc_load.groupby(["Layout Type", "hour"])["total_mh"]
            .sum()
            .reset_index()
        )
        fig2 = px.area(
            lt_hourly,
            x="hour",
            y="total_mh",
            color="Layout Type",
            labels={"hour": "Hour of Day", "total_mh": "Total Manhours"},
            height=450,
        )
        fig2.update_layout(xaxis=dict(dtick=1))
        st.plotly_chart(fig2, use_container_width=True)

    # ── Daily Flex Profile ──
    st.divider()
    st.subheader("Daily Flex Requirement")
    from core.staffing_calculator import compute_daily_flex
    daily_flex = compute_daily_flex(dc_load, dc_staff)
    if not daily_flex.empty:
        dc_flex = daily_flex[daily_flex["DC"] == selected_dc]
        if not dc_flex.empty:
            # Summary metrics
            col1, col2, col3, col4 = st.columns(4)
            total_perm = dc_staff["perm_heads"].sum()
            max_flex = dc_flex.groupby("Date of created")["flex_needed"].sum().max()
            avg_flex = dc_flex.groupby("Date of created")["flex_needed"].sum().mean()
            zero_flex_days = (dc_flex.groupby("Date of created")["flex_needed"].sum() == 0).sum()
            col1.metric("Permanent (Fixed)", f"{int(total_perm)}")
            col2.metric("Max Flex (Worst Day)", f"{int(max_flex)}")
            col3.metric("Avg Daily Flex", f"{avg_flex:.0f}")
            col4.metric("Zero-Flex Days", f"{int(zero_flex_days)}")

            # Stacked bar: perm (constant) + flex (variable) per day
            daily_agg = dc_flex.groupby("Date of created").agg(
                flex_needed=("flex_needed", "sum"),
                day_mh=("day_mh", "sum"),
            ).reset_index()
            daily_agg["perm"] = int(total_perm)
            daily_agg["Date"] = daily_agg["Date of created"].astype(str)

            import plotly.graph_objects as go
            fig_flex = go.Figure()
            fig_flex.add_trace(go.Bar(
                x=daily_agg["Date"], y=daily_agg["perm"],
                name="Permanent", marker_color="#6366f1",
            ))
            fig_flex.add_trace(go.Bar(
                x=daily_agg["Date"], y=daily_agg["flex_needed"],
                name="Flex", marker_color="#f97316",
            ))
            fig_flex.update_layout(
                barmode="stack", height=350,
                yaxis_title="Headcount",
                xaxis_tickangle=-45,
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )

            # Mark peak days
            dc_peak_str = [str(d) for d in peak_days.get(selected_dc, [])]
            shapes = []
            for d in dc_peak_str:
                if d in daily_agg["Date"].values:
                    shapes.append(dict(
                        type="line", x0=d, x1=d,
                        y0=0, y1=1, yref="paper",
                        line=dict(color="red", width=1, dash="dot"),
                    ))
            fig_flex.update_layout(shapes=shapes)

            st.plotly_chart(fig_flex, use_container_width=True)
            st.caption("🔴 Dotted red lines = Peak days. Permanent is fixed; Flex varies daily based on load.")

            # Per-shift breakdown table
            with st.expander("Daily Flex by Shift"):
                pivot = dc_flex.pivot_table(
                    index="Date of created",
                    columns="shift",
                    values=["day_mh", "perm_capacity", "flex_needed"],
                    aggfunc="sum",
                ).round(1)
                pivot.columns = [f"{v} ({s.split('(')[1].rstrip(')')})" for v, s in pivot.columns]
                st.dataframe(pivot, use_container_width=True)

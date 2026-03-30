"""Shadowfax DC Manpower Planning Tool — Streamlit Entry Point.

Two separate pipelines:
  1. Processing Manpower — derived from processing volume × activity rates × time-shifts
  2. Dock Manpower — actual IB/OB/CrossDock from dock file + derived OSC Bag Sorting/Bag Staging
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd
import numpy as np
from datetime import date

from config import (
    DEFAULT_PEAK_DAYS,
    DEFAULT_FLEX_PCT,
    DEFAULT_FLEX_EFFICIENCY,
    DEFAULT_TOP_N_HOURS,
    SHIFTS,
    ALL_DOCK_ACTIVITIES,
    ACTUAL_DOCK_ACTIVITIES,
    DERIVED_DOCK_ACTIVITIES,
)
from core.load_reader import read_load_files
from core.template_reader import load_all_template
from core.validator import validate_joins
from core.volumetric_splitter import apply_volumetric_split
from core.manhours_calculator import add_layout_type
from core.time_shifter import apply_time_offsets, compute_activity_detail
from core.dock_reader import read_dock_file, compute_dock_mh, compute_derived_dock_mh
from core.peak_selector import select_peak_days, compute_shift_peak_mh
from core.staffing_calculator import (
    compute_shift_headcount,
    rollup_by_dc,
    rollup_by_dc_shift,
    rollup_by_layout_type,
)
from output.excel_export import generate_excel
from output.dc_working_export import generate_dc_working_excel
from core.actual_productivity import read_actual_productivity

# ── Page Config ──
st.set_page_config(
    page_title="Shadowfax DC Manpower Planner",
    page_icon="🚛",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Password Protection ──
import os
try:
    _app_password = st.secrets.get("APP_PASSWORD", os.environ.get("APP_PASSWORD", ""))
except Exception:
    _app_password = os.environ.get("APP_PASSWORD", "")
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 DC Manpower Planning Tool")
    pwd = st.text_input("Enter password to access the tool", type="password")
    if pwd:
        if pwd == _app_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

# ── Branding ──
import base64
_logo_path = Path(__file__).resolve().parent / "assets" / "shadowfax_logo.png"
if _logo_path.exists():
    _logo_b64 = base64.b64encode(_logo_path.read_bytes()).decode()
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:18px;margin-bottom:8px;">
        <img src="data:image/png;base64,{_logo_b64}" style="height:48px;">
        <span style="font-size:28px;font-weight:700;color:#00836d;">DC Manpower Planning Tool</span>
    </div>
    """, unsafe_allow_html=True)
else:
    st.title("DC Manpower Planning Tool")

# Shadowfax theme
st.markdown("""
<style>
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #00836d 0%, #005a4a 100%); }
    [data-testid="stSidebar"] * { color: white !important; }
    [data-testid="stSidebar"] .stSlider label { color: white !important; }
    div[data-testid="metric-container"] {
        background: #f0faf7; border-left: 4px solid #00836d; padding: 12px; border-radius: 6px;
    }
    div[data-testid="metric-container"] label { color: #005a4a !important; font-weight: 600; }
    div[data-testid="metric-container"] [data-testid="stMetricValue"] { color: #00836d !important; }
    .stTabs [data-baseweb="tab"] { font-weight: 600; }
    .stTabs [aria-selected="true"] { border-bottom-color: #c8d600 !important; color: #00836d !important; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════
if _logo_path.exists():
    # White background behind logo so it looks clean on dark theme
    _logo_b64_sidebar = base64.b64encode(_logo_path.read_bytes()).decode()
    st.sidebar.markdown(
        f'<div style="background:white; padding:12px 16px; border-radius:8px; margin-bottom:12px;">'
        f'<img src="data:image/png;base64,{_logo_b64_sidebar}" style="width:160px;">'
        f'</div>',
        unsafe_allow_html=True,
    )
st.sidebar.header("Planning Parameters")

# Date range
st.sidebar.subheader("Date Range")
_all_load = read_load_files()
_all_dates = sorted(_all_load["Date of created"].unique())
_min_date = _all_dates[0] if _all_dates else date(2026, 3, 8)
_max_date = _all_dates[-1] if _all_dates else date(2026, 3, 24)
date_start = st.sidebar.date_input("Start Date", value=_min_date, min_value=_min_date, max_value=_max_date)
date_end = st.sidebar.date_input("End Date", value=_max_date, min_value=_min_date, max_value=_max_date)

if date_start > date_end:
    st.sidebar.error("Start date must be before end date.")
    st.stop()

# Planning parameters
st.sidebar.subheader("Parameters")
peak_days_count = st.sidebar.slider("Peak Days Count", min_value=2, max_value=7, value=DEFAULT_PEAK_DAYS)
top_n_hours = st.sidebar.slider("Avg Top N Hours per Shift", min_value=1, max_value=8, value=DEFAULT_TOP_N_HOURS,
                                 help="Number of top hours to average within each shift per day")
flex_pct = st.sidebar.slider("Flex %", min_value=0.0, max_value=0.30, value=DEFAULT_FLEX_PCT, step=0.01, format="%.0f%%")
flex_efficiency = st.sidebar.slider("Flex Efficiency", min_value=0.50, max_value=1.00, value=DEFAULT_FLEX_EFFICIENCY, step=0.05, format="%.0f%%")
st.sidebar.caption(f"Peak method: avg of top-{top_n_hours} hours per shift per day, averaged across {peak_days_count} peak days")
exclude_prime = st.sidebar.toggle("Exclude Prime Load", value=False, help="Filter out layouts with 'PRIME' in the name")

# Refresh
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# ══════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════

@st.cache_data(show_spinner="Loading template...")
def cached_load_template():
    return load_all_template()

@st.cache_data(show_spinner="Loading processing data...")
def cached_load_data(start, end):
    return read_load_files(date_start=start, date_end=end)

@st.cache_data(show_spinner="Loading dock data...")
def cached_load_dock(start, end):
    return read_dock_file(date_start=start, date_end=end)

try:
    template = cached_load_template()
except Exception as e:
    st.error(f"Failed to load template: {e}")
    st.stop()

try:
    load_df = cached_load_data(date_start, date_end)
except Exception as e:
    st.error(f"Failed to load processing data: {e}")
    st.stop()

try:
    dock_df = cached_load_dock(date_start, date_end)
except Exception as e:
    st.warning(f"No dock data loaded: {e}")
    dock_df = pd.DataFrame()

if load_df.empty:
    st.warning("No processing load data found for the selected date range.")
    st.stop()

# Validate
validation = validate_joins(load_df, template["layout_mapping"], template["manhours_per_ship"])
if validation["warnings"]:
    with st.expander(f"⚠️ {len(validation['warnings'])} validation warning(s)", expanded=False):
        for w in validation["warnings"]:
            st.warning(w)

# Filter prime
if exclude_prime:
    prime_mask = load_df["layout_name"].str.contains("PRIME", case=False, na=False)
    load_df = load_df[~prime_mask]
    if load_df.empty:
        st.warning("No non-prime data found.")
        st.stop()

# Volumetric split & layout type
load_df = apply_volumetric_split(load_df, template["volumetric_pct"], template["layout_mapping"])
load_df = add_layout_type(load_df, template["layout_mapping"])
load_df_preship = load_df.copy()

# ══════════════════════════════════════════════════════════
# PIPELINE 1: PROCESSING MANPOWER
# ══════════════════════════════════════════════════════════
# Exclude ALL dock activities from processing pipeline
proc_load = apply_time_offsets(load_df, template, exclude_activities=ALL_DOCK_ACTIVITIES)
# Apply XD effort multipliers for cross-dock sites
from core.time_shifter import apply_xd_multipliers
xd_sites = template.get("xd_sites", set())
proc_load = apply_xd_multipliers(proc_load, template, load_df_preship=load_df_preship)
proc_peak_days = select_peak_days(proc_load, peak_days_count=peak_days_count)
proc_shift_peaks, proc_overlap_peaks = compute_shift_peak_mh(proc_load, proc_peak_days, top_n=top_n_hours)
proc_staffing = compute_shift_headcount(
    proc_shift_peaks, proc_overlap_peaks, flex_pct=flex_pct, flex_efficiency=flex_efficiency
)

# Productivity for processing
_proc_peak_flat = set(d for dates in proc_peak_days.values() for d in dates)
_pvol_base = load_df_preship[load_df_preship["Date of created"].isin(_proc_peak_flat)].copy()
_pvol_base["hour"] = _pvol_base["hour"].astype(int)
_svr = []
for sn, sd in SHIFTS.items():
    sv = _pvol_base[_pvol_base["hour"].isin(sd["hours"])]
    dv = sv.groupby(["DC", "layout_name", "Date of created"])["Total awb_number"].sum().reset_index()
    av = dv.groupby(["DC", "layout_name"])["Total awb_number"].mean().reset_index().rename(
        columns={"Total awb_number": "avg_daily_vol"}
    )
    av["shift"] = sn
    _svr.append(av)
if _svr:
    _sv = pd.concat(_svr, ignore_index=True)
    proc_staffing = proc_staffing.merge(_sv, on=["DC", "layout_name", "shift"], how="left")
    proc_staffing["avg_daily_vol"] = proc_staffing["avg_daily_vol"].fillna(0)
else:
    proc_staffing["avg_daily_vol"] = 0
# Compute avg daily flex for realistic productivity
from core.staffing_calculator import compute_daily_flex
_proc_daily_flex = compute_daily_flex(proc_load, proc_staffing, top_n=top_n_hours, flex_efficiency=flex_efficiency)
if not _proc_daily_flex.empty:
    _avg_flex_dc_shift = (
        _proc_daily_flex.groupby(["DC", "shift"])["flex_needed"]
        .mean()
        .reset_index()
        .rename(columns={"flex_needed": "avg_daily_flex"})
    )
    proc_staffing = proc_staffing.merge(_avg_flex_dc_shift, on=["DC", "shift"], how="left")
    proc_staffing["avg_daily_flex"] = proc_staffing["avg_daily_flex"].fillna(0)
else:
    proc_staffing["avg_daily_flex"] = 0

# Effective heads = perm + avg daily flex (not peak flex)
proc_staffing["effective_heads"] = proc_staffing["perm_heads"] + proc_staffing["avg_daily_flex"].round(0).astype(int)
proc_staffing["productivity"] = (
    proc_staffing["avg_daily_vol"] / proc_staffing["effective_heads"].replace(0, float("nan"))
).round(1).fillna(0)

# ══════════════════════════════════════════════════════════
# PIPELINE 2: DOCK MANPOWER
# ══════════════════════════════════════════════════════════
if not dock_df.empty:
    # Actual dock MH (IB, OB, CrossDock) — DC level, not layout level
    actual_dock_mh = compute_dock_mh(dock_df, template["activity_manhours"])

    # Derived dock MH (OSC Bag Sorting, Bag Staging) — from processing volume
    derived_dock_mh = compute_derived_dock_mh(load_df_preship, template)

    # Combine into a unified dock hourly MH at DC level
    # Actual: already at DC level
    actual_agg = actual_dock_mh.groupby(["DC", "Date of created", "hour"]).agg(
        total_mh=("manhours", "sum")
    ).reset_index()
    actual_agg["layout_name"] = "Dock (Actual)"
    actual_agg["Layout Type"] = "Dock"

    # Derived: aggregate to DC level
    if not derived_dock_mh.empty:
        derived_agg = derived_dock_mh.groupby(["DC", "Date of created", "hour"]).agg(
            total_mh=("manhours", "sum")
        ).reset_index()
        derived_agg["layout_name"] = "Dock (Derived)"
        derived_agg["Layout Type"] = "Dock"
        dock_hourly = pd.concat([actual_agg, derived_agg], ignore_index=True)
    else:
        dock_hourly = actual_agg

    dock_hourly["regular_mh"] = dock_hourly["total_mh"]
    dock_hourly["volumetric_mh"] = 0.0

    dock_peak_days = select_peak_days(dock_hourly, peak_days_count=peak_days_count)
    dock_shift_peaks, dock_overlap_peaks = compute_shift_peak_mh(dock_hourly, dock_peak_days, top_n=top_n_hours)
    dock_staffing = compute_shift_headcount(
        dock_shift_peaks, dock_overlap_peaks, flex_pct=flex_pct, flex_efficiency=flex_efficiency
    )
    # Add dock volume for productivity
    if not dock_df.empty:
        _dvol = dock_df.copy()
        _dvol["hour"] = _dvol["hour"].astype(int)
        _dsvr = []
        for sn, sd in SHIFTS.items():
            dsv = _dvol[_dvol["hour"].isin(sd["hours"])]
            ddv = dsv.groupby(["DC", "Date of created"])["volume"].sum().reset_index()
            dav = ddv.groupby("DC")["volume"].mean().reset_index().rename(columns={"volume": "avg_daily_vol"})
            dav["shift"] = sn
            _dsvr.append(dav)
        if _dsvr:
            _dsv = pd.concat(_dsvr, ignore_index=True)
            dock_staffing = dock_staffing.merge(_dsv, on=["DC", "shift"], how="left")
            dock_staffing["avg_daily_vol"] = dock_staffing["avg_daily_vol"].fillna(0)
        else:
            dock_staffing["avg_daily_vol"] = 0
    else:
        dock_staffing["avg_daily_vol"] = 0
    # Avg daily flex for dock
    _dock_daily_flex = compute_daily_flex(dock_hourly, dock_staffing, top_n=top_n_hours, flex_efficiency=flex_efficiency)
    if not _dock_daily_flex.empty:
        _dock_avg_flex = (
            _dock_daily_flex.groupby(["DC", "shift"])["flex_needed"]
            .mean().reset_index().rename(columns={"flex_needed": "avg_daily_flex"})
        )
        dock_staffing = dock_staffing.merge(_dock_avg_flex, on=["DC", "shift"], how="left")
        dock_staffing["avg_daily_flex"] = dock_staffing["avg_daily_flex"].fillna(0)
    else:
        dock_staffing["avg_daily_flex"] = 0
    dock_staffing["effective_heads"] = dock_staffing["perm_heads"] + dock_staffing["avg_daily_flex"].round(0).astype(int)
    dock_staffing["productivity"] = (
        dock_staffing["avg_daily_vol"] / dock_staffing["effective_heads"].replace(0, float("nan"))
    ).round(1).fillna(0)
else:
    dock_staffing = pd.DataFrame()
    dock_hourly = pd.DataFrame()
    dock_peak_days = {}

# ══════════════════════════════════════════════════════════
# XD SITES: Combined dock + processing into single pool
# ══════════════════════════════════════════════════════════
# For XD sites, combine proc + dock hourly MH into one profile,
# then compute staffing on the combined peak — this is true cross-utilization.
proc_staffing["is_xd"] = proc_staffing["DC"].isin(xd_sites)
if not dock_staffing.empty:
    dock_staffing["is_xd"] = dock_staffing["DC"].isin(xd_sites)

# Build combined staffing for XD sites
xd_combined_staffing = pd.DataFrame()
if xd_sites and not dock_hourly.empty:
    # Get XD proc hourly MH (DC-level, summed across layouts)
    xd_proc = proc_load[proc_load["DC"].isin(xd_sites)].copy()
    xd_proc_hourly = (
        xd_proc.groupby(["DC", "Date of created", "hour"])["total_mh"]
        .sum().reset_index()
    )

    # Get XD dock hourly MH (already DC-level)
    xd_dock = dock_hourly[dock_hourly["DC"].isin(xd_sites)].copy()
    xd_dock_hourly = (
        xd_dock.groupby(["DC", "Date of created", "hour"])["total_mh"]
        .sum().reset_index()
    )

    # Combine: sum proc + dock MH per (DC, date, hour)
    xd_combined_hourly = pd.concat([xd_proc_hourly, xd_dock_hourly], ignore_index=True)
    xd_combined_hourly = (
        xd_combined_hourly.groupby(["DC", "Date of created", "hour"])["total_mh"]
        .sum().reset_index()
    )

    # Add dummy layout columns for the staffing pipeline
    xd_combined_hourly["layout_name"] = "XD_Combined"
    xd_combined_hourly["Layout Type"] = "Cross-Dock"

    # Run peak selection and staffing on combined profile
    xd_peak_days = select_peak_days(xd_combined_hourly, peak_days_count=peak_days_count)
    xd_shift_peaks, xd_overlap_peaks = compute_shift_peak_mh(
        xd_combined_hourly, xd_peak_days, top_n=top_n_hours
    )
    xd_combined_staffing = compute_shift_headcount(
        xd_shift_peaks, xd_overlap_peaks, flex_pct=flex_pct, flex_efficiency=flex_efficiency
    )
    xd_combined_staffing["is_xd"] = True

# ══════════════════════════════════════════════════════════
# ROLL-UPS
# ══════════════════════════════════════════════════════════
proc_dc_summary = rollup_by_dc(proc_staffing)
proc_dc_shift = rollup_by_dc_shift(proc_staffing)
proc_lt_summary = rollup_by_layout_type(proc_staffing)

if not dock_staffing.empty:
    dock_dc_summary = rollup_by_dc(dock_staffing)
    dock_dc_shift = rollup_by_dc_shift(dock_staffing)
else:
    dock_dc_summary = pd.DataFrame()
    dock_dc_shift = pd.DataFrame()

# ── DC Selector ──
st.sidebar.subheader("DC Filter")
all_dcs = sorted(set(proc_staffing["DC"].unique()) | (set(dock_staffing["DC"].unique()) if not dock_staffing.empty else set()))
dc_options = ["All DCs"] + all_dcs
selected_dc = st.sidebar.selectbox("Select DC", dc_options)

# ── Exports ──
st.sidebar.subheader("Export")
proc_excel = generate_excel(proc_dc_summary, proc_staffing, proc_lt_summary, proc_dc_shift, proc_peak_days)
st.sidebar.download_button(
    label="📥 Processing Staffing Plan",
    data=proc_excel,
    file_name=f"Processing_Staffing_{date_start}_{date_end}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

if not dock_staffing.empty:
    dock_excel = generate_excel(dock_dc_summary, dock_staffing, pd.DataFrame(), dock_dc_shift, dock_peak_days)
    st.sidebar.download_button(
        label="📥 Dock Staffing Plan",
        data=dock_excel,
        file_name=f"Dock_Staffing_{date_start}_{date_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# Shift info
st.sidebar.subheader("Shift Definitions")
st.sidebar.caption("Shift 1 (Night): 9 PM – 6 AM")
st.sidebar.caption("Shift 2 (Morning): 8 AM – 5 PM")
st.sidebar.caption("Shift 3 (Afternoon): 12 PM – 9 PM")
st.sidebar.caption("Overlap (Shift 2+3): 12 PM – 5 PM")
st.sidebar.caption("Gap: 6 AM – 8 AM (no shift)")

# ══════════════════════════════════════════════════════════
# PAGE RENDERING
# ══════════════════════════════════════════════════════════

if selected_dc == "All DCs":
    tab_proc, tab_dock, tab_combined, tab_roster, tab_ai = st.tabs([
        "📦 Processing Manpower", "🚛 Dock Manpower", "📊 Combined Overview", "📋 Roster Plan", "🤖 AI Assistant"
    ])

    with tab_proc:
        from ui.page_overview import render as render_overview
        render_overview(proc_dc_summary, proc_staffing, proc_dc_shift, proc_peak_days, proc_load)

    with tab_dock:
        st.subheader("Dock Manpower by DC (All Shifts)")
        if not dock_dc_summary.empty:
            from ui.page_overview import render as render_dock_overview
            render_dock_overview(dock_dc_summary, dock_staffing, dock_dc_shift, dock_peak_days, dock_hourly)
        else:
            st.info("No dock data available.")

    with tab_combined:
        st.header("Network Overview")
        # KPI strip for combined
        # Non-XD: proc + dock separate. XD: combined pool.
        _non_xd_proc = proc_staffing[~proc_staffing["DC"].isin(xd_sites)]
        _non_xd_dock = dock_staffing[~dock_staffing["DC"].isin(xd_sites)] if not dock_staffing.empty else pd.DataFrame()
        _non_xd_proc_heads = int(_non_xd_proc["total_heads"].sum()) if not _non_xd_proc.empty else 0
        _non_xd_dock_heads = int(_non_xd_dock["total_heads"].sum()) if not _non_xd_dock.empty else 0
        _xd_heads = int(xd_combined_staffing["total_heads"].sum()) if not xd_combined_staffing.empty else 0

        _c_proc_perm = int(proc_staffing["perm_heads"].sum())
        _c_dock_perm = int(dock_staffing["perm_heads"].sum()) if not dock_staffing.empty else 0
        _c_total_eff = _non_xd_proc_heads + _non_xd_dock_heads + _xd_heads
        _c_total_dcs = len(set(proc_staffing["DC"].unique()) | (set(dock_staffing["DC"].unique()) if not dock_staffing.empty else set()))
        _c_total_layouts = int(proc_dc_summary["layouts_count"].sum()) if "layouts_count" in proc_dc_summary.columns else 0
        _c_vol = proc_staffing["avg_daily_vol"].sum() if "avg_daily_vol" in proc_staffing.columns else 0
        _c_prod = round(_c_vol / _c_total_eff, 1) if _c_total_eff > 0 else 0

        cols = st.columns(7)
        cols[0].metric("Total DCs", _c_total_dcs)
        cols[1].metric("Total Layouts", _c_total_layouts)
        cols[2].metric("Non-XD Proc", f"{_non_xd_proc_heads:,}")
        cols[3].metric("Non-XD Dock", f"{_non_xd_dock_heads:,}")
        cols[4].metric("XD Combined", f"{_xd_heads:,}")
        cols[5].metric("Grand Total", f"{_c_total_eff:,}")
        cols[6].metric("Productivity", f"{_c_prod:,}")

        st.divider()
        st.subheader("Combined Manpower by DC (Processing + Dock)")
        if not proc_dc_summary.empty:
            combined = proc_dc_summary.copy()
            combined = combined.rename(columns={
                "total_perm": "Proc Perm", "total_flex": "Proc Flex", "total_heads": "Proc Heads"
            })
            if not dock_dc_summary.empty:
                dock_slim = dock_dc_summary[["DC", "total_perm", "total_flex", "total_heads"]].rename(columns={
                    "total_perm": "Dock Perm", "total_flex": "Dock Flex", "total_heads": "Dock Heads"
                })
                combined = combined.merge(dock_slim, on="DC", how="outer").fillna(0)
            else:
                combined["Dock Perm"] = 0
                combined["Dock Flex"] = 0
                combined["Dock Heads"] = 0
            # For XD sites, use combined staffing (cross-utilized single pool)
            combined["Is XD"] = combined["DC"].isin(xd_sites)
            combined["Type"] = combined["Is XD"].map({True: "XD", False: ""})

            # Non-XD: Grand Total = Proc + Dock (separate pools)
            combined.loc[~combined["Is XD"], "Grand Total"] = (
                combined.loc[~combined["Is XD"], "Proc Heads"] + combined.loc[~combined["Is XD"], "Dock Heads"]
            )

            # XD: Grand Total from combined hourly profile (true cross-utilization)
            if not xd_combined_staffing.empty:
                xd_totals = xd_combined_staffing.groupby("DC")["total_heads"].sum().to_dict()
                for dc in combined.loc[combined["Is XD"], "DC"]:
                    combined.loc[combined["DC"] == dc, "Grand Total"] = xd_totals.get(dc, 0)
            else:
                # Fallback: use max of proc/dock
                combined.loc[combined["Is XD"], "Grand Total"] = (
                    combined.loc[combined["Is XD"], ["Proc Heads", "Dock Heads"]].max(axis=1)
                )

            # Add Avg Daily Vol from PROCESSING data
            _proc_vol_dc = (
                proc_staffing.groupby("DC")["avg_daily_vol"].sum()
                .reset_index().rename(columns={"avg_daily_vol": "Avg Daily Vol"})
            )
            _proc_vol_dc["Avg Daily Vol"] = _proc_vol_dc["Avg Daily Vol"].round(0).astype(int)
            combined = combined.merge(_proc_vol_dc, on="DC", how="left")
            combined["Avg Daily Vol"] = combined["Avg Daily Vol"].fillna(0).astype(int)
            combined["Productivity"] = (
                combined["Avg Daily Vol"] / combined["Grand Total"].replace(0, float("nan"))
            ).round(1).fillna(0)

            # ── Actual Productivity comparison ──
            _act_detail, _act_summary = read_actual_productivity()
            if not _act_summary.empty:
                # Normalize DC names for matching
                _act_summary["DC"] = _act_summary["DC"].str.strip()
                combined = combined.merge(
                    _act_summary[["DC", "avg_actual_prod"]].rename(columns={"avg_actual_prod": "Actual Prod"}),
                    on="DC", how="left"
                )
                combined["Actual Prod"] = combined["Actual Prod"].round(0).fillna(0).astype(int)
                combined["Gap"] = (combined["Productivity"] - combined["Actual Prod"]).round(0).astype(int)
                combined["Gap %"] = (
                    (combined["Productivity"] - combined["Actual Prod"])
                    / combined["Actual Prod"].replace(0, float("nan")) * 100
                ).round(1).fillna(0)

            combined = combined.sort_values("Grand Total", ascending=False)

            # Total row
            num_cols = combined.select_dtypes(include="number").columns.tolist()
            totals = {c: combined[c].sum() for c in num_cols}
            if totals.get("Grand Total", 0) > 0:
                totals["Productivity"] = round(totals.get("Avg Daily Vol", 0) / totals["Grand Total"], 1)
            if totals.get("Actual Prod", 0) > 0 and "Actual Prod" in combined.columns:
                # Weighted average for actual prod
                _wt = combined[combined["DC"] != "TOTAL"]
                if not _wt.empty and _wt["Avg Daily Vol"].sum() > 0:
                    totals["Actual Prod"] = round(
                        (_wt["Actual Prod"] * _wt["Avg Daily Vol"]).sum() / _wt["Avg Daily Vol"].sum(), 0
                    )
                    totals["Gap"] = round(totals["Productivity"] - totals["Actual Prod"], 0)
                    totals["Gap %"] = round(
                        (totals["Productivity"] - totals["Actual Prod"]) / totals["Actual Prod"] * 100, 1
                    ) if totals["Actual Prod"] > 0 else 0
            totals_row = pd.DataFrame([{"DC": "TOTAL", **totals}])
            display = pd.concat([combined, totals_row], ignore_index=True)
            st.dataframe(display, use_container_width=True, hide_index=True)

            # ── Comparative Chart: Model vs Actual Productivity ──
            if "Actual Prod" in combined.columns:
                _chart_data = combined[
                    (combined["Productivity"] > 0) & (combined["Actual Prod"] > 0)
                ][["DC", "Productivity", "Actual Prod"]].copy()
                if not _chart_data.empty:
                    _chart_data = _chart_data.sort_values("Productivity", ascending=True)
                    import plotly.graph_objects as go
                    fig_comp = go.Figure()
                    fig_comp.add_trace(go.Bar(
                        y=_chart_data["DC"], x=_chart_data["Actual Prod"],
                        name="Actual Productivity", orientation="h",
                        marker_color="#ef4444", text=_chart_data["Actual Prod"].astype(int),
                        textposition="auto",
                    ))
                    fig_comp.add_trace(go.Bar(
                        y=_chart_data["DC"], x=_chart_data["Productivity"],
                        name="Model Productivity", orientation="h",
                        marker_color="#6366f1", text=_chart_data["Productivity"].astype(int),
                        textposition="auto",
                    ))
                    fig_comp.update_layout(
                        title="Model vs Actual Productivity (Shipments/Head/Day)",
                        barmode="group", height=max(400, len(_chart_data) * 45),
                        xaxis_title="Shipments per Head per Day",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    )
                    st.plotly_chart(fig_comp, use_container_width=True)

                    # Gap analysis
                    st.caption(
                        "**Gap > 0**: Model suggests higher productivity is achievable (potential overstaffing). "
                        "**Gap < 0**: Actual outperforms model (efficient operations or model underestimates)."
                    )
        else:
            st.info("No data.")

    with tab_roster:
        st.header("📋 Roster Plan")
        st.caption("Weekly offs optimized by load + absenteeism (15% day 1-10, 7% day 11+)")
        from core.roster_planner import compute_roster_plan, roster_summary
        roster_df = compute_roster_plan(proc_staffing, proc_load)
        if not roster_df.empty:
            r_summary = roster_summary(roster_df)

            # KPIs
            total_roster = r_summary["roster_size"].sum()
            total_perm = r_summary["perm_heads"].sum()
            avg_flex = r_summary["avg_flex"].sum()
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Perm Heads", f"{total_perm:,}")
            col2.metric("Total Roster Size", f"{total_roster:,}")
            col3.metric("Avg Daily Flex", f"{avg_flex:,.0f}")
            col4.metric("Roster Uplift", f"{(total_roster/total_perm - 1)*100:.1f}%" if total_perm > 0 else "-")

            # DC-level summary table
            st.subheader("Roster Summary by DC")
            display_cols = ["DC", "perm_heads", "roster_size", "avg_available", "avg_required", "avg_flex", "max_flex", "avg_surplus"]
            rename = {
                "perm_heads": "Perm Heads", "roster_size": "Roster Size",
                "avg_available": "Avg Available", "avg_required": "Avg Required",
                "avg_flex": "Avg Flex", "max_flex": "Max Flex", "avg_surplus": "Avg Surplus",
            }
            disp = r_summary[[c for c in display_cols if c in r_summary.columns]].rename(columns=rename)
            st.dataframe(disp, use_container_width=True, hide_index=True)

            # Daily detail for selected DC
            st.subheader("Daily Roster Detail")
            roster_dcs = sorted(roster_df["DC"].unique())
            sel_dc_roster = st.selectbox("Select DC", roster_dcs, key="roster_dc_select")
            sel_shift_roster = st.selectbox("Select Shift", sorted(roster_df["shift"].unique()), key="roster_shift_select")

            dc_roster = roster_df[(roster_df["DC"] == sel_dc_roster) & (roster_df["shift"] == sel_shift_roster)].sort_values("date")

            if not dc_roster.empty:
                import plotly.graph_objects as go

                fig = go.Figure()
                # Stack: Working + Surplus + Absent + Offs = Roster Size (constant)
                fig.add_trace(go.Bar(
                    x=dc_roster["date"].astype(str), y=dc_roster["working"],
                    name="Working", marker_color="#6366f1"
                ))
                fig.add_trace(go.Bar(
                    x=dc_roster["date"].astype(str), y=dc_roster["surplus"],
                    name="Surplus (Idle)", marker_color="#22c55e"
                ))
                fig.add_trace(go.Bar(
                    x=dc_roster["date"].astype(str), y=dc_roster["absent_expected"],
                    name="Expected Absent", marker_color="#ef4444"
                ))
                fig.add_trace(go.Bar(
                    x=dc_roster["date"].astype(str), y=dc_roster["offs_assigned"],
                    name="Weekly Offs", marker_color="#94a3b8"
                ))
                fig.add_trace(go.Scatter(
                    x=dc_roster["date"].astype(str), y=[dc_roster["roster_size"].iloc[0]] * len(dc_roster),
                    name="Roster Size", mode="lines", line=dict(color="orange", dash="dash", width=2)
                ))
                fig.update_layout(
                    barmode="stack", height=400,
                    yaxis_title="Headcount", xaxis_tickangle=-45,
                    title=f"{sel_dc_roster} — {sel_shift_roster} Daily Roster"
                )
                st.plotly_chart(fig, use_container_width=True)

                # Table
                table_cols = ["date", "dow", "dom", "day_load_ratio", "roster_size",
                              "offs_assigned", "on_floor_before_absent", "absent_expected",
                              "available", "working", "surplus", "flex_needed", "required_on_floor"]
                rename_t = {
                    "dow": "Day", "dom": "DoM", "day_load_ratio": "Load Ratio",
                    "roster_size": "Roster", "offs_assigned": "Offs",
                    "on_floor_before_absent": "On Floor (pre-absent)",
                    "absent_expected": "Absent", "available": "Available",
                    "working": "Working", "surplus": "Surplus",
                    "flex_needed": "Flex Needed", "required_on_floor": "Required",
                }
                disp_t = dc_roster[[c for c in table_cols if c in dc_roster.columns]].rename(columns=rename_t)
                st.dataframe(disp_t, use_container_width=True, hide_index=True)
        else:
            st.info("No roster data — check staffing and load data.")

    with tab_ai:
        from ui.ai_chat import render as render_ai
        render_ai(
            proc_staffing, proc_dc_summary, dock_staffing, dock_dc_summary,
            proc_load, dock_hourly, proc_peak_days, dock_peak_days, template
        )

else:
    # DC Detail view with Processing and Dock tabs
    tab_proc_dc, tab_dock_dc, tab_ai_dc = st.tabs(["📦 Processing", "🚛 Dock", "🤖 AI Assistant"])

    with tab_proc_dc:
        from ui.page_dc_detail import render as render_dc
        render_dc(proc_staffing, proc_lt_summary, proc_load, proc_peak_days, selected_dc, load_df_preship=load_df_preship)

    with tab_dock_dc:
        st.header(f"Dock Detail: {selected_dc}")
        if not dock_staffing.empty:
            dc_dock = dock_staffing[dock_staffing["DC"] == selected_dc]
            if not dc_dock.empty:
                from ui.components import kpi_strip
                kpi_strip([
                    {"label": "Dock Permanent", "value": f"{dc_dock['perm_heads'].sum():,}"},
                    {"label": "Dock Flex", "value": f"{dc_dock['flex_heads'].sum():,}"},
                    {"label": "Dock Total", "value": f"{dc_dock['total_heads'].sum():,}"},
                ])

                # ── Hourly MH chart with shift staffing lines ──
                dc_dock_load = dock_hourly[dock_hourly["DC"] == selected_dc] if not dock_hourly.empty else pd.DataFrame()
                dc_dock_peak_dates = dock_peak_days.get(selected_dc, [])
                if not dc_dock_load.empty and dc_dock_peak_dates:
                    import plotly.express as px
                    peak_load = dc_dock_load[dc_dock_load["Date of created"].isin(dc_dock_peak_dates)]
                    hourly_by_date = (
                        peak_load.groupby(["Date of created", "hour"])["total_mh"]
                        .sum().reset_index()
                    )
                    hourly_by_date["Date of created"] = hourly_by_date["Date of created"].astype(str)

                    st.subheader("Dock Hourly Manhours (Peak Days)")
                    fig = px.line(
                        hourly_by_date, x="hour", y="total_mh",
                        color="Date of created", markers=True,
                        labels={"hour": "Hour of Day", "total_mh": "Total Manhours", "Date of created": "Date"},
                        height=400,
                    )

                    # Shift region shading
                    shift_colors = {"Shift 1 (Night)": "rgba(99,102,241,0.08)", "Shift 2 (Morning)": "rgba(34,197,94,0.08)", "Shift 3 (Afternoon)": "rgba(249,115,22,0.08)"}
                    shift_line_colors = {"Shift 1 (Night)": "rgb(99,102,241)", "Shift 2 (Morning)": "rgb(34,197,94)", "Shift 3 (Afternoon)": "rgb(249,115,22)"}
                    for shift_name, shift_def in SHIFTS.items():
                        hours = shift_def["exclusive"]
                        if hours:
                            fig.add_vrect(
                                x0=min(hours) - 0.5, x1=max(hours) + 0.5,
                                fillcolor=shift_colors.get(shift_name, "rgba(0,0,0,0.05)"),
                                layer="below", line_width=0,
                                annotation_text=shift_name.split("(")[1].rstrip(")"),
                                annotation_position="top left", annotation_font_size=10,
                            )

                        # Staffing dotted lines
                        shift_staff = dc_dock[dc_dock["shift"] == shift_name]
                        if shift_staff.empty:
                            continue
                        staff_mh = shift_staff["peak_mh"].sum()
                        if not hours or staff_mh <= 0:
                            continue
                        short_name = shift_name.split("(")[1].rstrip(")")
                        color = shift_line_colors.get(shift_name, "gray")

                        low = [h for h in hours if h <= 5]
                        high = [h for h in hours if h >= 21]
                        if low and high:
                            for seg in [high, low]:
                                fig.add_shape(type="line",
                                    x0=min(seg)-0.5, x1=max(seg)+0.5,
                                    y0=staff_mh, y1=staff_mh,
                                    line=dict(color=color, width=2, dash="dot"))
                            fig.add_annotation(x=max(low)+0.3, y=staff_mh,
                                text=f"{short_name}: {staff_mh:.0f} MH",
                                showarrow=False, font=dict(size=9, color=color),
                                xanchor="right", yanchor="bottom")
                        else:
                            fig.add_shape(type="line",
                                x0=min(hours)-0.5, x1=max(hours)+0.5,
                                y0=staff_mh, y1=staff_mh,
                                line=dict(color=color, width=2, dash="dot"))
                            fig.add_annotation(x=max(hours)+0.3, y=staff_mh,
                                text=f"{short_name}: {staff_mh:.0f} MH",
                                showarrow=False, font=dict(size=9, color=color),
                                xanchor="right", yanchor="bottom")

                    fig.update_layout(xaxis=dict(dtick=1))
                    st.plotly_chart(fig, use_container_width=True)

                    # ── Stacked area by dock process ──
                    if "process" in dc_dock_load.columns:
                        st.subheader("Dock MH by Process (All Days)")
                        proc_hourly = dc_dock_load.groupby(["process", "hour"])["total_mh"].sum().reset_index()
                        fig2 = px.area(proc_hourly, x="hour", y="total_mh", color="process",
                            labels={"hour": "Hour of Day", "total_mh": "Total Manhours"}, height=450)
                        fig2.update_layout(xaxis=dict(dtick=1))
                        st.plotly_chart(fig2, use_container_width=True)

                st.divider()
                for shift_name in sorted(dc_dock["shift"].unique()):
                    shift_data = dc_dock[dc_dock["shift"] == shift_name]
                    st.subheader(shift_name)
                    display_cols = ["layout_name", "peak_mh", "perm_heads", "flex_heads",
                                    "total_heads", "avg_daily_vol", "productivity"]
                    display_cols = [c for c in display_cols if c in shift_data.columns]
                    st.dataframe(shift_data[display_cols], use_container_width=True, hide_index=True)
            else:
                st.info("No dock data for this DC.")
        else:
            st.info("No dock data available.")

    with tab_ai_dc:
        from ui.ai_chat import render as render_ai_dc
        render_ai_dc(
            proc_staffing, proc_dc_summary, dock_staffing, dock_dc_summary,
            proc_load, dock_hourly, proc_peak_days, dock_peak_days, template
        )

    # DC-specific detailed working Excel
    if st.sidebar.button(f"📥 Processing Working: {selected_dc}"):
        with st.spinner("Building detailed working..."):
            dc_excel = generate_dc_working_excel(
                selected_dc, load_df_preship, None,
                proc_load, proc_peak_days, proc_staffing, template,
                xd_combined_staffing=xd_combined_staffing,
            )
            st.sidebar.download_button(
                label=f"⬇️ Download {selected_dc} Processing",
                data=dc_excel,
                file_name=f"{selected_dc.replace(' ', '_')}_Processing_{date_start}_{date_end}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

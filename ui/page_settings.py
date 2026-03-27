"""Page 3: Sensitivity / What-If analysis."""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from core.staffing_calculator import compute_shift_headcount, rollup_by_dc
from ui.components import kpi_strip


def render(shift_peaks, overlap_peaks, current_params):
    """Render the sensitivity / what-if page."""

    st.header("Sensitivity Analysis")
    st.caption("Adjust parameters below to see how headcount changes in real time.")

    col1, col2 = st.columns(2)
    with col1:
        test_flex_pct = st.slider(
            "Flex %",
            min_value=0.0,
            max_value=0.30,
            value=current_params["flex_pct"],
            step=0.01,
            format="%.0f%%",
            key="sens_flex_pct",
        )
    with col2:
        test_flex_eff = st.slider(
            "Flex Efficiency",
            min_value=0.50,
            max_value=1.00,
            value=current_params["flex_efficiency"],
            step=0.05,
            format="%.0f%%",
            key="sens_flex_eff",
        )

    # Recompute with test params
    test_staffing = compute_shift_headcount(shift_peaks, overlap_peaks, flex_pct=test_flex_pct, flex_efficiency=test_flex_eff)
    test_dc = rollup_by_dc(test_staffing)

    # Compare to current
    current_staffing = compute_shift_headcount(shift_peaks, overlap_peaks, flex_pct=current_params["flex_pct"], flex_efficiency=current_params["flex_efficiency"])
    current_dc = rollup_by_dc(current_staffing)

    total_current = int(current_dc["total_heads"].sum())
    total_test = int(test_dc["total_heads"].sum())
    delta = total_test - total_current

    st.divider()

    kpi_strip([
        {"label": "Current Total Heads", "value": f"{total_current:,}"},
        {"label": "Scenario Total Heads", "value": f"{total_test:,}"},
        {"label": "Delta", "value": f"{delta:+,}"},
        {"label": "Change %", "value": f"{delta / max(total_current, 1) * 100:+.1f}%"},
    ])

    st.divider()

    # Side-by-side comparison
    compare = current_dc[["DC", "total_heads"]].merge(
        test_dc[["DC", "total_heads"]],
        on="DC",
        suffixes=("_current", "_scenario"),
    )
    compare["delta"] = compare["total_heads_scenario"] - compare["total_heads_current"]
    compare.columns = ["DC", "Current Heads", "Scenario Heads", "Delta"]
    compare = compare.sort_values("Delta", ascending=False)

    st.dataframe(compare, use_container_width=True, hide_index=True, height=500)

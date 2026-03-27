"""Reusable Streamlit UI components: KPI cards, styled tables, filters."""

import streamlit as st


def kpi_strip(metrics: list[dict]):
    """Render a horizontal strip of KPI cards.

    metrics: list of {"label": str, "value": str/int/float}
    """
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        col.metric(label=m["label"], value=m["value"])


def styled_dataframe(df, title=None, height=400):
    """Render a styled, sortable dataframe."""
    if title:
        st.subheader(title)
    st.dataframe(df, use_container_width=True, height=height, hide_index=True)

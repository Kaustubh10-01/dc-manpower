"""AI Assistant tab — Claude Opus powered chat with full data context."""

import os
from pathlib import Path
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

# Load .env from project root (dc-manpower/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=True)

# Fallback: read directly if dotenv fails
if not os.getenv("ANTHROPIC_API_KEY") and _env_path.exists():
    for line in _env_path.read_text().strip().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()

# Streamlit Cloud: read from st.secrets
if not os.getenv("ANTHROPIC_API_KEY"):
    try:
        import streamlit as _st
        _key = _st.secrets.get("ANTHROPIC_API_KEY", "")
        if _key:
            os.environ["ANTHROPIC_API_KEY"] = _key
    except Exception:
        pass


def _build_context(proc_staffing, proc_dc_summary, dock_staffing, dock_dc_summary,
                   proc_load, dock_hourly, proc_peak_days, dock_peak_days,
                   template):
    """Build a comprehensive text context from all computed data."""
    parts = []

    # ── Processing DC Summary ──
    if proc_dc_summary is not None and not proc_dc_summary.empty:
        parts.append("## Processing Manpower — DC Summary")
        parts.append(proc_dc_summary.to_string(index=False))

    # ── Dock DC Summary ──
    if dock_dc_summary is not None and not dock_dc_summary.empty:
        parts.append("\n## Dock Manpower — DC Summary")
        parts.append(dock_dc_summary.to_string(index=False))

    # ── Processing Staffing Detail (per layout × shift) ──
    if proc_staffing is not None and not proc_staffing.empty:
        parts.append("\n## Processing Staffing Detail (layout × shift)")
        # Summarize to avoid token overload
        summary = proc_staffing.groupby(["DC", "shift"]).agg(
            layouts=("layout_name", "nunique"),
            peak_mh=("peak_mh", "sum"),
            perm=("perm_heads", "sum"),
            flex=("flex_heads", "sum"),
            total=("total_heads", "sum"),
        ).reset_index()
        parts.append(summary.to_string(index=False))

        # Top layouts per DC
        parts.append("\n### Top 5 Layouts per DC (by total heads)")
        for dc in proc_staffing["DC"].unique():
            dc_s = proc_staffing[proc_staffing["DC"] == dc]
            top = dc_s.groupby(["layout_name", "Layout Type"])["total_heads"].sum().nlargest(5)
            if not top.empty:
                parts.append(f"\n{dc}:")
                for (ln, lt), heads in top.items():
                    parts.append(f"  {ln} ({lt}): {int(heads)} heads")

    # ── Layout Type Productivity (using avg_daily_vol from staffing) ──
    if proc_staffing is not None and not proc_staffing.empty and "avg_daily_vol" in proc_staffing.columns:
        parts.append("\n## Layout Type Mix & Productivity per DC")
        for dc in proc_staffing["DC"].unique():
            dc_staff = proc_staffing[proc_staffing["DC"] == dc]
            dc_total_vol = dc_staff["avg_daily_vol"].sum()
            dc_total_heads = dc_staff["total_heads"].sum()
            dc_prod = dc_total_vol / dc_total_heads if dc_total_heads > 0 else 0
            parts.append(f"\n{dc} (daily avg vol {dc_total_vol:,.0f}, {int(dc_total_heads)} heads, prod={dc_prod:,.0f}):")
            lt_group = dc_staff.groupby("Layout Type").agg(
                avg_vol=("avg_daily_vol", "sum"),
                heads=("total_heads", "sum"),
            ).reset_index()
            for _, row in lt_group.sort_values("avg_vol", ascending=False).iterrows():
                pct = row["avg_vol"] / dc_total_vol * 100 if dc_total_vol > 0 else 0
                lt_prod = row["avg_vol"] / row["heads"] if row["heads"] > 0 else 0
                parts.append(f"  {row['Layout Type']}: {pct:.1f}% vol, {int(row['heads'])} heads, prod={lt_prod:,.0f}")

    # ── Shift Breakdown ──
    if proc_staffing is not None and not proc_staffing.empty:
        parts.append("\n## Shift Breakdown per DC")
        shift_sum = proc_staffing.groupby(["DC", "shift"]).agg(
            total_heads=("total_heads", "sum"),
            peak_mh=("peak_mh", "sum"),
        ).reset_index()
        parts.append(shift_sum.to_string(index=False))

    # ── Peak Days ──
    if proc_peak_days:
        parts.append("\n## Processing Peak Days per DC")
        for dc, dates in sorted(proc_peak_days.items()):
            parts.append(f"  {dc}: {', '.join(str(d) for d in dates)}")

    # ── Hourly Load Variance ──
    if proc_load is not None and not proc_load.empty:
        parts.append("\n## Hourly Load Variance (Peak/Avg ratio)")
        for dc in sorted(proc_load["DC"].unique()):
            hourly = proc_load[proc_load["DC"] == dc].groupby("hour")["total_mh"].mean()
            if hourly.empty:
                continue
            ratio = hourly.max() / hourly.mean() if hourly.mean() > 0 else 0
            parts.append(f"  {dc}: peak/avg={ratio:.2f}")

    # ── MH per Shipment by DC ──
    if proc_staffing is not None and not proc_staffing.empty and "avg_daily_vol" in proc_staffing.columns:
        parts.append("\n## Avg Daily Volume & Productivity per DC")
        for dc in sorted(proc_staffing["DC"].unique()):
            dc_staff = proc_staffing[proc_staffing["DC"] == dc]
            avg_vol = dc_staff["avg_daily_vol"].sum()
            total_heads = dc_staff["total_heads"].sum()
            prod = avg_vol / total_heads if total_heads > 0 else 0
            parts.append(f"  {dc}: avg_daily_vol={avg_vol:,.0f}, heads={int(total_heads)}, prod={prod:,.0f}")

    # ── Template Info ──
    if template:
        parts.append("\n## Template Configuration")
        if "activity_manhours" in template:
            parts.append("Activity Efforts (MH per shipment):")
            for act, effort in sorted(template["activity_manhours"].items()):
                parts.append(f"  {act}: {effort:.6f}")

    return "\n".join(parts)


def render(proc_staffing, proc_dc_summary, dock_staffing, dock_dc_summary,
           proc_load, dock_hourly, proc_peak_days, dock_peak_days, template):
    """Render the AI chat assistant tab."""

    st.header("AI Assistant")
    st.caption("Ask questions about staffing, productivity, layout mix — powered by Claude Opus")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        st.error("ANTHROPIC_API_KEY not found in .env file. Please add it.")
        return

    # Build context once and cache in session
    if "ai_context" not in st.session_state or st.session_state.get("ai_context_stale", True):
        with st.spinner("Building data context..."):
            st.session_state["ai_context"] = _build_context(
                proc_staffing, proc_dc_summary, dock_staffing, dock_dc_summary,
                proc_load, dock_hourly, proc_peak_days, dock_peak_days, template
            )
            st.session_state["ai_context_stale"] = False

    # Chat history
    if "ai_messages" not in st.session_state:
        st.session_state["ai_messages"] = []

    # Display chat history
    for msg in st.session_state["ai_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    if prompt := st.chat_input("Ask about staffing, productivity, comparisons..."):
        st.session_state["ai_messages"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = _call_claude(api_key, st.session_state["ai_context"], prompt,
                                        st.session_state["ai_messages"][:-1])
            st.markdown(response)

        st.session_state["ai_messages"].append({"role": "assistant", "content": response})


def _call_claude(api_key, context, user_question, history):
    """Call Claude Opus with full data context."""
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)

        system_prompt = f"""You are an expert DC (Distribution Center) operations analyst embedded in a manpower planning tool.
You have access to the complete computed data for all DCs, layouts, shifts, and staffing.

Your role:
- Answer questions about staffing levels, productivity, layout efficiency
- Compare DCs, explain why one performs better than another
- Identify optimization opportunities
- Provide data-driven recommendations

Always cite specific numbers from the data. Be concise but thorough.

Here is the complete data context:

{context}
"""

        # Build messages with history
        messages = []
        for msg in history[-6:]:  # Last 6 messages for context
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_question})

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )

        return response.content[0].text

    except Exception as e:
        return f"Error calling Claude: {str(e)}"

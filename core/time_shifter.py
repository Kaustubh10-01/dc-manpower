"""Decomposes manhours into per-activity contributions and time-shifts each
activity to its correct hour based on Prior/Post offsets from Activity Productivity.

Time-shifting uses PROPORTIONAL splitting:
  50 min prior → 50/60 of MH goes to hour-1, 10/60 stays in current hour.
  90 min prior → 30/60 goes to hour-2, 30/60 goes to hour-1.
  180 min post → all MH goes to hour+3 (exact multiple of 60).

Vectorized with pandas — no row-by-row Python loops.
"""

import math
import pandas as pd
import numpy as np


def _build_proportional_offset_map(activity_prod_df):
    """Build dict: activity_name → list of (hour_offset, fraction) tuples.

    For M minutes Prior:
        full_hours = M // 60;  remaining = M % 60
        → (-(full_hours+1), remaining/60)  and  (-full_hours, 1 - remaining/60)
        If remaining == 0, only: (-full_hours, 1.0)

    For M minutes Post:
        → (full_hours+1, remaining/60)  and  (full_hours, 1 - remaining/60)
        If remaining == 0, only: (full_hours, 1.0)

    Examples:
        50 min Prior  → [(-1, 50/60), (0, 10/60)]
        90 min Prior  → [(-2, 30/60), (-1, 30/60)]
        180 min Post  → [(3, 1.0)]
    """
    offset_map = {}
    for _, row in activity_prod_df.iterrows():
        act = row["Activity"]
        minutes = row.get("Minutes", 0) or 0
        relative = row.get("Relative", None)

        if pd.isna(minutes) or minutes == 0 or pd.isna(relative):
            offset_map[act] = [(0, 1.0)]
            continue

        minutes = int(minutes)
        full_hours = minutes // 60
        remaining = minutes % 60
        direction = str(relative).strip().lower()

        if direction == "prior":
            if remaining == 0:
                offset_map[act] = [(-full_hours, 1.0)]
            else:
                frac_far = remaining / 60.0
                offset_map[act] = [
                    (-(full_hours + 1), frac_far),
                    (-full_hours, 1.0 - frac_far),
                ]
        elif direction == "post":
            if remaining == 0:
                offset_map[act] = [(full_hours, 1.0)]
            else:
                frac_far = remaining / 60.0
                offset_map[act] = [
                    (full_hours + 1, frac_far),
                    (full_hours, 1.0 - frac_far),
                ]
        else:
            offset_map[act] = [(0, 1.0)]

    return offset_map


def _build_activity_mh_rates(layout_prod_df, activity_names, activity_manhours):
    """Build dict: layout_type → {activity → mh_per_shipment}."""
    rates = {}
    for _, row in layout_prod_df.iterrows():
        lt = row["Layout Type"]
        lt_rates = {}
        for act in activity_names:
            load_factor = row.get(act, 0)
            if pd.isna(load_factor) or load_factor == 0:
                continue
            effort = activity_manhours.get(act, 0)
            if effort == 0:
                continue
            lt_rates[act] = float(load_factor) * float(effort)
        rates[lt] = lt_rates
    return rates


VOLUMETRIC_ACTIVITIES = [
    "IB Volumetric",
    "FWD Volumetric",
    "OSC Bag Sorting Volumetric",
    "Bag Staging Volumetric",
    "OB Volumetric",
]


def _build_explosion_table(lt_rates, vol_rates, offset_map, exclude_activities=None):
    """Pre-build a table of (layout_type, activity, hour_delta, frac, rate, is_vol)
    that can be joined to load_df in one vectorized operation.

    Args:
        exclude_activities: set of activity names to skip (e.g. dock activities).

    Returns list of dicts ready for pd.DataFrame.
    """
    exclude = set(exclude_activities or [])
    rows = []
    # Regular activities per layout type
    for lt, act_rates in lt_rates.items():
        for act, rate in act_rates.items():
            if act in exclude:
                continue
            offsets = offset_map.get(act, [(0, 1.0)])
            for (hour_delta, frac) in offsets:
                if frac <= 0:
                    continue
                rows.append({
                    "Layout Type": lt,
                    "activity": act,
                    "hour_delta": hour_delta,
                    "frac": frac,
                    "rate": rate * frac,
                    "is_vol": False,
                })

    # Volumetric activities (apply to ALL layout types)
    all_lts = list(lt_rates.keys())
    for act, rate in vol_rates.items():
        if act in exclude:
            continue
        offsets = offset_map.get(act, [(0, 1.0)])
        for (hour_delta, frac) in offsets:
            if frac <= 0:
                continue
            for lt in all_lts:
                rows.append({
                    "Layout Type": lt,
                    "activity": act,
                    "hour_delta": hour_delta,
                    "frac": frac,
                    "rate": rate * frac,
                    "is_vol": True,
                })

    return pd.DataFrame(rows)


def apply_xd_multipliers(load_df, template, load_df_preship=None):
    """For XD sites, recompute MH with XD effort multipliers applied per activity.

    Args:
        load_df: time-shifted MH data (output of apply_time_offsets)
        template: template dict
        load_df_preship: pre-shift data with regular_volume/volumetric_volume columns

    Uses activity-level detail to apply per-activity multipliers, then re-aggregates.
    Only modifies DCs that are in the XD list. Non-XD DCs pass through unchanged.
    """
    xd_sites = template.get("xd_sites", set())
    xd_multipliers = template.get("xd_multipliers", {})

    if not xd_sites or not xd_multipliers:
        return load_df

    non_xd = load_df[~load_df["DC"].isin(xd_sites)]
    xd_shifted = load_df[load_df["DC"].isin(xd_sites)]

    if xd_shifted.empty:
        return load_df

    # Use pre-shift data (with volume columns) for activity detail computation
    source = load_df_preship if load_df_preship is not None else xd_shifted
    xd_source = source[source["DC"].isin(xd_sites)]

    if xd_source.empty:
        return load_df

    # For XD sites, recompute using activity detail with multipliers
    detail = compute_activity_detail(xd_source, template, apply_xd=True)

    if detail.empty:
        return load_df

    # Re-aggregate: sum MH per (DC, layout, Layout Type, date, target_hour)
    xd_agg = (
        detail.groupby(["DC", "layout_name", "Layout Type", "Date of created", "target_hour"])["manhours"]
        .sum()
        .reset_index()
        .rename(columns={"target_hour": "hour", "manhours": "total_mh"})
    )
    xd_agg["regular_mh"] = xd_agg["total_mh"]  # simplified — vol is mixed in
    xd_agg["volumetric_mh"] = 0.0

    return pd.concat([non_xd, xd_agg], ignore_index=True)


def apply_time_offsets(load_df, template, exclude_activities=None):
    """Decompose manhours per activity, proportionally time-shift, and re-aggregate.

    Args:
        exclude_activities: set/list of activity names to skip.

    Vectorized: builds an explosion table and merges it with load_df.
    """
    layout_prod_df = template["layout_prod_df"]
    activity_names = template["activity_names"]
    activity_manhours = template["activity_manhours"]
    activity_prod_df = template["activity_prod"]

    offset_map = _build_proportional_offset_map(activity_prod_df)
    lt_rates = _build_activity_mh_rates(layout_prod_df, activity_names, activity_manhours)

    vol_rates = {}
    for act in VOLUMETRIC_ACTIVITIES:
        effort = activity_manhours.get(act, 0)
        if effort > 0:
            vol_rates[act] = float(effort)

    # Build explosion table: (Layout Type, activity, hour_delta, frac, rate, is_vol)
    explosion = _build_explosion_table(lt_rates, vol_rates, offset_map, exclude_activities)

    if explosion.empty:
        return load_df[["DC", "layout_name", "Layout Type", "Date of created", "hour"]].copy().assign(
            regular_mh=0.0, volumetric_mh=0.0, total_mh=0.0
        )

    # Split explosion into regular and volumetric
    exp_reg = explosion[~explosion["is_vol"]][["Layout Type", "hour_delta", "rate"]].copy()
    exp_vol = explosion[explosion["is_vol"]][["Layout Type", "hour_delta", "rate"]].copy()

    # Aggregate explosion by (Layout Type, hour_delta) — sum rates across activities
    exp_reg_agg = exp_reg.groupby(["Layout Type", "hour_delta"])["rate"].sum().reset_index()
    exp_vol_agg = exp_vol.groupby(["Layout Type", "hour_delta"])["rate"].sum().reset_index()

    # Select needed columns from load_df
    base = load_df[["DC", "layout_name", "Layout Type", "Date of created", "hour",
                     "regular_volume", "volumetric_volume"]].copy()
    base["hour"] = base["hour"].astype(int)

    # --- Regular MH ---
    reg_merged = base.merge(exp_reg_agg, on="Layout Type", how="inner")
    reg_merged["target_hour"] = (reg_merged["hour"] + reg_merged["hour_delta"]) % 24
    reg_merged["regular_mh"] = reg_merged["regular_volume"] * reg_merged["rate"]

    reg_result = (
        reg_merged.groupby(["DC", "layout_name", "Layout Type", "Date of created", "target_hour"])["regular_mh"]
        .sum()
        .reset_index()
        .rename(columns={"target_hour": "hour"})
    )

    # --- Volumetric MH ---
    vol_merged = base.merge(exp_vol_agg, on="Layout Type", how="inner")
    vol_merged["target_hour"] = (vol_merged["hour"] + vol_merged["hour_delta"]) % 24
    vol_merged["volumetric_mh"] = vol_merged["volumetric_volume"] * vol_merged["rate"]

    vol_result = (
        vol_merged.groupby(["DC", "layout_name", "Layout Type", "Date of created", "target_hour"])["volumetric_mh"]
        .sum()
        .reset_index()
        .rename(columns={"target_hour": "hour"})
    )

    # --- Combine ---
    group_cols = ["DC", "layout_name", "Layout Type", "Date of created", "hour"]
    result = reg_result.merge(vol_result, on=group_cols, how="outer").fillna(0)
    result["total_mh"] = result["regular_mh"] + result["volumetric_mh"]

    return result


def compute_activity_detail(load_df, template, exclude_activities=None, apply_xd=False):
    """Return per-activity, per-hour manhours WITH activity names preserved.

    Args:
        apply_xd: if True, apply XD effort multipliers for XD sites.

    Vectorized version. Returns DataFrame with:
        DC, layout_name, Layout Type, Date of created,
        source_hour, target_hour, activity, offset_hours, fraction, manhours.
    """
    layout_prod_df = template["layout_prod_df"]
    activity_names = template["activity_names"]
    activity_manhours = template["activity_manhours"]
    activity_prod_df = template["activity_prod"]

    offset_map = _build_proportional_offset_map(activity_prod_df)
    lt_rates = _build_activity_mh_rates(layout_prod_df, activity_names, activity_manhours)

    vol_rates = {}
    for act in VOLUMETRIC_ACTIVITIES:
        effort = activity_manhours.get(act, 0)
        if effort > 0:
            vol_rates[act] = float(effort)

    # Build full (non-aggregated) explosion table
    explosion = _build_explosion_table(lt_rates, vol_rates, offset_map, exclude_activities)

    if explosion.empty:
        return pd.DataFrame(columns=[
            "DC", "layout_name", "Layout Type", "Date of created",
            "source_hour", "target_hour", "activity", "offset_hours", "fraction", "manhours",
        ])

    base = load_df[["DC", "layout_name", "Layout Type", "Date of created", "hour",
                     "regular_volume", "volumetric_volume"]].copy()
    base["hour"] = base["hour"].astype(int)

    # Split into reg/vol explosions (keep activity-level detail)
    exp_reg = explosion[~explosion["is_vol"]][
        ["Layout Type", "activity", "hour_delta", "frac", "rate"]
    ].copy()
    exp_vol = explosion[explosion["is_vol"]][
        ["Layout Type", "activity", "hour_delta", "frac", "rate"]
    ].copy()

    results = []

    # Regular
    if not exp_reg.empty:
        reg = base.merge(exp_reg, on="Layout Type", how="inner")
        reg["target_hour"] = (reg["hour"] + reg["hour_delta"]) % 24
        reg["manhours"] = reg["regular_volume"] * reg["rate"]
        reg = reg[reg["manhours"] > 0]
        reg = reg.rename(columns={"hour": "source_hour", "hour_delta": "offset_hours", "frac": "fraction"})
        results.append(reg[["DC", "layout_name", "Layout Type", "Date of created",
                            "source_hour", "target_hour", "activity", "offset_hours",
                            "fraction", "manhours"]])

    # Volumetric
    if not exp_vol.empty:
        vol = base.merge(exp_vol, on="Layout Type", how="inner")
        vol["target_hour"] = (vol["hour"] + vol["hour_delta"]) % 24
        vol["manhours"] = vol["volumetric_volume"] * vol["rate"]
        vol = vol[vol["manhours"] > 0]
        vol = vol.rename(columns={"hour": "source_hour", "hour_delta": "offset_hours", "frac": "fraction"})
        results.append(vol[["DC", "layout_name", "Layout Type", "Date of created",
                            "source_hour", "target_hour", "activity", "offset_hours",
                            "fraction", "manhours"]])

    if results:
        detail = pd.concat(results, ignore_index=True)
    else:
        return pd.DataFrame(columns=[
            "DC", "layout_name", "Layout Type", "Date of created",
            "source_hour", "target_hour", "activity", "offset_hours", "fraction", "manhours",
        ])

    # Apply XD multipliers if requested
    if apply_xd:
        xd_sites = template.get("xd_sites", set())
        xd_multipliers = template.get("xd_multipliers", {})
        if xd_sites and xd_multipliers:
            for dc in detail["DC"].unique():
                if dc not in xd_sites:
                    continue
                mults = xd_multipliers.get(dc, {})
                for act, mult in mults.items():
                    if mult != 1.0:
                        mask = (detail["DC"] == dc) & (detail["activity"] == act)
                        detail.loc[mask, "manhours"] *= mult

    return detail

"""Splits volume into regular + volumetric using DC-level percentages."""

import pandas as pd


def apply_volumetric_split(load_df, volumetric_pct_df, layout_mapping):
    """Add columns: regular_volume, volumetric_volume based on DC + shipment_type.

    Volumetric % comes from the template, keyed by DC and shipment direction (forward/reverse).
    """
    # Build DC lookup from layout_mapping
    layout_to_dc = dict(zip(layout_mapping["layout_name"], layout_mapping["DC"]))
    load_df = load_df.copy()
    load_df["DC"] = load_df["layout_name"].map(layout_to_dc)

    # Build vol pct lookup: (DC, direction) → pct
    vol_lookup = {}
    for _, row in volumetric_pct_df.iterrows():
        dc = row["DC"]
        vol_lookup[(dc, "forward")] = row.get("Forward", 0.05)
        vol_lookup[(dc, "reverse")] = row.get("Reverse", 0.05)

    def get_vol_pct(row):
        return vol_lookup.get((row["DC"], row["shipment_type"]), 0.05)

    load_df["vol_pct"] = load_df.apply(get_vol_pct, axis=1)
    load_df["volumetric_volume"] = (load_df["Total awb_number"] * load_df["vol_pct"]).round(0).astype(int)
    load_df["regular_volume"] = load_df["Total awb_number"] - load_df["volumetric_volume"]

    return load_df

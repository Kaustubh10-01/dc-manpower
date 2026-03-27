"""Validation: check joins between load data and template."""


def validate_joins(load_df, layout_mapping, manhours_per_ship):
    """Validate that load layout names exist in template and layout types have productivity.

    Returns dict with keys: unmatched_layouts, unmatched_types, dormant_layouts, warnings.
    """
    load_layouts = set(load_df["layout_name"].unique())
    template_layouts = set(layout_mapping["layout_name"].unique())
    template_types = set(manhours_per_ship.keys())
    mapping_types = set(layout_mapping["Layout Type"].unique())

    unmatched_layouts = load_layouts - template_layouts
    dormant_layouts = template_layouts - load_layouts
    unmatched_types = mapping_types - template_types

    warnings = []
    if unmatched_layouts:
        warnings.append(f"{len(unmatched_layouts)} layout(s) in load data not found in template: {sorted(unmatched_layouts)[:10]}")
    if unmatched_types:
        warnings.append(f"{len(unmatched_types)} layout type(s) missing productivity data: {sorted(unmatched_types)}")
    if dormant_layouts:
        warnings.append(f"{len(dormant_layouts)} template layout(s) not seen in load data (dormant)")

    return {
        "unmatched_layouts": unmatched_layouts,
        "unmatched_types": unmatched_types,
        "dormant_layouts": dormant_layouts,
        "warnings": warnings,
    }

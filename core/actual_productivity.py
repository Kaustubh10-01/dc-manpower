"""Read actual productivity data from Excel."""

import pandas as pd
from config import ACTUAL_PRODUCTIVITY_FILE

# Normalize DC names from actual productivity file to match load data
DC_NAME_MAP = {
    "ONE NCR DC": "OneNCR DC",
}


def read_actual_productivity():
    """Read actual productivity file.

    Returns DataFrame with columns: DC, date, actual_productivity
    Also returns a summary DataFrame: DC, avg_actual_prod, min_actual_prod, max_actual_prod
    """
    if not ACTUAL_PRODUCTIVITY_FILE.exists():
        return pd.DataFrame(), pd.DataFrame()

    df = pd.read_excel(ACTUAL_PRODUCTIVITY_FILE, sheet_name="Sheet1", header=None)

    # Row 1 (index 1) has headers: col B = "Normalized Productivity", cols C+ = dates
    # Rows 2+ (index 2+) have DC names in col B, productivity values in cols C+
    dates = []
    for c in range(2, df.shape[1]):
        val = df.iloc[1, c]
        if pd.notna(val):
            try:
                dates.append(pd.to_datetime(val).date())
            except Exception:
                pass

    rows = []
    for r in range(2, df.shape[0]):
        dc = df.iloc[r, 1]
        if pd.isna(dc) or dc == "-" or dc == "Day Productivity":
            continue
        dc = str(dc).strip()
        dc = DC_NAME_MAP.get(dc, dc)  # normalize name
        for i, dt in enumerate(dates):
            val = df.iloc[r, 2 + i]
            if pd.notna(val) and val != "-" and val != 0:
                try:
                    rows.append({"DC": dc, "date": dt, "actual_productivity": float(val)})
                except (ValueError, TypeError):
                    pass

    detail = pd.DataFrame(rows)

    if detail.empty:
        return detail, pd.DataFrame()

    summary = detail.groupby("DC")["actual_productivity"].agg(
        avg_actual_prod="mean",
        min_actual_prod="min",
        max_actual_prod="max",
        days_with_data="count",
    ).reset_index()

    return detail, summary

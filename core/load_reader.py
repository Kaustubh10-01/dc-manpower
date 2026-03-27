"""Reads daily load files from the folder, applies date filter."""

import pandas as pd
from pathlib import Path
from datetime import datetime, date
from config import DAILY_LOADS_DIR, LOAD_COL_DATE, LOAD_COL_VOLUME, LOAD_FILE_PATTERN


def read_load_files(folder=None, date_start=None, date_end=None):
    """Read all .xlsx files in folder, concatenate, filter by date range.

    Args:
        folder: Path to daily_loads folder.
        date_start: inclusive start date (date or datetime).
        date_end: inclusive end date (date or datetime).

    Returns:
        pd.DataFrame with all load rows in the date range.
    """
    folder = Path(folder) if folder else DAILY_LOADS_DIR
    all_files = sorted(folder.glob("Location_wise_Layout_data_Processing*.xlsx"))
    if not all_files:
        # Fallback to old pattern
        all_files = sorted(folder.glob(f"{LOAD_FILE_PATTERN}.xlsx"))
    if not all_files:
        raise FileNotFoundError(f"No .xlsx files found in {folder}")

    frames = []
    for f in all_files:
        df = pd.read_excel(f, header=0)
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)

    # Normalise date column to date objects
    # Handle mix of proper dates and Excel serial numbers (e.g. 46089 = 2026-03-08)
    col = combined[LOAD_COL_DATE]
    EXCEL_EPOCH = pd.Timestamp("1899-12-30")

    # Detect numeric values first (Excel serial numbers)
    numeric_vals = pd.to_numeric(col, errors="coerce")
    is_serial = numeric_vals.notna() & (numeric_vals > 40000) & (numeric_vals < 60000)

    # Parse non-serial as datetime
    parsed = pd.Series(pd.NaT, index=col.index)
    if (~is_serial).any():
        parsed[~is_serial] = pd.to_datetime(col[~is_serial], errors="coerce")
    # Convert serial numbers
    if is_serial.any():
        parsed[is_serial] = EXCEL_EPOCH + pd.to_timedelta(numeric_vals[is_serial], unit="D")

    combined[LOAD_COL_DATE] = parsed.dt.date

    # Apply date filter
    if date_start:
        if isinstance(date_start, datetime):
            date_start = date_start.date()
        combined = combined[combined[LOAD_COL_DATE] >= date_start]
    if date_end:
        if isinstance(date_end, datetime):
            date_end = date_end.date()
        combined = combined[combined[LOAD_COL_DATE] <= date_end]

    # Ensure volume is numeric
    combined[LOAD_COL_VOLUME] = pd.to_numeric(combined[LOAD_COL_VOLUME], errors="coerce").fillna(0).astype(int)

    # Strip string columns
    for col in ["location_name", "layout_type", "shipment_type", "layout_name"]:
        if col in combined.columns:
            combined[col] = combined[col].astype(str).str.strip()

    return combined.reset_index(drop=True)

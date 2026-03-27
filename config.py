"""Configuration: paths, defaults, planning parameters."""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEPLOY_MODE = os.environ.get("DEPLOY_MODE", "local")

if DEPLOY_MODE == "cloud":
    # Cloud Run: data bundled in /app/data_files/
    CLOUD_DATA = BASE_DIR / "data_files"
    DAILY_LOADS_DIR = CLOUD_DATA
    TEMPLATE_FILE = CLOUD_DATA / "Layout_Productivity_Clean.xlsx"
    ACTUAL_PRODUCTIVITY_FILE = CLOUD_DATA / "Actual Productivity.xlsx"
elif (BASE_DIR / "data_bundle").exists():
    # Streamlit Cloud: data in data_bundle/ within repo
    CLOUD_DATA = BASE_DIR / "data_bundle"
    DAILY_LOADS_DIR = CLOUD_DATA
    TEMPLATE_FILE = CLOUD_DATA / "Layout_Productivity_Clean.xlsx"
    ACTUAL_PRODUCTIVITY_FILE = CLOUD_DATA / "Actual Productivity.xlsx"
else:
    # Local: read from parent folder so user can update in place
    DAILY_LOADS_DIR = BASE_DIR.parent
    TEMPLATE_FILE = BASE_DIR.parent / "Layout_Productivity_Clean.xlsx"
    ACTUAL_PRODUCTIVITY_FILE = BASE_DIR.parent / "Actual Productivity.xlsx"

LOAD_FILE_PATTERN = "Location_wise_*"  # glob pattern for load files

# ── Template sheet names ───────────────────────────────
SHEET_LAYOUT_MAPPING = "Layout to Station Type"
SHEET_VOLUMETRIC_PCT = "Volumetric %"
SHEET_LAYOUT_PRODUCTIVITY = "Layout Productivity"
SHEET_ACTIVITY_PRODUCTIVITY = "Activity Productivity"

# ── Planning parameter defaults ────────────────────────
DEFAULT_PEAK_DAYS = 3
DEFAULT_FLEX_PCT = 0.10
DEFAULT_FLEX_EFFICIENCY = 0.80   # flex output as fraction of permanent
DEFAULT_PEAK_HOUR_METHOD = "max" # "max" or "percentile"
DEFAULT_PEAK_HOUR_PERCENTILE = 90

# ── Shift definitions ─────────────────────────────────
# Each shift: name, all hours covered, exclusive hours (only this shift covers them)
SHIFTS = {
    "Shift 1 (Night)": {
        "hours": [21, 22, 23, 0, 1, 2, 3, 4, 5],
        "exclusive": [21, 22, 23, 0, 1, 2, 3, 4, 5],
        "label": "9 PM – 6 AM",
    },
    "Shift 2 (Morning)": {
        "hours": [8, 9, 10, 11, 12, 13, 14, 15, 16],
        "exclusive": [8, 9, 10, 11],
        "label": "8 AM – 5 PM",
    },
    "Shift 3 (Afternoon)": {
        "hours": [12, 13, 14, 15, 16, 17, 18, 19, 20],
        "exclusive": [17, 18, 19, 20],
        "label": "12 PM – 9 PM",
    },
}
# Hours 6-7 (6 AM – 8 AM) are gap hours between Shift 1 and Shift 2
OVERLAP_HOURS = [12, 13, 14, 15, 16]  # Shared between Shift 2 and Shift 3
OVERLAP_SHIFTS = ["Shift 2 (Morning)", "Shift 3 (Afternoon)"]

# ── Load file columns ─────────────────────────────────
LOAD_COL_DC = "location_name"
LOAD_COL_DATE = "Date of created"
LOAD_COL_HOUR = "hour"
LOAD_COL_LAYOUT_TYPE = "layout_type"
LOAD_COL_SHIPMENT_TYPE = "shipment_type"
LOAD_COL_LAYOUT = "layout_name"
LOAD_COL_VOLUME = "Total awb_number"

# ── Secondary activities (smoothable across shift) ────
# These activities can be buffered — no need to spike in one hour.
# Their MH is distributed evenly across SECONDARY_WORKING_HOURS per shift
# with SECONDARY_VARIANCE allowed on top.
SECONDARY_ACTIVITIES = [
    "FWD Secondary",
    "Rev Secondary Sorting",
    "Rejection Secondary",
    "FMRTS Secondary with Scan",
]
SECONDARY_WORKING_HOURS = 7   # out of 8-hour shift (1 hour break)
SECONDARY_VARIANCE = 0.10     # 10% above average allowed

# ── Dock vs Processing activity classification ────────
# Actual dock activities: MH comes from dock load file (actual hourly data)
ACTUAL_DOCK_ACTIVITIES = ["IB", "OB", "VIA Bag Sorting", "Bag Sorter Design"]
# Derived dock activities: MH derived from processing volume + time offsets
DERIVED_DOCK_ACTIVITIES = [
    "OSC Bag Sorting", "Bag Staging",
    "OSC Bag Sorting Volumetric", "Bag Staging Volumetric",
]
# All dock activities
ALL_DOCK_ACTIVITIES = ACTUAL_DOCK_ACTIVITIES + DERIVED_DOCK_ACTIVITIES
# Processing activities: everything NOT in dock (auto-computed from template)

# ── Top N hours for shift staffing ────────────────────
DEFAULT_TOP_N_HOURS = 7

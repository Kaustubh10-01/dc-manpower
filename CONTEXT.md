# DC Manpower Planning Tool — Complete Context

## Project Overview
A Streamlit-based workforce planning tool for Shadowfax distribution centers (DCs). Computes staffing requirements from hourly shipment volume data, with full traceability from raw data to final headcount.

**Live URL**: https://dc-manpower-roqefved2ycjsuouv8yuza.streamlit.app/
**Password**: ShadowfaxFTW
**Repo**: https://github.com/Kaustubh10-01/dc-manpower (public)
**API Key**: Stored in Streamlit Secrets (Anthropic Claude Opus for AI Assistant)

---

## Architecture

### Data Files (in `data_bundle/` for cloud, parent folder for local)
1. **Layout_Productivity_Clean.xlsx** — Template with 4 sheets:
   - `Layout to Station Type`: Maps layout names to layout types
   - `Volumetric %`: Per-DC volumetric split percentages
   - `Layout Productivity`: Load factors per activity per layout type (e.g., Rejection = 5-10%)
   - `Activity Productivity`: 30 activities with manhours/shipment, time offsets (Minutes + Prior/Post), and XD multipliers
2. **Location_wise_Layout_data_Processing.xlsx** — Hourly processing volume per DC/layout (77K+ rows, 17+ days)
3. **Location_wise_Layout_data_Dock.xlsx** — Hourly dock volume per DC/process (IB Regular, IB Cross-Dock, IB Cross-Dock Bag Sorter, OB)
4. **Actual Productivity.xlsx** — Historical achieved productivity per DC per day

### Pipeline Flow
```
Raw Volume (hourly, per layout)
  |
  v
Volumetric Split (95/5 default, per-DC configurable)
  |
  v
Add Layout Type mapping
  |
  v
Time Shifter (per-activity offsets from template)
  - Each activity's MH is proportionally split across hours
  - e.g., 50 min Prior = 83% to hour-1, 17% stays at hour 0
  - XD sites get reduced effort multipliers on certain activities
  |
  v
Peak Day Selection (top N days by total MH, per DC independently)
  |
  v
Shift Peak MH (per layout x shift x peak day)
  - Shift 1 (Night): 9 PM - 6 AM (hours 21-5)
  - Shift 2 (Morning): 8 AM - 5 PM (hours 8-16)
  - Shift 3 (Afternoon): 12 PM - 9 PM (hours 12-20)
  - For each peak day x shift: rank hours, take top N, average them
  - Then average across peak days
  |
  v
Staffing Calculator
  - Permanent = ceil(peak_mh * (1 - flex_pct))
  - Flex = ceil(peak_mh * flex_pct / flex_efficiency)
  - Default: 90% perm, 10% flex at 80% efficiency
  - Daily flex: computed per-day based on actual load vs permanent capacity
  - Effective heads = perm + avg_daily_flex (for productivity calculation)
  |
  v
Productivity = Avg Daily Volume / Effective Heads
```

### Two Separate Pipelines
1. **Processing Pipeline**: All processing activities (FWD FSM, Primary, Secondary, BBB, Sorter Feedline, etc.) — IB/OB/VIA zeroed out in template load factors
2. **Dock Pipeline**:
   - Actual: IB, OB, Cross-Dock from dock load file (real hourly data)
   - Derived: OSC Bag Sorting, Bag Staging (from processing volume x load factors x time offsets)
   - VIA Bag Sorting and Bag Sorter Design offset by +1 hour from IB

### XD (Cross-Dock) Sites
- Defined in template's `Activity Productivity` sheet (XD Sites column)
- Cross-utilization: same workers handle both dock + processing
- Combined hourly MH profile (dock + processing summed), then staffing computed on combined
- Some activities have reduced effort at XD sites (e.g., Bag Staging at 50%)

### Roster Planning
- Each person works 6 out of 7 days (Indian labour code)
- Weekly offs assigned to lightest load days within each week
- Absenteeism: 15% for day 1-10 of month, 7% for day 11-31
- Roster size = on-floor requirement / (1 - absenteeism%)
- Weekly offs distributed inversely proportional to daily load

---

## Key Design Decisions (from user prompts)

### Shift Definitions
- Shift 1 (Night): 9 PM to 6 AM — 8 working hours
- Shift 2 (Morning): 8 AM to 5 PM — 8 working hours
- Shift 3 (Afternoon): 12 PM to 9 PM — 8 working hours
- Hours 6-7 AM are gap hours (no shift)
- Hours 12-16 overlap between Shift 2 and Shift 3

### Staffing Method: Top-N Hours Average
- NOT absolute peak hour — too spiky and overstaffs
- For each layout x shift x peak day: find top N hours (configurable slider, default 7), average them
- Then average across peak days
- More peak days = lower staffing (dilutes extreme days)
- User explicitly rejected per-hour peak in favor of this averaging approach

### Time Shifting: Proportional Split
- Activities with offset don't hard-shift — they proportionally split
- 50 min Prior = 50/60 (83.3%) moves to hour-1, 10/60 (16.7%) stays
- 90 min Prior = 30/60 to hour-2, 30/60 to hour-1
- All offsets read from Excel template (not hardcoded), except dock +1hr for VIA/Bag Sorter Design

### Flex Staffing Model
- Permanent sized for peak days (fixed workforce)
- Flex is daily variable — covers gap between permanent capacity and actual daily load
- Flex operates at 80% efficiency of permanent
- Productivity uses effective heads (perm + avg daily flex), not peak flex

### Secondary Activities Smoothing (discussed but superseded by top-N approach)
- Originally discussed distributing FWD Secondary etc. evenly across shift
- User chose top-N hours averaging instead, which naturally smooths secondary spikes

### Volume Sources
- Processing tab: uses processing file volume
- Dock tab: uses dock file volume
- Combined tab: uses processing file volume for productivity calculation

### Actual vs Model Productivity
- Actual data from separate Excel (daily productivity per DC)
- DC name normalization: "ONE NCR DC" -> "OneNCR DC"
- Shown as side-by-side bar chart with gap analysis

---

## UI Structure

### Sidebar
- Date range picker (auto-detects from data)
- DC selector dropdown (All DCs + individual DCs)
- Parameters: Peak Days Count, Top N Hours per Shift, Flex %, Flex Efficiency
- Exclude Prime Load toggle
- Layout Type filter (when individual DC selected)
- Refresh Data button (clears cache, re-reads Excel)
- Export buttons (Staffing Plan Excel, Detailed Working Excel)

### Tabs (All DCs view)
1. **Processing**: DC summary table with productivity, shift breakdown
2. **Dock**: DC summary with actual dock staffing
3. **Combined**: Processing + Dock totals, Actual vs Model comparison chart
4. **AI Assistant**: Claude Opus chat with full data context
5. **Roster Plan**: Weekly roster with daily flex, offs, absenteeism

### Tabs (Individual DC view)
- Same tabs plus:
  - Daily shipment volume bar chart (peak days highlighted in red)
  - Hourly MH line chart per shift with staffing recommendation dotted lines
  - Layout type filter that updates all tables and charts
  - Detailed Working Excel download

---

## File Structure
```
dc-manpower/
  app.py                    # Main Streamlit app
  config.py                 # All configuration, paths, constants
  core/
    load_reader.py          # Read processing load Excel
    dock_reader.py          # Read dock load Excel, compute dock MH
    template_reader.py      # Read template (layout mapping, activity prod, etc.)
    volumetric_splitter.py  # Split volume into regular + volumetric
    manhours_calculator.py  # Add layout type, compute simple MH
    time_shifter.py         # Proportional time offsets, activity detail
    peak_selector.py        # Select peak days, compute shift peak MH (top-N)
    staffing_calculator.py  # Perm/flex headcount, daily flex profile
    actual_productivity.py  # Read actual productivity data
    roster_planner.py       # Roster with weekly offs + absenteeism
  ui/
    page_dc_detail.py       # Individual DC detail page
    ai_chat.py              # Claude AI assistant
  output/
    excel_export.py         # Summary staffing Excel
    dc_working_export.py    # Detailed working Excel with formula traceability
  data_bundle/              # Data files for cloud deployment
  assets/
    logo.png                # Shadowfax logo
  .env                      # Local API key (gitignored)
  requirements.txt          # Python dependencies
  Dockerfile                # For Cloud Run deployment (prepared, not used yet)
```

---

## Deployment
- **Current**: Streamlit Community Cloud (free)
- **Repo**: Public GitHub (password-protected app, API key in Streamlit Secrets)
- **Data updates**: Push new Excel files to `data_bundle/`, auto-redeploys
- **Prepared but not deployed**: Google Cloud Run (Dockerfile ready, needs GCP permissions)
- **Planned**: Google Sheets integration for live data without re-deploy

---

## Pending / Future Work

### Module 2: Sales Projection to Staffing
- Input: Client-level sales forecast (sales_projection_apr.xlsx)
- Step 1: Derive DC splits from FM/WH/DSC/Rev summary sheets
- Step 2: Split to layouts using last 7 days of real data patterns
- Step 3: Apply hourly patterns from last 7 days
- Step 4: Feed into Module 1 pipeline for staffing

### Google Sheets Integration
- Template: `1gSuZpzj-kKkfEWN76BFewd34hENlY9CWphp_krAa0YE`
- Processing: `1rGtipQrLgKnDpxziNey7dgvKiw8yIgQe_UJ9Qyk0ilk`
- Dock: `1tQTCGLY_fACITw3Dykwbrrwb1IlUSseBD8f6rHBWDJA`
- Actual Productivity: `1johHUb6rqppPE6VRzZuOGtw_qBxHKKzHQy4ZQlbcu-I`
- Needs service account setup for authenticated access

### Other Enhancements
- Restrict Cloud Run access to @shadowfax.in via Google IAM
- Daily flex profile chart per DC
- Dock hourly chart in DC detail view
- More robust date parsing for Excel serial numbers

---

## Key User Preferences
- No emojis unless requested
- Concise responses, straight to the point
- Always verify changes work before presenting
- Full traceability in Excel exports (formula-linked, not hardcoded)
- Smart roster planning (load-based weekly offs, not fixed day)
- Practical staffing model (top-N hours average, not absolute peak)
- Separate dock and processing manpower pools (except XD sites)
- Average daily flex for productivity (not peak flex)

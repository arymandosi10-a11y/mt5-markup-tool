import streamlit as st
import pandas as pd
import requests
import openpyxl
from io import BytesIO

# ============================================================
# PAGE CONFIG + WIDE LAYOUT (so right-side columns appear)
# ============================================================
st.set_page_config(page_title="MarkupX – Market Cost Engine", layout="wide")

st.markdown(
    """
    <style>
    /* Make the whole page wider so dataframe doesn't cut columns */
    .block-container {max-width: 1700px; padding-top: 1.2rem; padding-bottom: 2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 MarkupX – Real Market Markup & Cost Engine")
st.caption("Real market prices + Profit-currency → USD conversion | Forex • Indices • Metals • Energies")

# ============================================================
# REAL MARKET PRICE (Yahoo JSON endpoint - no extra packages)
# ============================================================
@st.cache_data(ttl=30)
def yahoo_quote(symbol: str):
    """
    Returns dict with regularMarketPrice if available.
    Uses a public Yahoo Finance quote endpoint (no yfinance dependency).
    """
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    res = data.get("quoteResponse", {}).get("result", [])
    return res[0] if res else None

@st.cache_data(ttl=30)
def get_market_price(symbol: str):
    """
    Returns live market price for a symbol via Yahoo endpoint.
    """
    try:
        q = yahoo_quote(symbol)
        if not q:
            return None
        p = q.get("regularMarketPrice", None)
        return float(p) if p is not None else None
    except Exception:
        return None

@st.cache_data(ttl=60)
def fx_to_usd_rate(ccy: str):
    """
    Convert Profit Currency -> USD using FX quote.
    Example: EUR -> EURUSD=X
    """
    if not ccy or str(ccy).upper() == "USD":
        return 1.0
    ccy = str(ccy).upper()
    try:
        q = yahoo_quote(f"{ccy}USD=X")
        if not q:
            return None
        p = q.get("regularMarketPrice", None)
        return float(p) if p is not None else None
    except Exception:
        return None

# ============================================================
# SIDEBAR SETTINGS
# ============================================================
st.sidebar.header("⚙️ Settings")

default_markup_points = st.sidebar.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)

lp_rate_per_1m_per_side = st.sidebar.number_input(
    "LP rate ($ per 1M notional per side)", min_value=0.0, value=7.0, step=0.5
)
lp_sides = st.sidebar.selectbox("LP sides", options=[1, 2], index=1)

st.sidebar.divider()
st.sidebar.subheader("IB Commission")

ib_type = st.sidebar.selectbox("Type", ["None", "Fixed ($/lot)", "Point-wise"], index=0)
ib_fixed_per_lot = st.sidebar.number_input("Fixed ($ per lot)", min_value=0.0, value=10.0, step=1.0)
ib_pointwise_points = st.sidebar.number_input(
    "Point-wise points (uses PointValue_USD * points * lots)", min_value=0.0, value=20.0, step=1.0
)

st.sidebar.divider()
st.sidebar.subheader("Display")
max_rows = st.sidebar.number_input("Show first N rows", min_value=10, max_value=5000, value=500, step=50)

# ============================================================
# FILE UPLOAD
# ============================================================
uploaded_file = st.file_uploader("📤 Upload Symbol Sheet (Excel)", type=["xlsx"])

if uploaded_file is None:
    st.warning("Please upload your Excel file to continue.")
    st.stop()

# ============================================================
# LOAD EXCEL SAFELY
# ============================================================
try:
    wb = openpyxl.load_workbook(uploaded_file, data_only=False)
    ws = wb.active

    headers = [cell.value for cell in ws[1]]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    df = pd.DataFrame(rows, columns=headers)

except Exception as e:
    st.error(f"Failed to read the Excel file. Error: {e}")
    st.stop()

# ============================================================
# REQUIRED COLUMNS (adjust names if your sheet differs)
# ============================================================
# Expected columns based on your file/workflow:
# Symbol | Lots | Profit Currency | Contract Size | PointValue_Profit_perlot
required = ["Symbol", "Lots", "Profit Currency", "Contract Size", "PointValue_Profit_perlot"]

missing = [c for c in required if c not in df.columns]
if missing:
    st.error(
        "Your Excel is missing required columns:\n"
        + ", ".join(missing)
        + "\n\nExpected columns are:\n"
        + ", ".join(required)
    )
    st.stop()

# Optional override column for per-symbol markup
if "Markup_Points_Override" not in df.columns:
    df["Markup_Points_Override"] = None  # creates it in dataframe for calculations

# ============================================================
# CALCULATIONS (Profit currency ONLY)
# Indices/Metals/Energies: no base currency needed -> profit currency conversion is enough
# ============================================================
out_rows = []
errors = []

for idx, r in df.iterrows():
    symbol = str(r.get("Symbol", "")).strip()
    lots = float(r.get("Lots") or 0)
    profit_ccy = str(r.get("Profit Currency", "USD")).upper().strip()

    contract_size = float(r.get("Contract Size") or 0)
    point_value_profit = float(r.get("PointValue_Profit_perlot") or 0)

    # markup override
    mp_override = r.get("Markup_Points_Override")
    try:
        mp = float(mp_override) if mp_override not in [None, "", 0] else float(default_markup_points)
    except Exception:
        mp = float(default_markup_points)

    # Market price (for Notional)
    price = get_market_price(symbol)

    # FX: Profit currency -> USD
    fx = fx_to_usd_rate(profit_ccy)
    if fx is None:
        # If FX missing (rare), fallback to 1 and track error
        fx = 1.0
        errors.append(f"FX rate missing for {profit_ccy}. Used 1.0 fallback.")

    # PointValue in USD (per lot)
    point_value_usd = point_value_profit * fx

    # Markup
    markup_profit = mp
    markup_usd = point_value_usd * mp * lots

    # Notional: use market price when available, else compute as 0 and show note
    if price is None:
        notional_profit = 0.0
        notional_usd = 0.0
        errors.append(f"Price missing for {symbol}. Notional set to 0.")
    else:
        notional_profit = price * contract_size * lots
        notional_usd = notional_profit * fx

    # LP commission (per 1M USD notional, uses sides)
    lp_commission_usd = (notional_usd / 1_000_000.0) * lp_rate_per_1m_per_side * int(lp_sides)

    # IB commission (NO sides)
    if ib_type == "Fixed ($/lot)":
        ib_commission_usd = ib_fixed_per_lot * lots
    elif ib_type == "Point-wise":
        # per your instruction: pointwise based on point value
        ib_commission_usd = point_value_usd * ib_pointwise_points * lots
    else:
        ib_commission_usd = 0.0

    # Brokerage formula (per your instruction)
    brokerage_usd = markup_usd - lp_commission_usd - ib_commission_usd

    out_rows.append(
        {
            "Symbol": symbol,
            "Lots": lots,
            "Profit_Currency": profit_ccy,
            "FX_Profit_to_USD": fx,
            "Market_Price": price if price is not None else "",
            "Contract_Size": contract_size,
            "PointValue_Profit_perlot": point_value_profit,
            "PointValue_USD_perlot": point_value_usd,
            "Markup_Points_Used": mp,
            "Markup_USD": markup_usd,
            "Notional_Profit": notional_profit,
            "Notional_USD": notional_usd,
            "LP_Commission_USD": lp_commission_usd,
            "IB_Commission_USD": ib_commission_usd,
            "Brokerage_USD": brokerage_usd,
        }
    )

out_df = pd.DataFrame(out_rows)

# ============================================================
# DISPLAY (Columns selector + full width)
# ============================================================
st.subheader("📈 Live Cost Calculation")

st.caption("Tip: Use the column selector to show/hide columns. This also prevents right-side columns from disappearing.")

default_cols = [
    "Symbol", "Lots", "Profit_Currency", "Market_Price",
    "PointValue_USD_perlot", "Markup_Points_Used", "Markup_USD",
    "Notional_USD", "LP_Commission_USD", "IB_Commission_USD", "Brokerage_USD"
]

selected_cols = st.multiselect(
    "Select columns to display",
    options=list(out_df.columns),
    default=[c for c in default_cols if c in out_df.columns],
)

view_df = out_df[selected_cols].head(int(max_rows)) if selected_cols else out_df.head(int(max_rows))

st.dataframe(view_df, use_container_width=True, hide_index=True)

if errors:
    with st.expander("⚠️ Warnings / Missing data details"):
        for e in errors[:200]:
            st.write("•", e)
        if len(errors) > 200:
            st.write(f"...and {len(errors) - 200} more")

# ============================================================
# EXCEL EXPORT WITH FORMULAS
#   - Report sheet with formulas visible in Excel
#   - Settings sheet with inputs
# ============================================================
def export_excel_with_formulas(report_df: pd.DataFrame):
    wb = openpyxl.Workbook()

    # Settings sheet
    ws_set = wb.active
    ws_set.title = "Settings"
    ws_set["A1"] = "Default_Markup_Points"
    ws_set["B1"] = float(default_markup_points)

    ws_set["A2"] = "LP_Rate_per_1M_per_side"
    ws_set["B2"] = float(lp_rate_per_1m_per_side)

    ws_set["A3"] = "LP_Sides"
    ws_set["B3"] = int(lp_sides)

    ws_set["A4"] = "IB_Type"
    ws_set["B4"] = ib_type

    ws_set["A5"] = "IB_Fixed_per_lot"
    ws_set["B5"] = float(ib_fixed_per_lot)

    ws_set["A6"] = "IB_Pointwise_Points"
    ws_set["B6"] = float(ib_pointwise_points)

    # Report sheet
    ws = wb.create_sheet("Report")

    cols = list(report_df.columns)
    ws.append(cols)

    # Write rows with formulas for key columns
    # Column letters helper
    def col_letter(n):
        # 1-indexed
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    # Column index map
    cidx = {name: i + 1 for i, name in enumerate(cols)}
    # Required for formulas:
    # Lots, FX_Profit_to_USD, PointValue_Profit_perlot, Markup_Points_Used, Notional_USD
    # We'll write values and then formula cells for:
    # PointValue_USD_perlot, Markup_USD, LP_Commission_USD, IB_Commission_USD, Brokerage_USD

    for i, row in report_df.iterrows():
        excel_row = i + 2  # because header is row 1

        # Base values (write as-is)
        ws.append([row.get(c, "") for c in cols])

        # Build cell refs
        lots_cell = f"{col_letter(cidx['Lots'])}{excel_row}"
        fx_cell = f"{col_letter(cidx['FX_Profit_to_USD'])}{excel_row}"
        pv_profit_cell = f"{col_letter(cidx['PointValue_Profit_perlot'])}{excel_row}"
        mp_cell = f"{col_letter(cidx['Markup_Points_Used'])}{excel_row}"
        notional_usd_cell = f"{col_letter(cidx['Notional_USD'])}{excel_row}"

        # PointValue_USD_perlot = PointValue_Profit_perlot * FX
        if "PointValue_USD_perlot" in cidx:
            cell = f"{col_letter(cidx['PointValue_USD_perlot'])}{excel_row}"
            ws[cell].value = f"={pv_profit_cell}*{fx_cell}"

        # Markup_USD = PointValue_USD_perlot * Markup_Points_Used * Lots
        if "Markup_USD" in cidx and "PointValue_USD_perlot" in cidx:
            pv_usd_cell = f"{col_letter(cidx['PointValue_USD_perlot'])}{excel_row}"
            cell = f"{col_letter(cidx['Markup_USD'])}{excel_row}"
            ws[cell].value = f"={pv_usd_cell}*{mp_cell}*{lots_cell}"

        # LP_Commission_USD = (Notional_USD / 1,000,000) * LP_Rate * LP_Sides
        if "LP_Commission_USD" in cidx:
            cell = f"{col_letter(cidx['LP_Commission_USD'])}{excel_row}"
            ws[cell].value = f"=({notional_usd_cell}/1000000)*Settings!$B$2*Settings!$B$3"

        # IB_Commission_USD:
        # - Fixed: Lots * Settings!B5
        # - Point-wise: PointValue_USD_perlot * Settings!B6 * Lots
        if "IB_Commission_USD" in cidx and "PointValue_USD_perlot" in cidx:
            pv_usd_cell = f"{col_letter(cidx['PointValue_USD_perlot'])}{excel_row}"
            cell = f"{col_letter(cidx['IB_Commission_USD'])}{excel_row}"
            ws[cell].value = (
                f"=IF(Settings!$B$4=\"Fixed ($/lot)\",{lots_cell}*Settings!$B$5,"
                f"IF(Settings!$B$4=\"Point-wise\",{pv_usd_cell}*Settings!$B$6*{lots_cell},0))"
            )

        # Brokerage_USD = Markup_USD - LP_Commission_USD - IB_Commission_USD
        if "Brokerage_USD" in cidx:
            mu_cell = f"{col_letter(cidx['Markup_USD'])}{excel_row}"
            lp_cell = f"{col_letter(cidx['LP_Commission_USD'])}{excel_row}"
            ib_cell = f"{col_letter(cidx['IB_Commission_USD'])}{excel_row}"
            cell = f"{col_letter(cidx['Brokerage_USD'])}{excel_row}"
            ws[cell].value = f"={mu_cell}-{lp_cell}-{ib_cell}"

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio

st.subheader("⬇️ Download Excel (with formulas)")

st.download_button(
    "Download MarkupX Report (Formulas Included)",
    data=export_excel_with_formulas(out_df),
    file_name="MarkupX_Report_With_Formulas.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

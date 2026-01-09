import pandas as pd
import requests
import streamlit as st
from io import BytesIO

# ============================================================
# PAGE
# ============================================================
st.set_page_config(page_title="MarkupX – Market Cost Engine", layout="wide")
st.title("📊 MarkupX – Real Market Markup & Cost Engine")
st.caption("ALL symbols (FX, Indices, Metals, Energies): Profit currency → USD conversion ONLY. Base currency is NOT used.")

# ============================================================
# FX RATES (1 CCY = X USD)
# ============================================================
@st.cache_data(ttl=300)
def fx_to_usd():
    """
    Returns mapping: 1 CCY = X USD
    Tries public FX APIs. Falls back if unavailable.
    """
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate.host/latest?base=USD",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            rates = data.get("rates") or data.get("conversion_rates")  # 1 USD = X CCY
            if not rates:
                continue

            # Convert to: 1 CCY = X USD
            out = {k.upper(): (1.0 / float(v)) for k, v in rates.items() if v}
            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("⚠️ FX API unavailable. Using fallback FX rates (please verify).")
    return {
        "USD": 1.0, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0068, "AUD": 0.66,
        "CAD": 0.74, "CHF": 1.11, "NZD": 0.61, "NOK": 0.095, "SEK": 0.094,
        "SGD": 0.74, "HKD": 0.128, "ZAR": 0.055, "CNH": 0.14, "PLN": 0.25,
        "TRY": 0.03, "MXN": 0.06
    }

# ============================================================
# LIVE MARKET PRICES (Yahoo Finance public quote endpoint)
# ============================================================
def _yahoo_quote_url(symbols_csv: str) -> str:
    return f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_csv}"

@st.cache_data(ttl=60)
def yahoo_prices(symbols: list[str]) -> dict:
    """
    Returns dict: {yahoo_symbol: regularMarketPrice}
    """
    if not symbols:
        return {}

    # Yahoo endpoint supports multiple symbols in one request
    syms_csv = ",".join(symbols)
    url = _yahoo_quote_url(syms_csv)

    out = {}
    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    j = r.json()
    results = (j.get("quoteResponse") or {}).get("result") or []
    for item in results:
        s = item.get("symbol")
        px = item.get("regularMarketPrice")
        if s and px is not None:
            out[s] = float(px)
    return out

def mt5_to_yahoo_symbol(mt5_symbol: str) -> str | None:
    """
    Maps MT5 symbol → Yahoo symbol.
    - Forex/metals like EURUSD, XAUUSD => "EURUSD=X"
    - Crypto like BTCUSD => "BTC-USD"
    - Indices/Energies mapped explicitly
    """
    s = mt5_symbol.upper().strip()

    special = {
        # Indices
        "NAS100": "^NDX",
        "US500": "^GSPC",
        "SP500": "^GSPC",
        "US30": "^DJI",
        "DJIUSD": "^DJI",
        "GER30": "^GDAXI",
        "DE30": "^GDAXI",
        "FRA40": "^FCHI",
        "UK100": "^FTSE",
        "JP225": "^N225",
        "HK50": "^HSI",

        # Energies
        "USOIL": "CL=F",
        "WTI": "CL=F",
        "UKOIL": "BZ=F",
        "BRENT": "BZ=F",

        # Metals (Yahoo supports XAUUSD=X / XAGUSD=X)
        "XAUUSD": "XAUUSD=X",
        "XAGUSD": "XAGUSD=X",

        # Crypto
        "BTCUSD": "BTC-USD",
        "ETHUSD": "ETH-USD",
    }
    if s in special:
        return special[s]

    # If looks like FX pair (6 letters)
    if len(s) == 6 and s.isalpha():
        return f"{s}=X"

    # If already a Yahoo style provided
    if "=X" in s or s.startswith("^") or s.endswith("-USD") or s.endswith("=F"):
        return s

    return None

# ============================================================
# EXCEL EXPORT
# ============================================================
def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()

# ============================================================
# SIDEBAR SETTINGS
# ============================================================
with st.sidebar:
    st.header("Settings")

    lots_default = st.number_input("Lots (default)", min_value=0.01, value=1.00, step=0.01)
    markup_points_default = st.number_input("Markup (points default)", min_value=0.0, value=20.0, step=1.0)

    st.divider()
    st.subheader("LP Commission")
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides (LP only)", [1, 2], index=1)

    st.divider()
    st.subheader("IB Commission (NO SIDES used)")
    ib_mode = st.selectbox("IB type", ["None", "Fixed ($ per lot)", "Point-wise (points)"], index=0)

    ib_fixed_per_lot = 0.0
    ib_points = 0.0
    if ib_mode == "Fixed ($ per lot)":
        ib_fixed_per_lot = st.number_input("IB fixed ($ per lot)", min_value=0.0, value=10.0, step=0.5)
    elif ib_mode == "Point-wise (points)":
        ib_points = st.number_input("IB points", min_value=0.0, value=0.0, step=1.0)

    st.divider()
    use_live_prices = st.toggle("Use LIVE market prices (Yahoo)", value=True)
    st.caption("If Yahoo doesn’t support a symbol, app will keep file price if provided.")

    st.divider()
    show_rows = st.number_input("Show first N rows", min_value=50, max_value=5000, value=500, step=50)

# ============================================================
# FILE UPLOAD
# ============================================================
file = st.file_uploader("📥 Upload Symbol Sheet (Excel)", type=["xlsx"])
if not file:
    st.info("Upload Excel with columns: Symbol Name, Digits, Profit Currency, Contract Size (optional: Current Price, Lots, MarkupPoints)")
    st.stop()

df = pd.read_excel(file)
df.columns = [str(c).strip() for c in df.columns]

required = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

# Normalize / types
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["Symbol"] = df["Symbol Name"].str.upper().str.strip()

df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# Optional columns
if "Lots" not in df.columns:
    df["Lots"] = float(lots_default)
else:
    df["Lots"] = pd.to_numeric(df["Lots"], errors="coerce").fillna(float(lots_default))

if "MarkupPoints" not in df.columns:
    df["MarkupPoints"] = float(markup_points_default)
else:
    df["MarkupPoints"] = pd.to_numeric(df["MarkupPoints"], errors="coerce").fillna(float(markup_points_default))

# If file has Current Price, use it as fallback
if "Current Price" in df.columns:
    df["Current Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
    df["Price"] = df["Current Price"].copy()
    df["Price_Source"] = "file"
else:
    df["Price"] = pd.NA
    df["Price_Source"] = "missing"

# ============================================================
# FX (Profit currency only)
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# LIVE PRICE FETCH (Yahoo)
# ============================================================
if use_live_prices:
    df["Yahoo_Symbol"] = df["Symbol"].apply(mt5_to_yahoo_symbol)
    yahoo_list = [s for s in df["Yahoo_Symbol"].dropna().unique().tolist() if s]

    live = {}
    failed_fetch = None
    try:
        live = yahoo_prices(yahoo_list)
    except Exception as e:
        failed_fetch = str(e)

    if failed_fetch:
        st.warning(f"⚠️ Live price fetch failed. Using file prices if provided. Details: {failed_fetch}")
    else:
        # Apply live prices where available
        updated = 0
        missing_live = []
        for idx, row in df.iterrows():
            ysym = row.get("Yahoo_Symbol")
            if not ysym:
                continue
            px = live.get(ysym)
            if px is None:
                missing_live.append(row["Symbol"])
                continue
            df.at[idx, "Price"] = px
            df.at[idx, "Price_Source"] = f"yahoo:{ysym}"
            updated += 1

        st.sidebar.success(f"Live prices updated: {updated}")
        if missing_live:
            st.sidebar.warning("Yahoo missing for: " + ", ".join(missing_live[:12]) + (" ..." if len(missing_live) > 12 else ""))

# ============================================================
# CALCULATIONS (Profit → USD ONLY)
# ============================================================
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

# Point value per lot in PROFIT currency (generic MT5-style)
df["PointValue_Profit_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"]

# Point value per lot in USD
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * df["MarkupPoints"] * df["Lots"]
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional
df["Notional_Profit"] = df["Price"].fillna(0.0) * df["Contract Size"].fillna(0.0) * df["Lots"]
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission (uses sides)
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# IB Commission (NO SIDES)
if ib_mode == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = float(ib_fixed_per_lot) * df["Lots"]
elif ib_mode == "Point-wise (points)":
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * float(ib_points) * df["Lots"]
else:
    df["IB_Commission_USD"] = 0.0

# Brokerage (your correct formula)
df["Brokerage_USD"] = df["Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]

# ============================================================
# REPORT
# ============================================================
report_cols = [
    "Symbol Name",
    "Profit Currency",
    "Price",
    "Price_Source",
    "Digits",
    "Contract Size",
    "Lots",
    "MarkupPoints",
    "Profit_to_USD",
    "PointSize",
    "PointValue_Profit_perLot",
    "PointValue_USD_perLot",
    "Markup_Profit",
    "Markup_USD",
    "Notional_Profit",
    "Notional_USD",
    "LP_Commission_USD",
    "IB_Commission_USD",
    "Brokerage_USD",
]
report = df[report_cols].copy()

st.subheader("Report (USD)")
st.dataframe(report.head(int(show_rows)), use_container_width=True, height=650)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

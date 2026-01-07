import re
from io import BytesIO

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="MarkupX – MT5 Cost Engine", layout="wide")

# ============================================================
# FX RATES (SAFE + FALLBACK)
# Returns mapping: 1 CCY = X USD
# ============================================================
@st.cache_data(ttl=3600)
def fx_to_usd() -> dict:
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate.host/latest?base=USD",
    ]

    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()

            if isinstance(data, dict):
                if "rates" in data and isinstance(data["rates"], dict):
                    rates = data["rates"]  # 1 USD = X CCY
                elif "conversion_rates" in data and isinstance(data["conversion_rates"], dict):
                    rates = data["conversion_rates"]  # 1 USD = X CCY
                else:
                    continue

                fx = {ccy: (1.0 / float(val)) for ccy, val in rates.items() if val}
                fx["USD"] = 1.0
                return fx

        except Exception:
            continue

    st.warning("FX API unavailable. Using fallback USD rates (verify for accuracy).")
    return {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.26,
        "JPY": 0.0068,
        "AUD": 0.66,
        "CAD": 0.74,
        "CHF": 1.11,
        "NZD": 0.61,
        "SGD": 0.74,
        "ZAR": 0.054,
        "HKD": 0.128,
        "NOK": 0.095,
        "SEK": 0.096,
        "PLN": 0.25,
        "TRY": 0.032,
        "MXN": 0.058,
        "CNH": 0.14,
    }


# ============================================================
# LIVE PRICES (YAHOO FINANCE)
# Returns mapping: MT5 SymbolKey -> live price
# ============================================================
@st.cache_data(ttl=30)
def get_live_prices_yf(symbol_to_yahoo: dict) -> dict:
    """
    symbol_to_yahoo: {"NAS100":"^NDX", "XAUUSD":"GC=F", ...}
    Returns: {"NAS100": 17500.0, "XAUUSD": 2045.3, ...}
    """
    if not symbol_to_yahoo:
        return {}

    # Unique tickers
    tickers = sorted({t for t in symbol_to_yahoo.values() if t})
    if not tickers:
        return {}

    # Download last close (1m bars) - good enough for "near real-time"
    data = yf.download(tickers=tickers, period="1d", interval="1m", progress=False)

    # Build latest close dict by ticker
    last_by_ticker = {}

    try:
        # Multi-ticker format -> columns are MultiIndex
        if isinstance(data.columns, pd.MultiIndex):
            close_df = data["Close"].ffill()
            if not close_df.empty:
                last_row = close_df.iloc[-1]
                last_by_ticker = last_row.to_dict()
        else:
            # Single ticker format -> normal columns
            close_series = data["Close"].ffill()
            if not close_series.empty:
                last_by_ticker[tickers[0]] = float(close_series.iloc[-1])
    except Exception:
        return {}

    # Invert mapping ticker->symbol
    ticker_to_symbol = {}
    for sym, tk in symbol_to_yahoo.items():
        if tk:
            ticker_to_symbol[tk] = sym

    out = {}
    for tk, px in last_by_ticker.items():
        if tk in ticker_to_symbol and pd.notna(px):
            out[ticker_to_symbol[tk]] = float(px)
    return out


# ============================================================
# HELPERS
# ============================================================
def parse_symbol(sym: str):
    """
    Best-effort parse for FX pairs (EURUSD, GBPJPY etc.).
    Returns (Base, Quote) or (None, None) for non-FX.
    """
    s = str(sym).upper().strip()

    parts = re.findall(r"[A-Z]{3}", s)
    if len(parts) >= 2:
        return parts[0], parts[1]

    letters = re.sub(r"[^A-Z]", "", s)
    if len(letters) >= 6:
        return letters[:3], letters[3:6]

    return None, None


def to_excel(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()


def parse_manual_prices(text: str) -> dict:
    """
    Lines like:
    NAS100=17500
    USOIL=72.5
    XAUUSD=2040.2
    """
    out = {}
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().upper()
        try:
            out[k] = float(v.strip())
        except Exception:
            pass
    return out


def norm_key(x) -> str:
    return str(x).upper().strip()


# ============================================================
# UI
# ============================================================
st.title("📊 MarkupX – MT5 Markup, Notional & LP Commission")
st.caption("Per $1M USD notional • Supports FX + Indices + Energies + Metals • Live FX + Live Prices")

with st.sidebar:
    st.header("Settings")
    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides", [1, 2], index=1)

    st.divider()
    st.subheader("Live prices")
    use_live_prices = st.toggle("Use Yahoo Finance live prices", value=True)
    st.caption("If broker prices differ, prefer MT5 Bid/Ask export instead of Yahoo.")

    st.divider()
    st.subheader("Manual Prices (optional)")
    st.caption("Format: NAS100=17500 (one per line)")
    manual_prices_text = st.text_area("Paste manual prices", height=120)

spec_file = st.file_uploader("Upload MT5 Symbol Export (Excel .xlsx)", type=["xlsx"], key="spec")
price_file = st.file_uploader(
    "Upload Prices file (optional) - columns: Symbol Name, Price",
    type=["xlsx", "csv"],
    key="price",
)

if not spec_file:
    st.info("Required columns: Symbol Name, Digits, Profit Currency, Contract Size")
    st.stop()

# ============================================================
# READ SPECS
# ============================================================
df = pd.read_excel(spec_file)

required = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns in MT5 export: {missing}")
    st.stop()

df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["SymbolKey"] = df["Symbol Name"].map(norm_key)
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()

df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# ============================================================
# FX MAP
# ============================================================
fx = fx_to_usd()
df["Base"], df["Quote"] = zip(*df["Symbol Name"].map(parse_symbol))

df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)
df["Base_to_USD"] = df["Base"].map(fx)  # NaN for non-FX

# ============================================================
# POINT VALUE + MARKUP
# Fix: float base so negative powers work
# ============================================================
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)
df["PointValue_ProfitCcy_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"]
df["PointValue_USD_perLot"] = df["PointValue_ProfitCcy_perLot"] * df["Profit_to_USD"]

df["Markup_USD"] = df["PointValue_USD_perLot"] * float(markup_points) * float(lots)

# ============================================================
# PRICES - Priority:
# 1) Uploaded prices file
# 2) Manual pasted prices
# 3) Live prices (Yahoo)
# ============================================================
price_map = {}

# 1) Uploaded prices file
if price_file is not None:
    try:
        if str(price_file.name).lower().endswith(".csv"):
            pdf = pd.read_csv(price_file)
        else:
            pdf = pd.read_excel(price_file)

        if "Symbol Name" in pdf.columns and "Price" in pdf.columns:
            pdf["SymbolKey"] = pdf["Symbol Name"].map(norm_key)
            pdf["Price"] = pd.to_numeric(pdf["Price"], errors="coerce")
            price_map.update(dict(zip(pdf["SymbolKey"], pdf["Price"])))
        else:
            st.warning("Prices file ignored: it must contain 'Symbol Name' and 'Price' columns.")
    except Exception as e:
        st.warning(f"Could not read Prices file: {e}")

# 2) Manual pasted prices
price_map.update(parse_manual_prices(manual_prices_text))

# 3) Live prices via Yahoo
# IMPORTANT: You MUST map your MT5 symbols -> Yahoo tickers here.
# Add your broker symbols into this dict (keys must match your MT5 Symbol Name).
default_symbol_to_yahoo = {
    # Indices (examples)
    "NAS100": "^NDX",
    "SP500": "^GSPC",
    "US500": "^GSPC",
    "WS30": "^DJI",
    "DJIUSD": "^DJI",
    "US30": "^DJI",

    # Energies (examples)
    "USOIL": "CL=F",
    "UKOIL": "BZ=F",
    "WTI": "CL=F",
    "BRENT": "BZ=F",

    # Metals (examples)
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",

    # Crypto (examples)
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}

live_price_map = {}
if use_live_prices:
    try:
        # Build mapping only for symbols present in current file (faster)
        present = set(df["SymbolKey"].tolist())
        filtered = {k: v for k, v in default_symbol_to_yahoo.items() if norm_key(k) in present and v}
        live_price_map = get_live_prices_yf({norm_key(k): v for k, v in filtered.items()})
        if live_price_map:
            st.sidebar.caption(f"Live prices loaded: {len(live_price_map)}")
        else:
            st.sidebar.caption("Live prices: 0 (mapping missing or Yahoo blocked)")
    except Exception:
        st.sidebar.caption("Live prices failed (Yahoo blocked).")

# Start Price column
df["Price"] = df["SymbolKey"].map(price_map)

# Fill with live prices if missing
if use_live_prices and live_price_map:
    df["Price"] = df["Price"].fillna(df["SymbolKey"].map(live_price_map))

# ============================================================
# NOTIONAL LOGIC
# FX: Notional = ContractSize * Lots * Base_to_USD
# CFDs/Indices/Energies/Metals: Notional = Price * ContractSize * Lots * Profit_to_USD
# ============================================================
is_fx = df["Base"].notna() & df["Base_to_USD"].notna()

df["Notional_USD"] = 0.0

# FX
df.loc[is_fx, "Notional_USD"] = (
    df.loc[is_fx, "Contract Size"].fillna(0.0) * float(lots) * df.loc[is_fx, "Base_to_USD"].fillna(0.0)
)

# Non-FX / CFDs
non_fx = ~is_fx
df.loc[non_fx, "Notional_USD"] = (
    df.loc[non_fx, "Price"].fillna(0.0)
    * df.loc[non_fx, "Contract Size"].fillna(0.0)
    * float(lots)
    * df.loc[non_fx, "Profit_to_USD"].fillna(1.0)
)

# LP Commission per $1M per side
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

# ============================================================
# WARNINGS
# ============================================================
missing_prices = df.loc[non_fx & df["Price"].isna(), "Symbol Name"].unique().tolist()
if missing_prices:
    st.warning(
        "Missing LIVE/Manual/Uploaded price for these non-FX symbols (Notional & LP commission will be 0): "
        + ", ".join(missing_prices)
        + "\n\nFix: Add them in mapping (default_symbol_to_yahoo) or upload/paste prices."
    )

# ============================================================
# REPORT
# ============================================================
report = df[
    [
        "Symbol Name",
        "Profit Currency",
        "Base",
        "Quote",
        "Digits",
        "Contract Size",
        "Price",
        "PointValue_USD_perLot",
        "Markup_USD",
        "Notional_USD",
        "LP_Commission_USD",
        "Total_Cost_USD",
    ]
].copy()

st.subheader("Report (USD)")
st.dataframe(report, use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.markdown("### Notes")
st.write(
    "- **FX pairs** notional uses base currency conversion.\n"
    "- **Indices/Energies/Metals/CFDs** notional uses **live Price × ContractSize × Lots**.\n"
    "- For best accuracy, prefer MT5 Bid/Ask export if available."
)

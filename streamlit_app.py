import pandas as pd
import requests
import streamlit as st
from io import BytesIO

# ============================================================
# PAGE
# ============================================================
st.set_page_config(page_title="MarkupX – Market Cost Engine", layout="wide")
st.title("📊 MarkupX – Markup / Notional / LP Commission (Profit Currency → USD)")
st.caption("For ALL symbols (FX + Indices + Metals + Energies): calculate in Profit Currency first, then convert Profit→USD.")

# ============================================================
# FX RATES: returns 1 CCY = X USD
# ============================================================
@st.cache_data(ttl=300)
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

            rates = None
            if isinstance(data, dict):
                if "rates" in data and isinstance(data["rates"], dict):
                    rates = data["rates"]  # 1 USD = X CCY
                elif "conversion_rates" in data and isinstance(data["conversion_rates"], dict):
                    rates = data["conversion_rates"]  # 1 USD = X CCY

            if not rates:
                continue

            out = {ccy.upper(): (1.0 / float(v)) for ccy, v in rates.items() if v}
            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("FX API unavailable. Using fallback FX rates (verify).")
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
# STOOQ PRICE (free market data; often delayed for some assets)
# ============================================================
@st.cache_data(ttl=60)
def stooq_last_close(stooq_symbol: str):
    """
    Returns last close price from Stooq CSV. Works for many indices/commodities/crypto.
    Example URL:
      https://stooq.com/q/l/?s=^ndx&f=sd2t2ohlcv&h&e=csv
    """
    try:
        url = f"https://stooq.com/q/l/?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        if len(lines) < 2:
            return None
        row = lines[1].split(",")
        if len(row) < 7:
            return None
        px = float(row[6])  # Close
        return px if px > 0 else None
    except Exception:
        return None

def norm_key(x) -> str:
    return str(x).upper().strip()

def parse_kv(text: str) -> dict:
    """
    KEY=VALUE per line
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
        v = v.strip()
        if k and v:
            out[k] = v
    return out

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
    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides", [1, 2], index=1)

    st.divider()
    st.subheader("Market price integration (optional)")
    fetch_market_prices = st.toggle("Override Current Price using market data", value=False)
    st.caption("Uses Stooq market prices where available. If a symbol is not supported, it keeps your Current Price.")

    st.caption("Mapping: MT5_SYMBOL=STOOQ_SYMBOL (one per line)")
    mapping_text = st.text_area(
        "Stooq mapping",
        height=200,
        value=(
            "NAS100=^ndx\n"
            "US500=^spx\n"
            "US30=^dji\n"
            "GER30=^dax\n"
            "FRA40=^cac\n"
            "UK100=^ftse\n"
            "JP225=^nkx\n"
            "HK50=^hsi\n"
            "AU200=^aord\n"
            "ES35=^ibex\n"
            "USOIL=cl.f\n"
            "UKOIL=brn.f\n"
            "USOIL=cl.f\n"
            "UKOIL=brn.f\n"
            "XAUUSD=xauusd\n"
            "XAGUSD=xagusd\n"
            "XNGUSD=ng.f\n"
            "BTCUSD=btcusd\n"
            "ETHUSD=ethusd\n"
            "USOil=cl.f\n"
            "UKOil=brn.f\n"
        ),
    )

file = st.file_uploader("Upload your Excel (Book2.xlsx format)", type=["xlsx"])

if not file:
    st.info("Upload your file with columns: Symbol Name, Current Price, Digits, Profit Currency, Contract Size")
    st.stop()

# ============================================================
# READ FILE
# ============================================================
df = pd.read_excel(file)
df.columns = [c.strip() for c in df.columns]

required = ["Symbol Name", "Current Price", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["SymbolKey"] = df["Symbol Name"].map(norm_key)

df["Current Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# ============================================================
# FX conversion (Profit currency only)
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# MARKET PRICE OVERRIDE (optional)
# ============================================================
df["Price"] = df["Current Price"].copy()
df["Price_Source"] = "file"

if fetch_market_prices:
    mapping = parse_kv(mapping_text)
    overridden = 0
    not_supported = []

    for sym in df["SymbolKey"].unique().tolist():
        stooq_sym = mapping.get(sym)
        if not stooq_sym:
            continue
        px = stooq_last_close(stooq_sym)
        if px is None:
            not_supported.append(sym)
            continue
        df.loc[df["SymbolKey"] == sym, "Price"] = px
        df.loc[df["SymbolKey"] == sym, "Price_Source"] = f"stooq:{stooq_sym}"
        overridden += 1

    st.sidebar.success(f"Market prices updated: {overridden}")
    if not_supported:
        st.sidebar.warning(f"Stooq price missing for: {', '.join(not_supported[:12])}" + (" ..." if len(not_supported) > 12 else ""))

# ============================================================
# CALCULATIONS (PROFIT currency first)
# ============================================================
# PointSize = 10^-digits (safe float)
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

# Point value per lot in PROFIT currency
df["PointValue_Profit_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"]

# Markup in PROFIT currency then USD
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * float(markup_points) * float(lots)
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional in PROFIT currency = Price × ContractSize × Lots
df["Notional_Profit"] = (
    df["Price"].fillna(0.0)
    * df["Contract Size"].fillna(0.0)
    * float(lots)
)

# Convert to USD using PROFIT currency only (your requirement)
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP commission per million USD notional
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# Total cost
df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

# ============================================================
# REPORT
# ============================================================
report = df[
    [
        "Symbol Name",
        "Profit Currency",
        "Price",
        "Price_Source",
        "Digits",
        "Contract Size",
        "PointValue_Profit_perLot",
        "Markup_Profit",
        "Markup_USD",
        "Notional_Profit",
        "Notional_USD",
        "LP_Commission_USD",
        "Total_Cost_USD",
    ]
].copy()

st.subheader("Report (USD)")
st.dataframe(report, use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.markdown("### Notes")
st.write(
    "- ✅ Base currency is NOT used at all (as you requested).\n"
    "- ✅ All symbols calculate in Profit Currency first, then convert Profit→USD.\n"
    "- ✅ Real-time market prices are optional via Stooq. If a symbol is not available on Stooq, the tool keeps your file price.\n"
    "- For 100% real-time correctness, keep updating the **Current Price** column or ensure your symbol mapping is supported by Stooq."
)

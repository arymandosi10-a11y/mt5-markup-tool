# streamlit_app.py
# ✅ Works on Streamlit Cloud (NO yfinance)
# ✅ Real-time FX rates (public API) + real-time-ish symbol prices (Stooq CSV)
# ✅ Correct notional for FX vs Indices/Energies/Metals/CFDs
# ✅ Per $1M USD notional LP commission
# ✅ Excel download

import re
from io import BytesIO

import pandas as pd
import requests
import streamlit as st

# ============================================================
# PAGE
# ============================================================
st.set_page_config(page_title="MarkupX – MT5 Cost Engine", layout="wide")
st.title("📊 MarkupX – MT5 Markup, Notional & LP Commission (USD)")
st.caption("FX uses base→USD; Indices/Energies/Metals/CFDs use live price × contract size × lots. (Per $1M notional)")

# ============================================================
# SETTINGS
# ============================================================
with st.sidebar:
    st.header("Settings")
    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides", [1, 2], index=1)

    st.divider()
    st.subheader("Live prices source (NO broker API)")
    st.caption("Uses Stooq (free). Broker prices can differ slightly. For exact broker pricing, use MT5 Bid/Ask export.")
    use_live_prices = st.toggle("Use live prices (Stooq)", value=True)

    st.divider()
    st.subheader("Symbol → Stooq mapping (editable)")
    st.caption("Format: MT5_SYMBOL=stooq_symbol (one per line)")
    st.caption("Examples: NAS100=^ndx, SP500=^spx, WS30=^dji, USOIL=cl.f, UKOIL=brn.f, XAUUSD=xauusd, XAGUSD=xagusd")
    mapping_text = st.text_area(
        "Mapping",
        value=(
            "NAS100=^ndx\n"
            "US100=^ndx\n"
            "SP500=^spx\n"
            "US500=^spx\n"
            "WS30=^dji\n"
            "US30=^dji\n"
            "DJIUSD=^dji\n"
            "USOIL=cl.f\n"
            "WTI=cl.f\n"
            "UKOIL=brn.f\n"
            "BRENT=brn.f\n"
            "XAUUSD=xauusd\n"
            "XAGUSD=xagusd\n"
            "BTCUSD=btcusd\n"
            "ETHUSD=ethusd\n"
        ),
        height=180,
    )

    st.divider()
    st.subheader("Optional")
    st.caption("Paste manual prices if needed (highest priority). Format: SYMBOL=PRICE")
    manual_prices_text = st.text_area("Manual Prices", value="", height=120)

spec_file = st.file_uploader("Upload MT5 Symbol Export (Excel .xlsx)", type=["xlsx"])

# ============================================================
# FX RATES (USD base) -> convert to "1 CCY = X USD"
# ============================================================
@st.cache_data(ttl=300)
def fx_to_usd() -> dict:
    """
    Returns dict where: 1 CCY = X USD
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

            rates = None
            if isinstance(data, dict):
                if "rates" in data and isinstance(data["rates"], dict):
                    rates = data["rates"]
                elif "conversion_rates" in data and isinstance(data["conversion_rates"], dict):
                    rates = data["conversion_rates"]

            if not rates:
                continue

            # rates: 1 USD = X CCY  =>  1 CCY = 1/X USD
            out = {ccy.upper(): (1.0 / float(v)) for ccy, v in rates.items() if v}
            out["USD"] = 1.0
            return out

        except Exception:
            continue

    st.warning("FX API unavailable. Using fallback FX (verify).")
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
# STOOQ LIVE PRICES (CSV)
# ============================================================
@st.cache_data(ttl=60)
def stooq_last_close(stooq_symbol: str):
    """
    Pulls last close from Stooq CSV.
    Returns float or None.
    """
    try:
        # Example: https://stooq.com/q/l/?s=^ndx&f=sd2t2ohlcv&h&e=csv
        url = f"https://stooq.com/q/l/?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
        r = requests.get(url, timeout=15)
        r.raise_for_status()

        lines = r.text.strip().splitlines()
        if len(lines) < 2:
            return None

        # Header: Symbol,Date,Time,Open,High,Low,Close,Volume
        row = lines[1].split(",")
        if len(row) < 7:
            return None

        close_val = row[6]
        px = float(close_val)
        if px <= 0:
            return None
        return px
    except Exception:
        return None

def parse_mapping(text: str) -> dict:
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

def parse_manual_prices(text: str) -> dict:
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

def parse_fx_pair(symbol: str):
    """
    Best-effort parse for FX-like strings: EURUSD, GBPJPY, etc.
    Returns (base, quote) or (None, None)
    """
    s = str(symbol).upper().strip()
    # Extract first two 3-letter groups from symbol
    parts = re.findall(r"[A-Z]{3}", s)
    if len(parts) >= 2:
        return parts[0], parts[1]
    # Fallback: letters only
    letters = re.sub(r"[^A-Z]", "", s)
    if len(letters) >= 6:
        return letters[:3], letters[3:6]
    return None, None

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()

# ============================================================
# MAIN
# ============================================================
if not spec_file:
    st.info("Upload your MT5 symbol export (.xlsx). Required columns: Symbol Name, Digits, Profit Currency, Contract Size")
    st.stop()

df = pd.read_excel(spec_file)
df.columns = [c.strip() for c in df.columns]

required = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

# Normalize
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["SymbolKey"] = df["Symbol Name"].str.upper()
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# FX map
fx = fx_to_usd()

# Parse base/quote & classify FX ONLY if both are valid currencies in FX table
bases, quotes = [], []
is_fx_list = []
for sym in df["Symbol Name"].tolist():
    b, q = parse_fx_pair(sym)
    b_ok = b in fx if b else False
    q_ok = q in fx if q else False
    is_fx = bool(b_ok and q_ok)
    bases.append(b if is_fx else None)
    quotes.append(q if is_fx else None)
    is_fx_list.append(is_fx)

df["Base"] = bases
df["Quote"] = quotes
df["Is_FX"] = is_fx_list

# Profit currency conversion to USD
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# Base currency conversion (for FX)
df["Base_to_USD"] = df["Base"].map(fx)

# Point size (fix: float base)
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

# Point value per lot (in ProfitCcy) then to USD
df["PointValue_ProfitCcy_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"]
df["PointValue_USD_perLot"] = df["PointValue_ProfitCcy_perLot"] * df["Profit_to_USD"]

# Markup in USD
df["Markup_USD"] = df["PointValue_USD_perLot"] * float(markup_points) * float(lots)

# -------------------------
# LIVE PRICES (for non-FX)
# -------------------------
mapping = parse_mapping(mapping_text)
manual_prices = parse_manual_prices(manual_prices_text)

df["Price_Source"] = ""
df["Price"] = pd.NA

# Manual prices first (highest priority)
df.loc[df["SymbolKey"].isin(manual_prices.keys()), "Price"] = df["SymbolKey"].map(manual_prices)
df.loc[df["SymbolKey"].isin(manual_prices.keys()), "Price_Source"] = "manual"

# Live prices for remaining non-FX
if use_live_prices:
    # only for symbols not FX and price is missing
    need = df.loc[(~df["Is_FX"]) & (df["Price"].isna()), "SymbolKey"].unique().tolist()
    for symkey in need:
        stooq_sym = mapping.get(symkey)
        if not stooq_sym:
            continue
        px = stooq_last_close(stooq_sym)
        if px is None:
            continue
        df.loc[df["SymbolKey"] == symkey, "Price"] = px
        df.loc[df["SymbolKey"] == symkey, "Price_Source"] = f"stooq:{stooq_sym}"

# -------------------------
# NOTIONAL USD
# FX: ContractSize * Lots * Base_to_USD
# CFD: Price * ContractSize * Lots * Profit_to_USD
# -------------------------
df["Notional_USD"] = 0.0

# FX
df.loc[df["Is_FX"], "Notional_USD"] = (
    df.loc[df["Is_FX"], "Contract Size"].fillna(0.0)
    * float(lots)
    * df.loc[df["Is_FX"], "Base_to_USD"].fillna(0.0)
)

# Non-FX / CFDs / Indices / Energies / Metals
df.loc[~df["Is_FX"], "Notional_USD"] = (
    pd.to_numeric(df.loc[~df["Is_FX"], "Price"], errors="coerce").fillna(0.0)
    * df.loc[~df["Is_FX"], "Contract Size"].fillna(0.0)
    * float(lots)
    * df.loc[~df["Is_FX"], "Profit_to_USD"].fillna(1.0)
)

# LP Commission
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# Total cost
df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

# Warnings for missing prices
missing_prices = df.loc[(~df["Is_FX"]) & (pd.to_numeric(df["Price"], errors="coerce").isna()), "Symbol Name"].unique().tolist()
if missing_prices:
    st.warning(
        "Missing live/manual price for these non-FX symbols (Notional & LP commission will be 0): "
        + ", ".join(missing_prices)
        + "\n\nFix: Add mapping in sidebar (SYMBOL=stooq_symbol) or paste manual prices."
    )

# Report
report = df[
    [
        "Symbol Name",
        "Profit Currency",
        "Is_FX",
        "Base",
        "Quote",
        "Digits",
        "Contract Size",
        "Price",
        "Price_Source",
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
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.markdown("### Notes")
st.write(
    "- **FX** notional uses **Base→USD** found from live FX rates.\n"
    "- **Indices/Energies/Metals/CFDs** notional uses **live Price × Contract Size × Lots × ProfitCurrency→USD**.\n"
    "- Live prices come from **Stooq** (free). If your broker prices differ, paste manual prices or export MT5 Bid/Ask."
)

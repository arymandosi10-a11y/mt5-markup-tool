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
st.caption("All calculations are based on PROFIT currency first, then converted to USD.")

# ============================================================
# FX: PROFIT CURRENCY -> USD (live)
# Returns: 1 CCY = X USD
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
# HELPERS
# ============================================================
def norm_key(x) -> str:
    return str(x).upper().strip()

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()

def parse_fx_pair(symbol: str):
    # optional; only used to "guess" FX pair when no quotes file is provided
    s = str(symbol).upper().strip()
    parts = re.findall(r"[A-Z]{3}", s)
    if len(parts) >= 2:
        return parts[0], parts[1]
    letters = re.sub(r"[^A-Z]", "", s)
    if len(letters) >= 6:
        return letters[:3], letters[3:6]
    return None, None

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("Settings")
    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides", [1, 2], index=1)

    st.divider()
    st.subheader("Real-time price source")
    st.caption("Best: Upload MT5 Quotes (Symbol + Bid/Ask or Last). Without this, indices/oil/metals notional will be 0.")
    manual_prices_text = st.text_area(
        "Optional manual prices (highest priority)\nFormat: SYMBOL=PRICE",
        value="",
        height=120,
    )

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

# ============================================================
# UPLOADS
# ============================================================
spec_file = st.file_uploader("Upload MT5 Symbol Specs (Excel .xlsx)", type=["xlsx"], key="spec")
quotes_file = st.file_uploader(
    "Upload MT5 Quotes (Excel/CSV) (optional but recommended)",
    type=["xlsx", "csv"],
    key="quotes",
)

if not spec_file:
    st.info("Upload specs file first. Required columns: Symbol Name, Digits, Profit Currency, Contract Size")
    st.stop()

df = pd.read_excel(spec_file)
df.columns = [c.strip() for c in df.columns]

required = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns in specs: {missing}")
    st.stop()

df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["SymbolKey"] = df["Symbol Name"].map(norm_key)
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# If MT5 export has Point/Tick Size column, use it
point_col = None
for c in ["Point", "Tick Size", "TickSize", "Tick size"]:
    if c in df.columns:
        point_col = c
        break

fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# PRICE MAP (REAL-TIME)
# Priority: Manual > Quotes upload > (none)
# ============================================================
manual_prices = parse_manual_prices(manual_prices_text)

quotes_map = {}
quotes_source = None

if quotes_file is not None:
    try:
        if str(quotes_file.name).lower().endswith(".csv"):
            qdf = pd.read_csv(quotes_file)
        else:
            qdf = pd.read_excel(quotes_file)

        qdf.columns = [c.strip() for c in qdf.columns]

        # Acceptable formats:
        # 1) Symbol Name, Bid, Ask
        # 2) Symbol Name, Last
        # 3) Symbol, Bid, Ask / Symbol, Last
        sym_col = "Symbol Name" if "Symbol Name" in qdf.columns else ("Symbol" if "Symbol" in qdf.columns else None)
        if sym_col is None:
            raise ValueError("Quotes file must have column 'Symbol Name' or 'Symbol'.")

        qdf["SymbolKey"] = qdf[sym_col].map(norm_key)

        if "Last" in qdf.columns:
            qdf["Price"] = pd.to_numeric(qdf["Last"], errors="coerce")
            quotes_source = "quotes:last"
        elif "Bid" in qdf.columns and "Ask" in qdf.columns:
            bid = pd.to_numeric(qdf["Bid"], errors="coerce")
            ask = pd.to_numeric(qdf["Ask"], errors="coerce")
            qdf["Price"] = ((bid + ask) / 2.0)
            quotes_source = "quotes:mid"
        else:
            raise ValueError("Quotes file must have (Bid & Ask) OR (Last).")

        qdf = qdf.dropna(subset=["Price"])
        quotes_map = dict(zip(qdf["SymbolKey"], qdf["Price"]))

    except Exception as e:
        st.warning(f"Could not read quotes file: {e}")

# Build final Price column
df["Price"] = pd.NA
df["Price_Source"] = ""

# Manual
mask_manual = df["SymbolKey"].isin(manual_prices.keys())
df.loc[mask_manual, "Price"] = df.loc[mask_manual, "SymbolKey"].map(manual_prices)
df.loc[mask_manual, "Price_Source"] = "manual"

# Quotes (only where price still missing)
mask_quotes = df["Price"].isna() & df["SymbolKey"].isin(quotes_map.keys())
df.loc[mask_quotes, "Price"] = df.loc[mask_quotes, "SymbolKey"].map(quotes_map)
df.loc[mask_quotes, "Price_Source"] = quotes_source if quotes_source else "quotes"

# ============================================================
# POINT SIZE / POINT VALUE (Profit currency first)
# ============================================================
if point_col:
    df["PointSize"] = pd.to_numeric(df[point_col], errors="coerce").fillna(0.0)
else:
    df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

# Point value per lot in PROFIT currency (common MT5 approximation)
df["PointValue_Profit_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"]

# Convert to USD
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup USD
df["Markup_USD"] = df["PointValue_USD_perLot"] * float(markup_points) * float(lots)

# ============================================================
# NOTIONAL (Profit currency first)  <-- NEEDS PRICE
# Notional_Profit = Price * ContractSize * Lots
# Notional_USD = Notional_Profit * Profit_to_USD
# ============================================================
df["Notional_Profit"] = (
    pd.to_numeric(df["Price"], errors="coerce").fillna(0.0)
    * df["Contract Size"].fillna(0.0)
    * float(lots)
)

df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# Total
df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

# Warnings if prices missing
missing_prices = df.loc[pd.to_numeric(df["Price"], errors="coerce").isna(), "Symbol Name"].unique().tolist()
if missing_prices:
    st.error(
        "Real-time PRICE is missing for these symbols, so Notional/LP commission is wrong (0). "
        "Upload MT5 Quotes (Bid/Ask or Last), or paste manual prices:\n\n"
        + ", ".join(missing_prices)
    )

# ============================================================
# REPORT
# ============================================================
report = df[
    [
        "Symbol Name",
        "Profit Currency",
        "Digits",
        "Contract Size",
        "Price",
        "Price_Source",
        "PointSize",
        "PointValue_Profit_perLot",
        "PointValue_USD_perLot",
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

st.markdown("### What you must provide for REAL-TIME accuracy")
st.write(
    "- **Specs file** gives Digits/ContractSize/ProfitCurrency.\n"
    "- **Real-time Notional needs Price**.\n"
    "- Best source = **MT5 Quotes export** (Symbol + Bid/Ask or Last). This matches broker feed.\n"
    "- Public web prices will never perfectly match broker indices/oil/metals pricing."
)

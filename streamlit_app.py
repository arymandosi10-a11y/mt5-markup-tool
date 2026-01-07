import re
from io import BytesIO

import pandas as pd
import requests
import streamlit as st

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="MarkupX – MT5 Cost Engine", layout="wide")

# ============================================================
# FX RATES (SAFE + FALLBACK)
#   Returns mapping: 1 unit of CCY = X USD
# ============================================================
@st.cache_data(ttl=3600)
def fx_to_usd() -> dict:
    urls = [
        "https://open.er-api.com/v6/latest/USD",          # most stable
        "https://api.exchangerate.host/latest?base=USD",  # fallback
    ]

    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()

            # Different APIs use different keys
            if isinstance(data, dict):
                if "rates" in data and isinstance(data["rates"], dict):
                    rates = data["rates"]  # 1 USD = X CCY
                elif "conversion_rates" in data and isinstance(data["conversion_rates"], dict):
                    rates = data["conversion_rates"]  # 1 USD = X CCY
                else:
                    continue

                # Convert to: 1 CCY = X USD
                fx = {ccy: (1 / val) for ccy, val in rates.items() if val}
                fx["USD"] = 1.0
                return fx

        except Exception:
            continue

    # Absolute fallback - app will never crash
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
# HELPERS
# ============================================================
def parse_symbol(sym: str):
    """
    Best-effort parsing for common MT5 symbols:
    EURUSD, GBPJPY, XAUUSD, BTCUSD, US100, etc.

    For FX pairs, returns (Base, Quote)
    For non-FX, returns (None, ProfitCurrencyGuess)
    """
    s = (sym or "").upper().strip()

    # Try strict 3+3 currency parsing (EURUSD, GBPJPY, etc.)
    parts = re.findall(r"[A-Z]{3}", s)
    if len(parts) >= 2:
        return parts[0], parts[1]

    # Try stripping non-letters then split into 6 if possible
    letters = re.sub(r"[^A-Z]", "", s)
    if len(letters) >= 6:
        return letters[:3], letters[3:6]

    # Not a standard FX pair (indices, metals with suffix, etc.)
    return None, None


def to_excel(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()


def safe_num(series):
    return pd.to_numeric(series, errors="coerce")


# ============================================================
# UI
# ============================================================
st.title("📊 MarkupX – MT5 Markup & LP Commission Engine")
st.caption("Automated markup + notional + LP commission (per $1M USD notional). All outputs in USD.")

with st.sidebar:
    st.header("Settings")
    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.00, step=1.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.00, step=0.5)
    sides = st.selectbox("Sides", [1, 2], index=1)
    st.divider()
    st.caption("Tip: If FX API fails, app uses fallback rates so it never stops.")

uploaded = st.file_uploader("Upload MT5 Symbol Export (Excel .xlsx)", type=["xlsx"])

if not uploaded:
    st.info("Upload your MT5 export file. Required columns: **Symbol Name, Digits, Profit Currency, Contract Size**")
    st.stop()

# ============================================================
# READ DATA
# ============================================================
try:
    df = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Cannot read the Excel file: {e}")
    st.stop()

required_cols = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"Missing required column(s): {missing}")
    st.stop()

# Clean / normalize
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Digits"] = safe_num(df["Digits"])
df["Contract Size"] = safe_num(df["Contract Size"])

# ============================================================
# FX MAP
# ============================================================
fx = fx_to_usd()

# Add Base/Quote if possible
bases, quotes = zip(*df["Symbol Name"].map(parse_symbol))
df["Base"] = bases
df["Quote"] = quotes

# ============================================================
# CALCULATIONS
# ============================================================
# Point size = 10^-digits
df["PointSize"] = 10 ** (-df["Digits"])

# Point value per lot in PROFIT currency:
# Common approximation: contract_size * point_size
df["PointValue_ProfitCcy_perLot"] = df["Contract Size"] * df["PointSize"]

# Profit Currency -> USD factor (1 ProfitCcy = X USD)
df["Profit_to_USD"] = df["Profit Currency"].map(fx)

# For notional conversion we need base->USD (for FX pairs)
df["Base_to_USD"] = df["Base"].map(fx)

# Handle unknown currencies gracefully
unknown_profit = df[df["Profit_to_USD"].isna()]["Profit Currency"].unique().tolist()
unknown_base = df[df["Base"].notna() & df["Base_to_USD"].isna()]["Base"].unique().tolist()

# If Profit currency is unknown, assume 1 (still show warning)
df["Profit_to_USD"] = df["Profit_to_USD"].fillna(1.0)

# If base currency is unknown (non-FX symbols), notional may be wrong; set to 0 and warn
df["Base_to_USD"] = df["Base_to_USD"].fillna(0.0)

# USD point value per lot
df["PointValue_USD_perLot"] = df["PointValue_ProfitCcy_perLot"] * df["Profit_to_USD"]

# Markup USD for given markup_points and lots
df["Markup_USD"] = df["PointValue_USD_perLot"] * markup_points * lots

# Notional USD:
# For FX: ContractSize (in base ccy units) * lots * base_to_usd
df["Notional_USD"] = df["Contract Size"] * lots * df["Base_to_USD"]

# LP Commission USD: (Notional / 1M) * rate * sides
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * lp_rate * float(sides)

# Total cost USD
df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

# ============================================================
# OUTPUT REPORT
# ============================================================
report_cols = [
    "Symbol Name",
    "Profit Currency",
    "Base",
    "Quote",
    "Digits",
    "Contract Size",
    "PointValue_USD_perLot",
    "Markup_USD",
    "Notional_USD",
    "LP_Commission_USD",
    "Total_Cost_USD",
]

report = df[report_cols].copy()

# Nice formatting
for c in ["PointValue_USD_perLot", "Markup_USD", "Notional_USD", "LP_Commission_USD", "Total_Cost_USD"]:
    report[c] = pd.to_numeric(report[c], errors="coerce")

# ============================================================
# WARNINGS (if any)
# ============================================================
if unknown_profit:
    st.warning(f"Unknown Profit Currency found (not in FX table): {unknown_profit} — treated as 1:1 to USD. Please verify.")
if unknown_base:
    st.warning(f"Unknown Base Currency found (not in FX table): {unknown_base} — Notional may be 0 for these symbols. Please verify symbol mapping.")

# ============================================================
# DISPLAY
# ============================================================
st.subheader("Report (USD)")
st.dataframe(report, use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

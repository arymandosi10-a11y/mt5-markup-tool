import re
from io import BytesIO

import pandas as pd
import requests
import streamlit as st

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="MarkupX – MT5 Cost Engine",
    layout="wide"
)

# ============================================================
# FX RATES (SAFE + FALLBACK)
# Returns: 1 CCY = X USD
# ============================================================
@st.cache_data(ttl=3600)
def fx_to_usd():
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate.host/latest?base=USD",
    ]

    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()

            if "rates" in data:
                rates = data["rates"]
            elif "conversion_rates" in data:
                rates = data["conversion_rates"]
            else:
                continue

            fx = {ccy: (1.0 / float(val)) for ccy, val in rates.items() if val}
            fx["USD"] = 1.0
            return fx

        except Exception:
            continue

    # Hard fallback (app NEVER crashes)
    st.warning("FX API unavailable. Using fallback USD rates.")
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
def parse_symbol(sym):
    s = str(sym).upper().strip()
    parts = re.findall(r"[A-Z]{3}", s)
    if len(parts) >= 2:
        return parts[0], parts[1]
    letters = re.sub(r"[^A-Z]", "", s)
    if len(letters) >= 6:
        return letters[:3], letters[3:6]
    return None, None


def to_excel(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()


# ============================================================
# UI
# ============================================================
st.title("📊 MarkupX – MT5 Markup & LP Commission Engine")
st.caption("Per $1M USD notional • All values in USD")

with st.sidebar:
    st.header("Settings")
    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0)
    sides = st.selectbox("Sides", [1, 2], index=1)

uploaded = st.file_uploader(
    "Upload MT5 Symbol Export (Excel)",
    type=["xlsx"]
)

if not uploaded:
    st.info("Required columns: Symbol Name, Digits, Profit Currency, Contract Size")
    st.stop()

# ============================================================
# READ DATA
# ============================================================
df = pd.read_excel(uploaded)

required = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

df["Symbol Name"] = df["Symbol Name"].astype(str)
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper()

df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# ============================================================
# FX MAP
# ============================================================
fx = fx_to_usd()

df["Base"], df["Quote"] = zip(*df["Symbol Name"].map(parse_symbol))

df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)
df["Base_to_USD"] = df["Base"].map(fx).fillna(0.0)

# ============================================================
# CORE FIX (THIS WAS CRASHING BEFORE)
# ============================================================
# Use FLOAT base so negative powers are allowed
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

df["PointValue_ProfitCcy_perLot"] = df["Contract Size"] * df["PointSize"]
df["PointValue_USD_perLot"] = df["PointValue_ProfitCcy_perLot"] * df["Profit_to_USD"]

df["Markup_USD"] = df["PointValue_USD_perLot"] * markup_points * lots
df["Notional_USD"] = df["Contract Size"] * lots * df["Base_to_USD"]
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * lp_rate * sides

df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

# ============================================================
# OUTPUT
# ============================================================
report = df[
    [
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
]

st.subheader("Cost Breakdown (USD)")
st.dataframe(report, use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel(report),
    file_name="MarkupX_Report.xlsx",
)

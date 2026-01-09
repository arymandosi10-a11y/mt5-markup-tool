import pandas as pd
import streamlit as st
from io import BytesIO
import requests

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="MarkupX – Real Market Markup & Cost Engine",
    layout="wide",
)

st.title("📊 MarkupX – Real Market Markup & Cost Engine")
st.caption(
    "Profit-currency → USD calculations | FX • Indices • Metals • Energies\n"
    "Base currency is NOT used. Prices are taken ONLY from uploaded Excel."
)

# ============================================================
# FX RATES (Profit Currency → USD)
# ============================================================
@st.cache_data(ttl=300)
def fx_to_usd():
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate.host/latest?base=USD",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            rates = data.get("rates") or data.get("conversion_rates")
            if rates:
                fx = {k.upper(): 1 / float(v) for k, v in rates.items() if v}
                fx["USD"] = 1.0
                return fx
        except Exception:
            pass

    st.warning("FX API unavailable. Using fallback FX rates.")
    return {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.26,
        "JPY": 0.0068,
        "AUD": 0.66,
        "CAD": 0.74,
        "CHF": 1.11,
        "NZD": 0.61,
    }

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("Settings")

    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)

    st.divider()
    st.subheader("LP Commission")
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides (LP only)", [1, 2], index=1)

    st.divider()
    st.subheader("IB Commission (NO SIDES)")
    ib_type = st.selectbox(
        "IB commission type",
        ["None", "Fixed ($ per lot)", "Point-wise (points)"],
        index=0,
    )

    ib_fixed = 0.0
    ib_points = 0.0

    if ib_type == "Fixed ($ per lot)":
        ib_fixed = st.number_input("IB fixed ($ per lot)", min_value=0.0, value=10.0, step=1.0)
    elif ib_type == "Point-wise (points)":
        ib_points = st.number_input("IB (points)", min_value=0.0, value=10.0, step=1.0)

    st.divider()
    show_rows = st.number_input("Show first N rows", min_value=10, value=500, step=10)

# ============================================================
# FILE UPLOAD
# ============================================================
file = st.file_uploader("Upload Excel (Book2.xlsx format)", type=["xlsx"])

if not file:
    st.info(
        "Excel must contain columns:\n\n"
        "• Symbol Name\n"
        "• Current Price\n"
        "• Digits\n"
        "• Profit Currency\n"
        "• Contract Size"
    )
    st.stop()

df = pd.read_excel(file)
df.columns = [c.strip() for c in df.columns]

required_cols = [
    "Symbol Name",
    "Current Price",
    "Digits",
    "Profit Currency",
    "Contract Size",
]

missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"Missing required columns: {missing}")
    st.stop()

# ============================================================
# DATA CLEANING
# ============================================================
df["Symbol"] = df["Symbol Name"].astype(str).str.upper().str.strip()
df["Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# ============================================================
# FX CONVERSION (Profit Currency ONLY)
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# CORE CALCULATIONS
# ============================================================
df["PointSize"] = 10 ** (-df["Digits"])
df["PointValue_Profit_perLot"] = df["Contract Size"] * df["PointSize"]
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * markup_points * lots
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional
df["Notional_Profit"] = df["Price"] * df["Contract Size"] * lots
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000) * lp_rate * sides

# IB Commission (NO SIDES)
if ib_type == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = ib_fixed * lots
elif ib_type == "Point-wise (points)":
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * ib_points * lots
else:
    df["IB_Commission_USD"] = 0.0

# Brokerage (FINAL)
df["Brokerage_USD"] = (
    df["Markup_USD"]
    - df["LP_Commission_USD"]
    - df["IB_Commission_USD"]
)

# ============================================================
# REPORT
# ============================================================
report = df[
    [
        "Symbol Name",
        "Profit Currency",
        "Price",
        "Digits",
        "Contract Size",
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
]

st.subheader("📑 Final Cost Report (USD)")
st.dataframe(report.head(show_rows), use_container_width=True)

# ============================================================
# DOWNLOAD
# ============================================================
def to_excel(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="MarkupX_Report")
    return buf.getvalue()

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

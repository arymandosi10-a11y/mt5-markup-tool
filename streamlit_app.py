import pandas as pd
import numpy as np
import requests
import streamlit as st
from io import BytesIO

# ============================================================
# PAGE
# ============================================================
st.set_page_config(
    page_title="MarkupX – Real Market Markup & Cost Engine",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 MarkupX – Real Market Markup & Cost Engine")
st.caption(
    "Profit-currency → USD only (Base currency NOT used). "
    "Brokerage_USD = Markup_USD - LP_Commission_USD - IB_Commission_USD"
)

# ============================================================
# FX RATES (1 CCY = X USD)
# ============================================================
@st.cache_data(ttl=300)
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
            rates = data.get("rates") or data.get("conversion_rates")
            if not rates:
                continue

            out = {k.upper(): 1.0 / float(v) for k, v in rates.items() if v}
            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("FX API unavailable. Using fallback FX rates.")
    return {"USD": 1.0, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0068, "AUD": 0.66, "CAD": 0.74, "CHF": 1.11, "NZD": 0.61}


def to_excel_bytes(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()


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
    ib_type = st.selectbox("Type", ["None", "Fixed ($ per lot)", "Point-wise (points)"], index=0)

    ib_fixed_per_lot = 0.0
    ib_points = 0.0

    if ib_type == "Fixed ($ per lot)":
        ib_fixed_per_lot = st.number_input("Fixed ($ per lot)", min_value=0.0, value=10.0, step=0.5)
    elif ib_type == "Point-wise (points)":
        ib_points = st.number_input("IB points", min_value=0.0, value=20.0, step=1.0)

    st.divider()
    show_rows = st.number_input("Show first N rows", min_value=50, value=500, step=50)

# ============================================================
# FILE UPLOAD
# ============================================================
file = st.file_uploader("Upload Excel (Book2.xlsx format)", type=["xlsx"])
if not file:
    st.info("Upload file with columns: Symbol Name, Current Price, Digits, Profit Currency, Contract Size")
    st.stop()

df = pd.read_excel(file)
df.columns = [c.strip() for c in df.columns]

required = ["Symbol Name", "Current Price", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

# Clean
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["Symbol"] = df["Symbol Name"].str.upper()
df["Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper()
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")
df["Price_Source"] = "file"

# ============================================================
# FX
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# CALCULATIONS
# ============================================================
df["PointSize"] = 10 ** (-df["Digits"])
df["PointValue_Profit_perLot"] = df["Contract Size"] * df["PointSize"]
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

df["Markup_Points"] = markup_points
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * markup_points * lots
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

df["Notional_Profit"] = df["Price"] * df["Contract Size"] * lots
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000) * lp_rate * sides

if ib_type == "None":
    df["IB_Commission_USD"] = 0.0
elif ib_type == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = ib_fixed_per_lot * lots
else:
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * ib_points * lots

df["Brokerage_USD"] = (
    df["Markup_USD"]
    - df["LP_Commission_USD"]
    - df["IB_Commission_USD"]
)

# ============================================================
# LOSS FLAGS + SUGGESTED MARKUP (NO BUFFER UI)
# ============================================================
df["Loss_Flag"] = df["Brokerage_USD"] < 0

df["Breakeven_Markup_USD"] = df["LP_Commission_USD"] + df["IB_Commission_USD"]
den = (df["PointValue_USD_perLot"] * lots).replace(0, np.nan)
df["Breakeven_Points"] = (df["Breakeven_Markup_USD"] / den).fillna(0)

df["PointStep"] = np.where(df["Digits"] <= 1, 0.1, 1.0)
df["Breakeven_Points_Rounded"] = np.ceil(df["Breakeven_Points"] / df["PointStep"]) * df["PointStep"]

df["Suggested_Markup_Points"] = np.maximum(markup_points, df["Breakeven_Points_Rounded"])
df["Suggested_Markup_USD"] = df["PointValue_USD_perLot"] * df["Suggested_Markup_Points"] * lots
df["Suggested_Brokerage_USD"] = (
    df["Suggested_Markup_USD"]
    - df["LP_Commission_USD"]
    - df["IB_Commission_USD"]
)
df["Suggested_Loss_Flag"] = df["Suggested_Brokerage_USD"] < 0

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
        "Markup_Points",
        "Markup_USD",
        "Notional_USD",
        "LP_Commission_USD",
        "IB_Commission_USD",
        "Brokerage_USD",
        "Loss_Flag",
        "Breakeven_Points_Rounded",
        "Suggested_Markup_Points",
        "Suggested_Brokerage_USD",
        "Suggested_Loss_Flag",
    ]
]

st.subheader("Report (USD)")
st.dataframe(report.head(int(show_rows)), use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

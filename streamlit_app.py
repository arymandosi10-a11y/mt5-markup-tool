import pandas as pd
import streamlit as st
from io import BytesIO
import requests

# ============================================================
# PAGE SETUP
# ============================================================
st.set_page_config(
    page_title="MarkupX – Real Market Markup & Cost Engine",
    layout="wide"
)

st.title("📊 MarkupX – Real Market Markup & Cost Engine")
st.caption(
    "Forex • Indices • Metals • Energies | "
    "Profit currency → USD only | Base currency NOT used"
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
            if not rates:
                continue
            out = {k.upper(): 1 / float(v) for k, v in rates.items() if v}
            out["USD"] = 1.0
            return out
        except Exception:
            pass

    # fallback (safety only)
    return {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.26,
        "JPY": 0.0068,
        "AUD": 0.66,
        "CAD": 0.74,
        "CHF": 1.11,
    }

# ============================================================
# SIDEBAR – SETTINGS
# ============================================================
with st.sidebar:
    st.header("Settings")

    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)

    st.divider()
    st.subheader("LP Commission")
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0)
    sides = st.selectbox("Sides (LP only)", [1, 2], index=1)

    st.divider()
    st.subheader("IB Commission (NO SIDES)")
    ib_type = st.selectbox("IB commission type", ["None", "Fixed ($ per lot)", "Pip-wise (points)"])

    ib_fixed = 0.0
    ib_points = 0.0

    if ib_type == "Fixed ($ per lot)":
        ib_fixed = st.number_input("IB fixed ($ per lot)", min_value=0.0, value=10.0)
    elif ib_type == "Pip-wise (points)":
        ib_points = st.number_input("IB points", min_value=0.0, value=20.0)

# ============================================================
# FILE UPLOAD
# ============================================================
file = st.file_uploader("Upload Excel (Book2.xlsx)", type=["xlsx"])
if not file:
    st.info(
        "Required columns:\n"
        "Symbol Name, Current Price, Digits, Profit Currency, Contract Size"
    )
    st.stop()

df = pd.read_excel(file)
df.columns = [c.strip() for c in df.columns]

required = ["Symbol Name", "Current Price", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

# ============================================================
# CLEAN & TYPES
# ============================================================
df["Symbol"] = df["Symbol Name"].astype(str).str.upper().str.strip()
df["Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce").astype(float)
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()

# ============================================================
# FX
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# CORE CALCULATIONS (FIXED)
# ============================================================
df["PointSize"] = 10.0 ** (-df["Digits"])

df["PointValue_Profit_perLot"] = df["Contract Size"] * df["PointSize"]
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

df["Markup_Profit"] = df["PointValue_Profit_perLot"] * markup_points * lots
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

df["Notional_Profit"] = df["Price"] * df["Contract Size"] * lots
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000) * lp_rate * sides

if ib_type == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = ib_fixed * lots
elif ib_type == "Pip-wise (points)":
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * ib_points * lots
else:
    df["IB_Commission_USD"] = 0.0

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

st.subheader("📄 Final Report (USD)")
st.dataframe(report, width="stretch", hide_index=True)

# ============================================================
# DOWNLOAD
# ============================================================
def to_excel_bytes(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

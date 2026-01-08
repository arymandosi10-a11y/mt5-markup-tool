import pandas as pd
import requests
import streamlit as st
from io import BytesIO

# ============================================================
# PAGE
# ============================================================
st.set_page_config(page_title="MarkupX – Market Cost Engine", layout="wide")
st.title("📊 MarkupX – Markup / Notional / LP / IB Commission (Profit → USD)")
st.caption("ALL symbols (FX, Indices, Metals, Energies): Profit currency first → USD. Base currency NOT used.")

# ============================================================
# FX RATES (1 CCY = X USD)
# ============================================================
@st.cache_data(ttl=300)
def fx_to_usd():
    """
    Returns mapping: 1 CCY = X USD
    Uses public FX APIs. If unavailable, uses a small fallback set.
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

            out = {k.upper(): 1.0 / float(v) for k, v in rates.items() if v}  # 1 CCY = X USD
            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("FX API unavailable. Using fallback FX rates (verify).")
    return {"USD": 1.0, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0068, "AUD": 0.66, "CAD": 0.74, "CHF": 1.11}

def to_excel_bytes(df: pd.DataFrame) -> bytes:
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
    st.subheader("IB Commission (NO SIDES used)")
    ib_type = st.selectbox("IB commission type", ["Fixed ($ per lot)", "Pip-wise (points)"], index=0)

    if ib_type == "Fixed ($ per lot)":
        ib_fixed_per_lot = st.number_input("IB fixed ($ per lot)", min_value=0.0, value=0.0, step=0.5)
        ib_points = 0.0
    else:
        ib_points = st.number_input("IB (points)", min_value=0.0, value=0.0, step=1.0)
        ib_fixed_per_lot = 0.0

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

# Clean / types
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["Symbol"] = df["Symbol Name"].str.upper().str.strip()

df["Current Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# ============================================================
# FX (Profit currency only)
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

# ============================================================
# PRICE (ONLY from file)
# ============================================================
df["Price"] = df["Current Price"].copy()

# ============================================================
# CALCULATIONS (Profit → USD)
# ============================================================
# Point size
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

# Point value per lot in PROFIT currency
df["PointValue_Profit_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"]

# Point value per lot in USD
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * float(markup_points) * float(lots)
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional
df["Notional_Profit"] = df["Price"].fillna(0.0) * df["Contract Size"].fillna(0.0) * float(lots)
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission ($ per 1M per side) - uses sides
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# IB Commission (NO SIDES for any type)
if ib_type == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = float(ib_fixed_per_lot) * float(lots)
else:
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * float(ib_points) * float(lots)

# Brokerage
df["Brokerage_USD"] = df["Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]

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
].copy()

st.subheader("Report (USD)")
st.dataframe(report, use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

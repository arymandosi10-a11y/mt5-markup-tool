import pandas as pd
import requests
import streamlit as st
from io import BytesIO

# ============================================================
# PAGE
# ============================================================
st.set_page_config(page_title="MarkupX – Market Cost Engine", layout="wide")

st.title("📊 MarkupX – Markup / Notional / LP / IB Commission (Profit → USD)")
st.caption(
    "ALL symbols (FX, Indices, Metals, Energies): Profit-currency → USD conversion ONLY. "
    "Base currency is NOT used."
)

# ============================================================
# FX RATES (1 CCY = X USD)
# ============================================================
@st.cache_data(ttl=300)
def fx_to_usd() -> dict:
    """
    Returns mapping: 1 CCY = X USD
    Tries a couple of public FX APIs. If unavailable, uses a small fallback.
    NOTE: These are spot FX rates for currency conversion (not broker rates).
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

            # API variations:
            # - open.er-api: conversion_rates
            # - exchangerate.host: rates
            rates = data.get("rates") or data.get("conversion_rates")  # 1 USD = X CCY
            if not rates:
                continue

            out = {}
            for k, v in rates.items():
                try:
                    v = float(v)
                    if v == 0:
                        continue
                    out[str(k).upper()] = 1.0 / v  # 1 CCY = X USD
                except Exception:
                    continue

            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("FX API unavailable. Using fallback FX rates (please verify).")
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
    st.header("Trade Settings")

    lots = st.number_input("Lots", min_value=0.01, value=1.00, step=0.01)
    markup_points = st.number_input("Markup (points)", min_value=0.0, value=20.0, step=1.0)

    st.divider()
    st.subheader("LP Commission")
    lp_rate = st.number_input("LP rate ($ per 1M per side)", min_value=0.0, value=7.0, step=0.5)
    sides = st.selectbox("Sides (LP only)", [1, 2], index=1)

    st.divider()
    st.subheader("IB Commission (NO SIDES used)")
    ib_mode = st.selectbox("Type", ["None", "Fixed ($ per lot)", "Point-wise (points)"], index=0)

    ib_fixed_per_lot = 0.0
    ib_points = 0.0

    if ib_mode == "Fixed ($ per lot)":
        ib_fixed_per_lot = st.number_input("Fixed ($ per lot)", min_value=0.0, value=10.0, step=0.5)
    elif ib_mode == "Point-wise (points)":
        st.caption("Uses: PointValue_USD_perLot × points × lots")
        ib_points = st.number_input("Point-wise points", min_value=0.0, value=0.0, step=1.0)

    st.divider()
    st.subheader("Display")
    show_rows = st.number_input("Show first N rows", min_value=10, value=500, step=50)


# ============================================================
# FILE UPLOAD
# ============================================================
file = st.file_uploader("Upload Symbol Excel (Book2.xlsx)", type=["xlsx"])
if not file:
    st.info(
        "Upload Excel with columns (Book2 format): "
        "Symbol Name, Profit Currency, Current Price (or Price), Digits, Contract Size"
    )
    st.stop()

df = pd.read_excel(file)
df.columns = [str(c).strip() for c in df.columns]

# Accept both "Price" and "Current Price"
price_col = None
if "Price" in df.columns:
    price_col = "Price"
elif "Current Price" in df.columns:
    price_col = "Current Price"
else:
    st.error("Missing required column: Price or Current Price")
    st.stop()

required = ["Symbol Name", "Profit Currency", price_col, "Digits", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.stop()

# ============================================================
# CLEAN / TYPES
# ============================================================
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["Symbol"] = df["Symbol Name"].astype(str).str.upper().str.strip()

df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")
df["Price"] = pd.to_numeric(df[price_col], errors="coerce")

# For transparency
df["Price_Source"] = "file"

# ============================================================
# FX (Profit currency only)
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx)

# If a profit currency is missing from FX feed, keep 1.0 but flag it
df["FX_Missing"] = df["Profit_to_USD"].isna()
df["Profit_to_USD"] = df["Profit_to_USD"].fillna(1.0)

# ============================================================
# CALCULATIONS (Profit → USD)
# ============================================================
# Point size
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).fillna(0.0)

# Point value per lot in PROFIT currency
# (Works for FX + indices/metals/energies if profit currency is correct)
df["PointValue_Profit_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"].fillna(0.0)

# Point value per lot in USD
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * float(markup_points) * float(lots)
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional (in profit currency using file price)
df["Notional_Profit"] = df["Price"].fillna(0.0) * df["Contract Size"].fillna(0.0) * float(lots)
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission ($ per 1M per side) - uses sides
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# IB Commission (NO SIDES for any type)
if ib_mode == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = float(ib_fixed_per_lot) * float(lots)
elif ib_mode == "Point-wise (points)":
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * float(ib_points) * float(lots)
else:
    df["IB_Commission_USD"] = 0.0

# Brokerage = Markup - LP - IB   (your requested formula)
df["Brokerage_USD"] = df["Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]

# Loss flags + analytics (NO sidebar buffer option)
df["Broker_Negative"] = df["Brokerage_USD"] < 0

# Minimum markup points needed to break-even (Brokerage >= 0)
# Brokerage = PV_USD_perLot*points*lots - LP - IB
# => points >= (LP + IB) / (PV_USD_perLot*lots)
den = (df["PointValue_USD_perLot"].replace(0, pd.NA) * float(lots))
df["Min_Markup_Points_Breakeven"] = ((df["LP_Commission_USD"] + df["IB_Commission_USD"]) / den).fillna(0.0)

# Suggested markup points = max(current markup_points, min_breakeven_points)
df["Suggested_Markup_Points"] = df["Min_Markup_Points_Breakeven"].apply(lambda x: max(float(markup_points), float(x)))

# If you applied Suggested_Markup_Points, what would brokerage be?
df["Brokerage_USD_If_Suggested"] = (
    df["PointValue_USD_perLot"] * df["Suggested_Markup_Points"] * float(lots)
    - df["LP_Commission_USD"]
    - df["IB_Commission_USD"]
)

df["Still_Negative_After_Suggested"] = df["Brokerage_USD_If_Suggested"] < 0

# ============================================================
# REPORT
# ============================================================
report_cols = [
    "Symbol Name",
    "Profit Currency",
    "Price",
    "Digits",
    "Contract Size",
    "PointSize",
    "PointValue_Profit_perLot",
    "PointValue_USD_perLot",
    "Markup_USD",
    "LP_Commission_USD",
    "IB_Commission_USD",
    "Brokerage_USD",
    "Broker_Negative",
]


report = df[report_cols].copy()

st.subheader("Report (USD)")
st.dataframe(report.head(int(show_rows)), use_container_width=True)

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

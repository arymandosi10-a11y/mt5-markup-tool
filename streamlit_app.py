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
    "Works for FX, Indices, Metals, Energies. "
    "Brokerage_USD = Markup_USD - LP_Commission_USD - IB_Commission_USD"
)

# ============================================================
# FX RATES (1 CCY = X USD)  — Real-time via public FX API
# ============================================================
@st.cache_data(ttl=300)
def fx_to_usd() -> dict:
    """
    Returns mapping: 1 CCY = X USD

    Tries multiple public FX endpoints (no API key).
    If all fail, uses a fallback set.
    """
    urls = [
        "https://open.er-api.com/v6/latest/USD",             # returns: conversion_rates
        "https://api.exchangerate.host/latest?base=USD",     # returns: rates
    ]

    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()

            # 1 USD = X CCY
            rates = data.get("rates") or data.get("conversion_rates")
            if not isinstance(rates, dict) or not rates:
                continue

            # convert to: 1 CCY = X USD
            out = {}
            for k, v in rates.items():
                if v is None:
                    continue
                v = float(v)
                if v == 0:
                    continue
                out[str(k).upper()] = 1.0 / v

            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("⚠️ FX API unavailable. Using fallback FX rates (please verify).")
    return {"USD": 1.0, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0068, "AUD": 0.66, "CAD": 0.74, "CHF": 1.11, "NZD": 0.61}


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()


def normalize_columns(cols):
    # keep original names but strip whitespace
    return [str(c).strip() for c in cols]


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
        st.caption("Uses: PointValue_USD_perLot × points × lots (NO SIDES)")
        ib_points = st.number_input("Point-wise points", min_value=0.0, value=20.0, step=1.0)

    st.divider()
    st.subheader("Loss flags + analytics")
    buffer_points = st.number_input(
        "Suggested Markup buffer (points)",
        min_value=0.0,
        value=1.0,
        step=0.5,
        help="Extra safety points added on top of breakeven to avoid negatives due to rounding/precision.",
    )

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
df.columns = normalize_columns(df.columns)

required = ["Symbol Name", "Current Price", "Digits", "Profit Currency", "Contract Size"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns in your Excel: {missing}\n\nExpected: {required}")
    st.stop()

# Clean / types
df["Symbol Name"] = df["Symbol Name"].astype(str).str.strip()
df["Symbol"] = df["Symbol Name"].str.upper().str.strip()

df["Current Price"] = pd.to_numeric(df["Current Price"], errors="coerce")
df["Digits"] = pd.to_numeric(df["Digits"], errors="coerce")
df["Profit Currency"] = df["Profit Currency"].astype(str).str.upper().str.strip()
df["Contract Size"] = pd.to_numeric(df["Contract Size"], errors="coerce")

# Use file price (no market price section)
df["Price"] = df["Current Price"].copy()
df["Price_Source"] = "file"

# ============================================================
# FX (Profit currency only)
# ============================================================
fx = fx_to_usd()
df["Profit_to_USD"] = df["Profit Currency"].map(fx).fillna(1.0)

with st.expander("FX snapshot (1 CCY = X USD)", expanded=False):
    show_ccy = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "PLN", "SGD", "HKD", "ZAR", "TRY", "MXN", "CNH"]
    snap = {k: fx.get(k) for k in show_ccy if fx.get(k) is not None}
    st.write(snap)

# ============================================================
# CALCULATIONS (Profit → USD)
# ============================================================

# Point size
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).replace([np.inf, -np.inf], np.nan).fillna(0.0)

# Point value per lot in PROFIT currency
df["PointValue_Profit_perLot"] = (df["Contract Size"].fillna(0.0) * df["PointSize"].fillna(0.0)).fillna(0.0)

# Point value per lot in USD
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup
df["Markup_Points"] = float(markup_points)
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * df["Markup_Points"] * float(lots)
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional
df["Notional_Profit"] = df["Price"].fillna(0.0) * df["Contract Size"].fillna(0.0) * float(lots)
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission ($ per 1M per side) - uses sides
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# IB Commission (NO SIDES)
if ib_type == "None":
    df["IB_Commission_USD"] = 0.0
elif ib_type == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = float(ib_fixed_per_lot) * float(lots)
else:
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * float(ib_points) * float(lots)

# Brokerage
df["Brokerage_USD"] = df["Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]

# ============================================================
# LOSS FLAGS + SUGGESTED MARKUP (round UP to avoid tiny negative)
# ============================================================
df["Loss_Flag"] = df["Brokerage_USD"] < 0

# Breakeven markup USD needed = LP + IB
df["Breakeven_Markup_USD"] = df["LP_Commission_USD"] + df["IB_Commission_USD"]

# Convert USD requirement into points
den = (df["PointValue_USD_perLot"] * float(lots)).replace([0, np.nan, np.inf, -np.inf], np.nan)
raw_be = (df["Breakeven_Markup_USD"] / den).replace([np.inf, -np.inf], np.nan).fillna(0.0)

# Points rounding step (safe defaults)
# digits <= 1 (indices-like): 0.1 step; else: 1 point step
df["PointStep"] = np.where(df["Digits"].fillna(0) <= 1, 0.1, 1.0)

df["Breakeven_Points"] = raw_be
df["Breakeven_Points_Rounded"] = (np.ceil(raw_be / df["PointStep"]) * df["PointStep"]).fillna(0.0)

df["Suggested_Markup_Points"] = np.maximum(df["Markup_Points"], df["Breakeven_Points_Rounded"] + float(buffer_points))

df["Suggested_Markup_USD"] = df["PointValue_USD_perLot"] * df["Suggested_Markup_Points"] * float(lots)
df["Suggested_Brokerage_USD"] = df["Suggested_Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]
df["Suggested_Loss_Flag"] = df["Suggested_Brokerage_USD"] < 0

# ============================================================
# REPORT
# ============================================================
report = df[
    [
        "Symbol Name",
        "Symbol",
        "Profit Currency",
        "Profit_to_USD",
        "Price",
        "Price_Source",
        "Digits",
        "Contract Size",
        "PointSize",
        "PointValue_Profit_perLot",
        "PointValue_USD_perLot",
        "Markup_Points",
        "Markup_Profit",
        "Markup_USD",
        "Notional_Profit",
        "Notional_USD",
        "LP_Commission_USD",
        "IB_Commission_USD",
        "Brokerage_USD",
        "Loss_Flag",
        "Breakeven_Points",
        "Breakeven_Points_Rounded",
        "PointStep",
        "Suggested_Markup_Points",
        "Suggested_Markup_USD",
        "Suggested_Brokerage_USD",
        "Suggested_Loss_Flag",
    ]
].copy()

# View + analytics
st.subheader("Report (USD)")
st.dataframe(report.head(int(show_rows)), use_container_width=True)

st.subheader("Loss Analytics (current markup)")
loss_df = report[report["Loss_Flag"]].copy()
col1, col2, col3, col4 = st.columns(4)
col1.metric("Symbols (total)", int(len(report)))
col2.metric("Loss symbols", int(len(loss_df)))
col3.metric("Total Brokerage_USD", float(np.nan_to_num(report["Brokerage_USD"]).sum()))
col4.metric("Total Loss (neg sum)", float(np.nan_to_num(loss_df["Brokerage_USD"]).sum()))

if len(loss_df) > 0:
    st.write("Worst loss symbols (current markup):")
    st.dataframe(
        loss_df.sort_values("Brokerage_USD").head(15)[
            ["Symbol Name", "Profit Currency", "Markup_USD", "LP_Commission_USD", "IB_Commission_USD", "Brokerage_USD", "Breakeven_Points_Rounded", "Suggested_Markup_Points"]
        ],
        use_container_width=True,
    )
else:
    st.success("✅ No negative brokerage at current settings.")

st.subheader("Suggested Markup Outcome")
s_loss_df = report[report["Suggested_Loss_Flag"]].copy()
if len(s_loss_df) > 0:
    st.warning(
        "Some symbols are still negative even after suggested markup. "
        "Increase 'Suggested Markup buffer (points)' or check Digits/Contract Size inputs."
    )
    st.dataframe(
        s_loss_df.sort_values("Suggested_Brokerage_USD").head(15)[
            ["Symbol Name", "Suggested_Markup_Points", "Suggested_Brokerage_USD", "PointStep", "Breakeven_Points_Rounded"]
        ],
        use_container_width=True,
    )
else:
    st.success("✅ Suggested markup makes all symbols breakeven/positive (with rounding + buffer).")

st.download_button(
    "⬇️ Download Excel Report",
    data=to_excel_bytes(report),
    file_name="MarkupX_Report.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

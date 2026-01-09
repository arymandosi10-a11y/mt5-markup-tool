import pandas as pd
import numpy as np
import requests
import streamlit as st
from io import BytesIO

# ============================================================
# PAGE
# ============================================================
st.set_page_config(page_title="MarkupX – Market Cost Engine", layout="wide")

st.title("📊 MarkupX – Real Market Markup & Cost Engine")
st.caption("ALL symbols (FX, Indices, Metals, Energies): Profit currency → USD only. Base currency is NOT used.")

# ============================================================
# FX RATES (1 CCY = X USD)  ✅ Profit currency → USD
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

            # Convert to: 1 CCY = X USD
            out = {k.upper(): (1.0 / float(v)) for k, v in rates.items() if v and float(v) != 0.0}
            out["USD"] = 1.0
            return out
        except Exception:
            continue

    st.warning("⚠️ FX API unavailable. Using fallback FX rates (please verify).")
    return {"USD": 1.0, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0068, "AUD": 0.66, "CAD": 0.74, "CHF": 1.11, "NZD": 0.62}

def to_excel_bytes(report_df: pd.DataFrame, summary_df: pd.DataFrame, loss_df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        report_df.to_excel(w, index=False, sheet_name="Report")
        summary_df.to_excel(w, index=False, sheet_name="Summary")
        loss_df.to_excel(w, index=False, sheet_name="Losses")
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
    ib_type = st.selectbox("IB commission type", ["None", "Fixed ($ per lot)", "Point-wise (points)"], index=0)

    ib_fixed_per_lot = 0.0
    ib_points = 0.0

    if ib_type == "Fixed ($ per lot)":
        ib_fixed_per_lot = st.number_input("IB fixed ($ per lot)", min_value=0.0, value=10.0, step=0.5)
    elif ib_type == "Point-wise (points)":
        ib_points = st.number_input("IB points", min_value=0.0, value=0.0, step=1.0)

    st.divider()
    st.subheader("Display / Filters")
    show_only_losses = st.toggle("Show only LOSS rows", value=False)
    show_top_n = st.number_input("Top N (tables)", min_value=5, max_value=200, value=20, step=5)
    search = st.text_input("Search symbol contains", value="").strip().upper()

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
    st.error(f"Missing columns in Excel: {missing}")
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
df["Profit_to_USD"] = df["Profit Currency"].map(fx)

unknown_ccy = df.loc[df["Profit_to_USD"].isna(), "Profit Currency"].dropna().unique().tolist()
if unknown_ccy:
    st.warning("Unknown Profit Currency (treated as 1.0 USD): " + ", ".join(unknown_ccy))
df["Profit_to_USD"] = df["Profit_to_USD"].fillna(1.0)

# ============================================================
# PRICE (no external source now; uses file price)
# ============================================================
df["Price"] = df["Current Price"].copy()
df["Price_Source"] = "file"

# ============================================================
# CALCULATIONS (Profit → USD)
# ============================================================
df["PointSize"] = (10.0 ** (-df["Digits"].astype(float))).replace([np.inf, -np.inf], np.nan).fillna(0.0)

# Point value per lot in PROFIT currency
df["PointValue_Profit_perLot"] = df["Contract Size"].fillna(0.0) * df["PointSize"].fillna(0.0)

# Point value per lot in USD
df["PointValue_USD_perLot"] = df["PointValue_Profit_perLot"] * df["Profit_to_USD"]

# Markup (Points)
df["Markup_Points"] = float(markup_points)
df["Markup_Profit"] = df["PointValue_Profit_perLot"] * df["Markup_Points"] * float(lots)
df["Markup_USD"] = df["Markup_Profit"] * df["Profit_to_USD"]

# Notional (uses symbol price; still converted using Profit Currency per your rule)
df["Notional_Profit"] = df["Price"].fillna(0.0) * df["Contract Size"].fillna(0.0) * float(lots)
df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

# LP Commission ($ per 1M per side) - uses sides
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000.0) * float(lp_rate) * float(sides)

# IB Commission (NO SIDES for any type)
if ib_type == "None":
    df["IB_Commission_USD"] = 0.0
elif ib_type == "Fixed ($ per lot)":
    df["IB_Commission_USD"] = float(ib_fixed_per_lot) * float(lots)
else:
    df["IB_Commission_USD"] = df["PointValue_USD_perLot"] * float(ib_points) * float(lots)

# Brokerage (your formula)
df["Brokerage_USD"] = df["Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]

# ============================================================
# LOSS FLAGS + BREAKEVEN ANALYTICS ✅
# ============================================================
df["Loss_Flag"] = df["Brokerage_USD"] < 0
df["Loss_Amount_USD"] = np.where(df["Loss_Flag"], -df["Brokerage_USD"], 0.0)

# breakeven markup needed in USD = LP + IB
df["Breakeven_Markup_USD"] = df["LP_Commission_USD"] + df["IB_Commission_USD"]

# convert breakeven markup to points (how many points needed to cover LP+IB)
den = (df["PointValue_USD_perLot"] * float(lots)).replace(0, np.nan)
df["Breakeven_Points"] = (df["Breakeven_Markup_USD"] / den).replace([np.inf, -np.inf], np.nan).fillna(0.0)

# Suggested markup points = max(current markup, breakeven points)  (so brokerage never goes negative)
df["Suggested_Markup_Points"] = np.maximum(df["Markup_Points"], df["Breakeven_Points"])

# If you apply suggested points, what would brokerage be?
df["Suggested_Markup_USD"] = df["PointValue_USD_perLot"] * df["Suggested_Markup_Points"] * float(lots)
df["Suggested_Brokerage_USD"] = df["Suggested_Markup_USD"] - df["LP_Commission_USD"] - df["IB_Commission_USD"]

# ============================================================
# REPORT TABLE
# ============================================================
report = df[
    [
        "Symbol Name",
        "Profit Currency",
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
        "Loss_Amount_USD",
        "Breakeven_Points",
        "Suggested_Markup_Points",
        "Suggested_Brokerage_USD",
    ]
].copy()

# Filters
view = report.copy()
if search:
    view = view[view["Symbol Name"].astype(str).str.upper().str.contains(search, na=False)]
if show_only_losses:
    view = view[view["Loss_Flag"] == True]

# ============================================================
# ANALYTICS DASHBOARD ✅
# ============================================================
st.subheader("📌 Summary Analytics")

total_rows = len(report)
loss_rows = int(report["Loss_Flag"].sum())
total_brokerage = float(report["Brokerage_USD"].sum())
total_loss_amount = float(report["Loss_Amount_USD"].sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total symbols", f"{total_rows}")
c2.metric("Loss symbols", f"{loss_rows}")
c3.metric("Total Brokerage (USD)", f"{total_brokerage:,.2f}")
c4.metric("Total Loss Amount (USD)", f"{total_loss_amount:,.2f}")

# Worst loss / best brokerage
if total_rows > 0:
    worst = report.sort_values("Brokerage_USD", ascending=True).head(1)
    best = report.sort_values("Brokerage_USD", ascending=False).head(1)

    c5, c6 = st.columns(2)
    with c5:
        st.markdown("**Worst Symbol (Most Negative Brokerage)**")
        st.dataframe(worst[["Symbol Name", "Brokerage_USD", "LP_Commission_USD", "IB_Commission_USD", "Markup_USD", "Breakeven_Points", "Suggested_Markup_Points"]],
                     use_container_width=True, hide_index=True)
    with c6:
        st.markdown("**Best Symbol (Highest Brokerage)**")
        st.dataframe(best[["Symbol Name", "Brokerage_USD", "LP_Commission_USD", "IB_Commission_USD", "Markup_USD"]],
                     use_container_width=True, hide_index=True)

st.divider()

# Top loss table
st.subheader(f"🚨 Top {int(show_top_n)} Loss Makers")
loss_df = report[report["Loss_Flag"] == True].sort_values("Loss_Amount_USD", ascending=False)
st.dataframe(loss_df.head(int(show_top_n)), use_container_width=True)

# Bar chart: top loss amounts
if len(loss_df) > 0:
    chart_df = loss_df.head(min(int(show_top_n), 30))[["Symbol Name", "Loss_Amount_USD"]].set_index("Symbol Name")
    st.bar_chart(chart_df)

st.divider()

# Suggested fix impact
st.subheader("🛠️ Fix Suggestion Impact (If you apply Suggested_Markup_Points)")
fixed_loss_rows = int((report["Suggested_Brokerage_USD"] < 0).sum())
st.metric("Loss symbols after applying suggested markup", f"{fixed_loss_rows}")

st.caption("Suggested_Markup_Points = max(current markup points, breakeven points). This ensures brokerage is not negative (subject to rounding).")

# ============================================================
# MAIN REPORT
# ============================================================
st.subheader("📄 Report (USD)")
st.dataframe(view, use_container_width=True)

# ============================================================
# DOWNLOAD
# ============================================================
summary_df = pd.DataFrame(
    [
        ["Lots", lots],
        ["Markup points", markup_points],
        ["LP rate ($/1M/side)", lp_rate],
        ["LP sides", sides],
        ["IB type", ib_type],
        ["IB fixed ($/lot)", ib_fixed_per_lot],
        ["IB points", ib_points],
        ["Total symbols", total_rows],
        ["Loss symbols", loss_rows],
        ["Total brokerage (USD)", total_brokerage],
        ["Total loss amount (USD)", total_loss_amount],
    ],
    columns=["Metric", "Value"],
)

excel_bytes = to_excel_bytes(report, summary_df, loss_df)

st.download_button(
    "⬇️ Download Excel Report (Report + Summary + Losses)",
    data=excel_bytes,
    file_name="MarkupX_Report_With_LossFlags.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

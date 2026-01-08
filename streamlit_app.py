import streamlit as st
import pandas as pd
import yfinance as yf

# =========================
# PAGE CONFIG
# =========================
st.set_page_config(
    page_title="MarkupX – Market Cost Engine",
    layout="wide"
)

st.title("📊 MarkupX – Real-Time Markup / LP / IB Cost Engine")
st.caption("Forex • Indices • Metals • Energies | Profit-currency based")

# =========================
# SIDEBAR SETTINGS
# =========================
st.sidebar.header("⚙️ Global Settings")

lots = st.sidebar.number_input("Lots", 0.01, 1000.0, 1.0)
markup_points = st.sidebar.number_input("Markup (points)", 0.0, 1000.0, 20.0)

lp_rate = st.sidebar.number_input(
    "LP rate ($ per 1M notional)",
    0.0, 100.0, 7.0
)

ib_type = st.sidebar.selectbox(
    "IB Commission Type",
    ["None", "Fixed ($/lot)", "Point-wise"]
)

ib_value = st.sidebar.number_input(
    "IB Commission value",
    0.0, 1000.0, 10.0
)

# =========================
# FILE UPLOAD
# =========================
uploaded_file = st.file_uploader(
    "Upload MT5 Symbol Sheet (Excel)",
    type=["xlsx"]
)

if not uploaded_file:
    st.warning("Please upload your Excel symbol file")
    st.stop()

df = pd.read_excel(uploaded_file)

# =========================
# REAL-TIME PRICE SOURCE
# =========================
@st.cache_data(ttl=60)
def get_market_price(symbol):
    try:
        ticker_map = {
            "XAUUSD": "GC=F",
            "XAGUSD": "SI=F",
            "WTI": "CL=F",
            "BRENT": "BZ=F",
            "NAS100": "^NDX",
            "SP500": "^GSPC",
            "WS30": "^DJI",
        }

        yf_symbol = ticker_map.get(symbol, symbol)
        price = yf.Ticker(yf_symbol).fast_info["last_price"]
        return price
    except Exception:
        return None

# =========================
# FX CONVERSION
# =========================
@st.cache_data(ttl=300)
def fx_to_usd(currency):
    if currency == "USD":
        return 1.0
    try:
        pair = f"{currency}USD=X"
        return yf.Ticker(pair).fast_info["last_price"]
    except Exception:
        return None

# =========================
# CALCULATIONS
# =========================
prices = []
fx_rates = []

for _, row in df.iterrows():
    price = get_market_price(row["Symbol"])
    prices.append(price)

    fx = fx_to_usd(row["Profit_Currency"])
    fx_rates.append(fx)

df["Market_Price"] = prices
df["Profit_to_USD"] = fx_rates

df["PointValue_USD"] = (
    df["PointValue_Profit_perlot"] * df["Profit_to_USD"]
)

df["Markup_Profit"] = markup_points
df["Markup_USD"] = df["PointValue_USD"] * markup_points * lots

df["Notional_Profit"] = (
    df["Market_Price"] * df["Contract Size"] * lots
)

df["Notional_USD"] = df["Notional_Profit"] * df["Profit_to_USD"]

df["LP_Commission_USD"] = (
    (df["Notional_USD"] / 1_000_000) * lp_rate
)

# =========================
# IB COMMISSION
# =========================
if ib_type == "Fixed ($/lot)":
    df["IB_Commission_USD"] = ib_value * lots

elif ib_type == "Point-wise":
    df["IB_Commission_USD"] = (
        df["PointValue_USD"] * markup_points * lots
    )

else:
    df["IB_Commission_USD"] = 0.0

# =========================
# FINAL BROKERAGE
# =========================
df["Brokerage_USD"] = (
    df["Markup_USD"]
    - df["LP_Commission_USD"]
    - df["IB_Commission_USD"]
)

# =========================
# DISPLAY
# =========================
st.subheader("📈 Final Cost Breakdown")

cols = [
    "Symbol",
    "Market_Price",
    "Profit_Currency",
    "PointValue_USD",
    "Markup_USD",
    "Notional_USD",
    "LP_Commission_USD",
    "IB_Commission_USD",
    "Brokerage_USD"
]

st.dataframe(df[cols], width="stretch")

# =========================
# EXCEL EXPORT WITH FORMULAS
# =========================
st.subheader("⬇️ Download Excel (with formulas)")

output = pd.ExcelWriter("MarkupX_Output.xlsx", engine="xlsxwriter")
df.to_excel(output, index=False, sheet_name="Report")

workbook = output.book
worksheet = output.sheets["Report"]

worksheet.write_formula(
    "P2",
    "=K2-N2-O2"
)

output.close()

with open("MarkupX_Output.xlsx", "rb") as f:
    st.download_button(
        "Download Excel",
        f,
        file_name="MarkupX_Output.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

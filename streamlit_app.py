import streamlit as st
import pandas as pd
import requests
import openpyxl
from io import BytesIO

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="MarkupX – Market Cost Engine",
    layout="wide"
)

st.title("📊 MarkupX – Real Market Markup & Cost Engine")

# ============================================================
# REAL MARKET PRICE (TradingView style – public)
# ============================================================
@st.cache_data(ttl=30)
def get_market_price(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
        r = requests.get(url, timeout=5).json()
        return float(r["quoteResponse"]["result"][0]["regularMarketPrice"])
    except:
        return None

# ============================================================
# FILE UPLOAD (MANDATORY FIX)
# ============================================================
uploaded_file = st.file_uploader(
    "📤 Upload Symbol Sheet (Excel)",
    type=["xlsx"]
)

if uploaded_file is None:
    st.warning("Please upload your Excel file to continue.")
    st.stop()

# ============================================================
# LOAD EXCEL
# ============================================================
wb = openpyxl.load_workbook(uploaded_file)
ws = wb.active

headers = [cell.value for cell in ws[1]]
rows = list(ws.iter_rows(min_row=2, values_only=True))
df = pd.DataFrame(rows, columns=headers)

# ============================================================
# USER INPUTS
# ============================================================
st.sidebar.header("⚙️ Settings")

markup_points = st.sidebar.number_input("Markup Points", value=20.0)
ib_commission_per_lot = st.sidebar.number_input(
    "IB Commission ($ / Lot)", value=10.0
)

# ============================================================
# CALCULATION LOGIC (ONLY PROFIT CURRENCY)
# ============================================================
output_rows = []

for _, r in df.iterrows():
    symbol = r["Symbol"]
    lots = float(r["Lots"])
    profit_ccy = r["Profit Currency"]

    # Market price
    market_price = get_market_price(symbol)

    # Profit currency conversion
    if profit_ccy == "USD":
        fx_rate = 1
    else:
        fx_rate = get_market_price(f"{profit_ccy}USD") or 1

    # Markup cost
    markup_usd = lots * markup_points * fx_rate

    # IB commission (FIXED, no side)
    ib_cost = lots * ib_commission_per_lot

    total_cost = markup_usd + ib_cost

    output_rows.append([
        symbol,
        lots,
        market_price,
        profit_ccy,
        fx_rate,
        markup_usd,
        ib_cost,
        total_cost
    ])

# ============================================================
# OUTPUT DATAFRAME
# ============================================================
out_df = pd.DataFrame(output_rows, columns=[
    "Symbol",
    "Lots",
    "Market Price",
    "Profit Currency",
    "FX Rate → USD",
    "Markup Cost (USD)",
    "IB Commission (USD)",
    "Total Cost (USD)"
])

st.subheader("📈 Live Cost Calculation")
st.dataframe(out_df, use_container_width=True)

# ============================================================
# EXCEL EXPORT WITH FORMULAS
# ============================================================
def export_excel(df):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MarkupX"

    ws.append(list(df.columns))

    for i, row in df.iterrows():
        excel_row = i + 2
        ws.append([
            row["Symbol"],
            row["Lots"],
            row["Market Price"],
            row["Profit Currency"],
            row["FX Rate → USD"],
            f"=B{excel_row}*{markup_points}*E{excel_row}",
            f"=B{excel_row}*{ib_commission_per_lot}",
            f"=F{excel_row}+G{excel_row}"
        ])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio

st.download_button(
    "⬇️ Download Excel (With Editable Formulas)",
    export_excel(out_df),
    file_name="MarkupX_With_Formulas.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

import re
import streamlit as st
import pandas as pd
import requests
from io import BytesIO

st.set_page_config(page_title="MarkupX – MT5 Cost Engine", layout="wide")

@st.cache_data(ttl=3600)
def fx_to_usd():
    url = "https://api.exchangerate.host/latest?base=USD"
    rates = requests.get(url, timeout=20).json()["rates"]
    fx = {k: (1 / v) for k, v in rates.items() if v}
    fx["USD"] = 1.0
    return fx

def parse_symbol(sym):
    parts = re.findall(r"[A-Z]{3}", sym.upper())
    if len(parts) >= 2:
        return parts[0], parts[1]
    s = re.sub(r"[^A-Z]", "", sym.upper())
    return s[:3], s[3:6]

def to_excel(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Report")
    return buf.getvalue()

st.title("📊 MarkupX – MT5 Markup & LP Commission Tool")

with st.sidebar:
    st.header("Settings")
    lots = st.number_input("Lots", value=1.0)
    markup_points = st.number_input("Markup (points)", value=20.0)
    lp_rate = st.number_input("LP rate ($ per 1M per side)", value=30.0)
    sides = st.selectbox("Sides", [1, 2], index=1)

uploaded = st.file_uploader("Upload MT5 Symbol Export (Excel)", type=["xlsx"])

if not uploaded:
    st.info("Upload MT5 file with columns: Symbol Name, Digits, Profit Currency, Contract Size")
    st.stop()

df = pd.read_excel(uploaded)

required = ["Symbol Name", "Digits", "Profit Currency", "Contract Size"]
for c in required:
    if c not in df.columns:
        st.error(f"Missing column: {c}")
        st.stop()

fx = fx_to_usd()

df["Base"], df["Quote"] = zip(*df["Symbol Name"].map(parse_symbol))
df["PointSize"] = 10 ** (-df["Digits"])
df["PointValue"] = df["Contract Size"] * df["PointSize"]

df["Profit_to_USD"] = df["Profit Currency"].map(fx)
df["Base_to_USD"] = df["Base"].map(fx)

df["PointValue_USD"] = df["PointValue"] * df["Profit_to_USD"]
df["Markup_USD"] = df["PointValue_USD"] * markup_points * lots
df["Notional_USD"] = df["Contract Size"] * lots * df["Base_to_USD"]
df["LP_Commission_USD"] = (df["Notional_USD"] / 1_000_000) * lp_rate * sides

df["Total_Cost_USD"] = df["Markup_USD"] + df["LP_Commission_USD"]

report = df[[
    "Symbol Name","Base","Quote","PointValue_USD",
    "Markup_USD","Notional_USD","LP_Commission_USD","Total_Cost_USD"
]]

st.dataframe(report, use_container_width=True)

st.download_button(
    "Download Excel Report",
    data=to_excel(report),
    file_name="MarkupX_Report.xlsx"
)

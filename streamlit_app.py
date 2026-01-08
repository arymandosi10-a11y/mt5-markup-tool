import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.table import Table, TableStyleInfo

src_path = "/mnt/data/Book2.xlsx"
wb_src = openpyxl.load_workbook(src_path)
ws_src = wb_src.active

# Read headers and rows
headers = [cell.value for cell in ws_src[1]]
rows = []
for r in ws_src.iter_rows(min_row=2, values_only=True):
    if all(v is None for v in r):
        continue
    rows.append(list(r))

# Create new workbook
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Input"

# Settings sheet
ws_set = wb.create_sheet("Settings")
ws_set["A1"] = "Parameter"
ws_set["B1"] = "Value"
ws_set["A1"].font = ws_set["B1"].font = Font(bold=True)

settings = [
    ("Lots", 1.0),
    ("Default_Markup_Points", 20.0),
    ("LP_Rate_USD_per_1M_per_side", 7.0),
    ("LP_Sides", 2),
    ("IB_Type", "Fixed"),  # Fixed or Points
    ("IB_Fixed_USD_per_lot", 0.0),
    ("IB_Points", 0.0),
]
for i,(k,v) in enumerate(settings, start=2):
    ws_set[f"A{i}"] = k
    ws_set[f"B{i}"] = v

ws_set.column_dimensions["A"].width = 30
ws_set.column_dimensions["B"].width = 22

# FXRates sheet
ws_fx = wb.create_sheet("FXRates")
ws_fx["A1"] = "Currency"
ws_fx["B1"] = "Profit_to_USD"
ws_fx["A1"].font = ws_fx["B1"].font = Font(bold=True)

fx_seed = [
    ("USD", 1.0),
    ("EUR", 1.08),
    ("GBP", 1.26),
    ("JPY", 0.0068),
    ("AUD", 0.66),
    ("CAD", 0.74),
    ("CHF", 1.11),
    ("NZD", 0.62),
    ("SGD", 0.74),
    ("HKD", 0.128),
    ("ZAR", 0.054),
    ("TRY", 0.03),
]
for i,(ccy,val) in enumerate(fx_seed, start=2):
    ws_fx[f"A{i}"] = ccy
    ws_fx[f"B{i}"] = val

ws_fx.column_dimensions["A"].width = 14
ws_fx.column_dimensions["B"].width = 16

# Add original headers to Input
for c,h in enumerate(headers, start=1):
    ws.cell(row=1, column=c, value=h)

# Add helper + calculated columns
# Ensure expected base columns exist; if not, still proceed but formulas may show blanks.
calc_cols = [
    "Markup_Points_Override",
    "Profit_to_USD",
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
start_col = len(headers) + 1
for i,name in enumerate(calc_cols):
    ws.cell(row=1, column=start_col+i, value=name)

# Map header names to column index
hmap = {str(h).strip(): idx+1 for idx,h in enumerate(headers) if h is not None}

def col(name):
    return hmap.get(name)

# Put rows
for r_i, r in enumerate(rows, start=2):
    for c_i, val in enumerate(r, start=1):
        ws.cell(row=r_i, column=c_i, value=val)

# Define column letters for formulas
def L(cidx): 
    return get_column_letter(cidx)

# Identify base columns
c_symbol = col("Symbol Name") or col("Symbol") or 1
c_price = col("Current Price") or col("Price") or 2
c_digits = col("Digits") or 3
c_contract = col("Contract Size") or 4
c_profitccy = col("Profit Currency") or col("ProfitCurrency") or 5

# Calc column indices
c_override = start_col
c_profit_to_usd = start_col + 1
c_pointsize = start_col + 2
c_pv_profit = start_col + 3
c_pv_usd = start_col + 4
c_markup_profit = start_col + 5
c_markup_usd = start_col + 6
c_notional_profit = start_col + 7
c_notional_usd = start_col + 8
c_lp = start_col + 9
c_ib = start_col + 10
c_brokerage = start_col + 11

# Settings cells references
LOTS = "Settings!$B$2"
DEF_MARKUP = "Settings!$B$3"
LP_RATE = "Settings!$B$4"
LP_SIDES = "Settings!$B$5"
IB_TYPE = "Settings!$B$6"
IB_FIXED = "Settings!$B$7"
IB_POINTS = "Settings!$B$8"

# Apply formulas per row
max_row = 1 + len(rows)
for r in range(2, max_row+1):
    # Markup points effective
    override_cell = f"{L(c_override)}{r}"
    eff_markup = f"IF({override_cell}=\"\",{DEF_MARKUP},{override_cell})"
    
    # Profit_to_USD via XLOOKUP (Excel 365). fallback VLOOKUP.
    profitccy_cell = f"{L(c_profitccy)}{r}"
    ws.cell(row=r, column=c_profit_to_usd, value=f"IFERROR(XLOOKUP({profitccy_cell},FXRates!$A:$A,FXRates!$B:$B),1)")
    
    # PointSize = 10^-Digits
    digits_cell = f"{L(c_digits)}{r}"
    ws.cell(row=r, column=c_pointsize, value=f"IFERROR(POWER(10,-{digits_cell}),0)")
    
    # PointValue_Profit_perLot = ContractSize * PointSize
    contract_cell = f"{L(c_contract)}{r}"
    pointsize_cell = f"{L(c_pointsize)}{r}"
    ws.cell(row=r, column=c_pv_profit, value=f"IFERROR({contract_cell}*{pointsize_cell},0)")
    
    # PointValue_USD_perLot = PV_Profit * Profit_to_USD
    profit_to_usd_cell = f"{L(c_profit_to_usd)}{r}"
    pv_profit_cell = f"{L(c_pv_profit)}{r}"
    ws.cell(row=r, column=c_pv_usd, value=f"IFERROR({pv_profit_cell}*{profit_to_usd_cell},0)")
    
    # Markup_Profit = PV_Profit * MarkupPoints * Lots
    ws.cell(row=r, column=c_markup_profit, value=f"IFERROR({pv_profit_cell}*({eff_markup})*{LOTS},0)")
    
    # Markup_USD = Markup_Profit * Profit_to_USD
    markup_profit_cell = f"{L(c_markup_profit)}{r}"
    ws.cell(row=r, column=c_markup_usd, value=f"IFERROR({markup_profit_cell}*{profit_to_usd_cell},0)")
    
    # Notional_Profit = Price * ContractSize * Lots
    price_cell = f"{L(c_price)}{r}"
    ws.cell(row=r, column=c_notional_profit, value=f"IFERROR({price_cell}*{contract_cell}*{LOTS},0)")
    
    # Notional_USD = Notional_Profit * Profit_to_USD
    notional_profit_cell = f"{L(c_notional_profit)}{r}"
    ws.cell(row=r, column=c_notional_usd, value=f"IFERROR({notional_profit_cell}*{profit_to_usd_cell},0)")
    
    # LP_Commission_USD = (Notional_USD/1,000,000)*LP_Rate*LP_Sides
    notional_usd_cell = f"{L(c_notional_usd)}{r}"
    ws.cell(row=r, column=c_lp, value=f"IFERROR(({notional_usd_cell}/1000000)*{LP_RATE}*{LP_SIDES},0)")
    
    # IB_Commission_USD (NO SIDES)
    pv_usd_cell = f"{L(c_pv_usd)}{r}"
    ws.cell(row=r, column=c_ib, value=
            f"IF({IB_TYPE}=\"Fixed\","
            f"IFERROR({IB_FIXED}*{LOTS},0),"
            f"IFERROR({pv_usd_cell}*{IB_POINTS}*{LOTS},0))")
    
    # Brokerage_USD = Markup_USD - LP - IB
    markup_usd_cell = f"{L(c_markup_usd)}{r}"
    lp_cell = f"{L(c_lp)}{r}"
    ib_cell = f"{L(c_ib)}{r}"
    ws.cell(row=r, column=c_brokerage, value=f"IFERROR({markup_usd_cell}-{lp_cell}-{ib_cell},0)")

# Styling header
header_fill = PatternFill("solid", fgColor="FFF2CC")  # light yellow like Excel
for c in range(1, start_col+len(calc_cols)):
    cell = ws.cell(row=1, column=c)
    cell.font = Font(bold=True)
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if c >= c_markup_profit:  # yellow marked area includes later, but style calc headers
        cell.fill = header_fill

# Freeze panes and widths
ws.freeze_panes = "A2"
for c in range(1, start_col+len(calc_cols)):
    ws.column_dimensions[get_column_letter(c)].width = 18

# Add a table for nicer filtering
table_ref = f"A1:{get_column_letter(start_col+len(calc_cols)-1)}{max_row}"
tab = Table(displayName="MarkupXTable", ref=table_ref)
tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False,
                                    showLastColumn=False, showRowStripes=True, showColumnStripes=False)
ws.add_table(tab)

out_path = "/mnt/data/MarkupX_WithFormulas.xlsx"
wb.save(out_path)

out_path

import pandas as pd
import sys

def dump_sheet(file, sheet):
    print(f"\n{'='*20} {sheet} {'='*20}")
    try:
        df = pd.read_excel(file, sheet_name=sheet)
        # Drop completely empty rows/cols
        df = df.dropna(how='all').dropna(axis=1, how='all')
        print(df.to_string())
    except Exception as e:
        print(f"Error reading {sheet}: {e}")

file = 'Database_design.xlsx'
sheets = ['Table PO Header', 'Table PO Items', 'Table Received PO Item', 'Table Master Item']

for s in sheets:
    dump_sheet(file, s)

print(f"\n{'='*20} Samples Headers {'='*20}")
try:
    print("Sale Data:", pd.read_excel('samples/data_sale.xlsx').columns.tolist())
    print("Stock Data:", pd.read_excel('samples/data_stock_jst.xlsx').columns.tolist())
    print("Master Data:", pd.read_excel('samples/master_item.xlsx').columns.tolist())
except Exception as e:
    print(f"Error reading samples: {e}")

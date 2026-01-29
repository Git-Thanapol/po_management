import pandas as pd
import sys
import os

def analyze(file_path, target_sku=None):
    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        return

    print(f"Reading file: {file_path}...")
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error reading excel: {e}")
        return

    # Cleanup headers
    df.columns = df.columns.astype(str).str.replace('\u200b', '').str.strip()
    print(f"Total rows in file: {len(df)}")
    
    # --- Helper to identify columns ---
    def get_col_name(candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    col_status = get_col_name(['สถานะคำสั่งซื้อ', 'Status'])
    col_sku = get_col_name(['รหัสสินค้า', 'SKU'])
    col_qty = get_col_name(['จำนวน', 'Quantity'])
    col_order_id = get_col_name(['หมายเลขคำสั่งซื้อออนไลน์', 'Order ID', 'หมายเลขออเดอร์ภายใน'])

    if not col_sku or not col_qty or not col_order_id:
        print("Critical columns missing (SKU, Quantity, or Order ID). Check file headers.")
        print(f"Found headers: {list(df.columns)}")
        return

    # --- Filtering ---
    # 1. Filter out 'ยกเลิก'
    if col_status:
        # Normalize status
        df['__status_clean'] = df[col_status].astype(str).str.strip()
        original_count = len(df)
        df_active = df[df['__status_clean'] != 'ยกเลิก'].copy()
        filtered_count = len(df_active)
        print(f"Rows after filtering 'ยกเลิก': {filtered_count} (Removed {original_count - filtered_count} rows)")
    else:
        df_active = df.copy()
        print("Warning: Status column not found, skipping cancellation filter.")

    # 2. Normalize Data
    df_active['__sku_clean'] = df_active[col_sku].astype(str).str.strip()
    df_active['__order_clean'] = df_active[col_order_id].astype(str).str.strip()
    
    # --- Analysis ---
    if target_sku:
        print(f"\n--- Analyzing SKU: {target_sku} ---")
        df_target = df_active[df_active['__sku_clean'] == target_sku]
        
        total_qty = df_target[col_qty].sum()
        row_count = len(df_target)
        
        print(f"Found {row_count} rows for SKU '{target_sku}'")
        print(f"Total Quantity Sum: {total_qty}")
        
        # Check for Duplicates (Order ID + SKU Collision)
        # The importer uses (Order ID + SKU) as separate items. 
        # But checks for duplicates? 
        # Uniqueness logic in Importer: Sale.objects.get_or_create(order_id=..., sku=...)
        # This implies keys are (Order ID, SKU).
        
        # Determine if there are multiple rows with SAME Order ID for this SKU
        # e.g. Split lines in Excel?
        duplicates = df_target[df_target.duplicated(subset=['__order_clean'], keep=False)]
        
        if not duplicates.empty:
            print(f"\n[WARNING] Found {len(duplicates)} rows sharing Order IDs for this SKU!")
            print("The system imports based on unique (Order ID, SKU). If multiple rows exist for the same Order ID and SKU, data might be overwritten or ignored depending on logic.")
            
            print("\nDuplicate Details:")
            print(duplicates[[col_order_id, col_sku, col_qty, col_status] if col_status else [col_order_id, col_sku, col_qty]])
            
            # Sub-analysis of duplicates
            dup_sum = duplicates[col_qty].sum()
            print(f"Sum of quantity in these duplicate rows: {dup_sum}")
            
            # Calculate what would be stored if uniquely keyed by OrderID
            # Assuming last one wins or first one wins? 
            # get_or_create usually grabs the first one it finds matching criteria in DB, 
            # or creates one. If loop runs twice, second time it gets the EXISTING one.
            # It DOES NOT sum quantity automatically. It just updates other fields.
            
            # So if you have 2 rows: Qty 5, Qty 4 for same OrderID.
            # 1. Create Order A, Qty 5.
            # 2. Get Order A. defaults={'qty': 4..} -> ignored because NOT CREATED.
            # Result: Qty 5 in DB. Missing 4.
            
            print("\n[HYPOTHESIS] The importer does NOT sum quantities for duplicate rows. It takes the first one created.")
            
        else:
            print("\nNo duplicate Order IDs found for this SKU. Data looks unique.")
            
    else:
        print("\n--- Summary by SKU (Top 20) ---")
        summary = df_active.groupby('__sku_clean')[col_qty].sum().sort_values(ascending=False).head(20)
        print(summary)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_sales.py <path_to_excel> [target_sku]")
        print("Example: python scripts/analyze_sales.py data.xlsx SP019")
    else:
        file_path = sys.argv[1]
        sku = sys.argv[2] if len(sys.argv) > 2 else None
        
        # Support quoting in args if needed, usually shell handles it
        analyze(file_path, sku)

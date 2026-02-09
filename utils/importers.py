import pandas as pd
import requests
import os
from django.core.files.base import ContentFile
from django.conf import settings
from inventory.models import MasterItem, Sale, POHeader, POItem, JSTStockSnapshot, ReceivedPOItem
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class ImportService:
    @staticmethod
    def clean_header(df):
        df.columns = df.columns.astype(str).str.replace('\u200b', '').str.strip()
        return df

    @staticmethod
    def download_image(url, save_name):
        """
        Downloads image from URL and returns a Django ContentFile
        """
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return ContentFile(response.content, name=save_name)
        except Exception as e:
            logger.error(f"Failed to download image {url}: {e}")
        return None

    @staticmethod
    def import_master_items(file):
        df = pd.read_excel(file)
        df = ImportService.clean_header(df)
        
        # Mapping: 'รหัสสินค้า': product_code, 'ชื่อสินค้า': name, 'รูปภาพ': image
        # 'รูปแบบสินค้า': product_format, 'Type': category, 'สินค้าคงเหลือ': current_stock
        # 'Min_Limit': min_limit, 'Note': note
        
        results = {"success": 0, "failed": 0, "errors": []}
        
        for index, row in df.iterrows():
            try:
                code = str(row.get('รหัสสินค้า', '')).strip()
                if not code or code == 'nan':
                    continue
                
                # Check if exists to update or create
                item, created = MasterItem.objects.get_or_create(product_code=code)
                
                item.name = row.get('ชื่อสินค้า', item.name)
                item.product_format = row.get('รูปแบบสินค้า', item.product_format)
                item.category = row.get('Type', row.get('หมวดหมู่สินค้า', item.category))
                item.current_stock = row.get('สินค้าคงเหลือ', row.get('Stock', item.current_stock)) or 0
                item.min_limit = row.get('Min_Limit', item.min_limit) or 0
                item.note = row.get('Note', item.note)

                # Image Handling (Only if URL provided and different?)
                # Logic: If 'รูปภาพ' is a URL, download it.
                image_url = row.get('รูปภาพ')
                if image_url and str(image_url).startswith('http'):
                    # Save logic (maybe check if already has image?)
                    # For now, simplistic: download if provided
                    file_name = f"{code}.jpg" # or derive extension
                    content = ImportService.download_image(image_url, file_name)
                    if content:
                        item.image.save(file_name, content, save=False)
                
                item.save()
                results["success"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Row {index}: {e}")
        
        return results

    @staticmethod
    def import_sales_data(file):
        df = pd.read_excel(file)
        df = ImportService.clean_header(df)
        
        results = {"success": 0, "failed": 0, "errors": []}

        # --- helper to find col ---
        def get_col(df, candidates):
            for c in candidates:
                if c in df.columns: return c
            return None

        col_order_id = get_col(df, ['หมายเลขคำสั่งซื้อออนไลน์', 'Order ID', 'หมายเลขออเดอร์ภายใน'])
        col_sku = get_col(df, ['รหัสสินค้า', 'SKU'])
        col_qty = get_col(df, ['จำนวน', 'Quantity'])
        col_total_price = get_col(df, ['รายละเอียดยอดที่ชำระแล้ว', 'Total Price', 'ยอดขาย Upsell'])
        col_unit_price = get_col(df, ['ราคาต่อชิ้น', 'Unit Price'])
        col_status = get_col(df, ['สถานะคำสั่งซื้อ', 'Status'])
        col_platform = get_col(df, ['แพลตฟอร์ม', 'Platform'])
        col_date = get_col(df, ['เวลาสั่งซื้อ', 'Date'])
        col_shop = get_col(df, ['ร้านค้า', 'Shop Name'])

        if not col_order_id or not col_sku:
             results['errors'].append("Missing critical columns (Order ID or SKU)")
             return results

        # Normalize Data Phase
        # We build a standard list of dicts to process
        processed_data = []

        for index, row in df.iterrows():
            try:
                # 1. Status Check & Filter
                status = str(row[col_status]) if col_status and pd.notna(row[col_status]) else 'Completed'
                if status.strip() == 'ยกเลิก':
                    # Check delete logic if needed, but for aggregation we just skip first?
                    # If we skip, we don't aggregate.
                    # BUT: If DB has it, we must delete it.
                    # Complexity: We need to know 'Did we delete this OrderID/SKU combination?'
                    # We can do a bulk delete cleanup for all 'ยกเลิก' rows found?
                    
                    oid = str(row[col_order_id]).strip()
                    sk = str(row[col_sku]).strip()
                    # Try to find MasterItem just to identify the key for deletion
                    # (Performance hit but safer)
                    try:
                        m_item = MasterItem.objects.get(product_code=sk)
                        Sale.objects.filter(order_id=oid, sku=m_item).delete()
                    except:
                        pass # SKU doesn't exist, so Sale can't exist
                    continue

                processed_data.append({
                    'order_id': str(row[col_order_id]).strip(),
                    'sku_code': str(row[col_sku]).strip(),
                    'qty': int(row[col_qty]) if col_qty and pd.notna(row[col_qty]) else 0,
                    'total_price': float(row[col_total_price]) if col_total_price and pd.notna(row[col_total_price]) else 0,
                    'unit_price': float(row[col_unit_price]) if col_unit_price and pd.notna(row[col_unit_price]) else 0,
                    'status': status,
                    'platform': str(row[col_platform]) if col_platform and pd.notna(row[col_platform]) else 'Shopee',
                    'date': row[col_date] if col_date and pd.notna(row[col_date]) else datetime.today(),
                    'shop_name': str(row[col_shop]) if col_shop and pd.notna(row[col_shop]) else '',
                    'orig_index': index
                })
            except Exception as e:
                results['errors'].append(f"Row {index} parsing error: {e}")

        # Convert to DF for Aggregation
        if not processed_data:
            return results

        df_clean = pd.DataFrame(processed_data)
        
        # Aggregate duplicates (Order ID + SKU)
        # Sum: qty, total_price
        # First: status, platform, date, shop_name, unit_price (calculated later)
        
        agg_rules = {
            'qty': 'sum',
            'total_price': 'sum',
            'status': 'first',
            'platform': 'first',
            'date': 'first',
            'shop_name': 'first',
            'unit_price': 'first' # We take first, or recalc? Recalc is better.
        }
        
        df_grouped = df_clean.groupby(['order_id', 'sku_code'], as_index=False).agg(agg_rules)

        # Import Phase
        for index, row in df_grouped.iterrows():
            try:
                order_id = row['order_id']
                sku_code = row['sku_code']
                
                if not order_id or not sku_code: continue

                # Get/Create Master Item
                try:
                    master_item = MasterItem.objects.get(product_code=sku_code)
                except MasterItem.DoesNotExist:
                    results["failed"] += 1
                    # Auto-create unknown
                    master_item = MasterItem.objects.create(product_code=sku_code, name=f"Unknown {sku_code}")

                qty = row['qty']
                total_price = row['total_price']
                
                # Recalculate Unit Price from aggregated totals to be safe
                if qty > 0:
                    unit_price = total_price / qty
                else:
                    unit_price = row['unit_price']

                sale, created = Sale.objects.get_or_create(
                    order_id=order_id,
                    sku=master_item,
                    defaults={
                        'qty': qty,
                        'price': unit_price,
                        'total_price': total_price,
                        'net_price': total_price,
                        'status': row['status'],
                        'platform': row['platform'],
                        'date': row['date'],
                        'shop_name': row['shop_name'],
                    }
                )

                if not created:
                    # Update existing record with aggregated values
                    sale.qty = qty
                    sale.total_price = total_price
                    sale.price = unit_price
                    sale.net_price = total_price
                    sale.status = row['status']
                    sale.save()
                
                results["success"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Grouped Item {row.get('order_id')}: {e}")

        return results

    @staticmethod
    def import_stock_jst(file):
        df = pd.read_excel(file)
        df = ImportService.clean_header(df)
        
        # Columns: 'รหัสสินค้า', 'คงเหลือ', 'Min_Limit'
        results = {"success": 0, "failed": 0, "errors": []}
        
        # We usually wipe yesterday's snapshot or keep history? 
        # Model stores history with 'snapshot_date'. So we just add new entries for today.
        
        today = datetime.now().date()
        
        for index, row in df.iterrows():
            try:
                code = str(row.get('รหัสSKU', '')).strip()
                if not code or code == 'nan':
                    continue

                try:
                    master_item = MasterItem.objects.get(product_code=code)
                except MasterItem.DoesNotExist:
                     master_item = MasterItem.objects.create(product_code=code, name=row.get('ชื่อสินค้า', f"Unknown {code}"))
                
                # Check for existing snapshot for today
                snapshot = JSTStockSnapshot.objects.filter(sku=master_item, snapshot_date=today).first()
                if snapshot:
                    snapshot.quantity = row.get('จํานวนที่ใช้ได้', 0)
                    snapshot.jst_min_limit = row.get('จำนวนน้อยสุดในการเติมสินค้า (MIN)', 0)
                    snapshot.note = row.get('หมายเหตุสินค้า', '')
                    snapshot.save()
                else:
                    JSTStockSnapshot.objects.create(
                        sku=master_item,
                        quantity=row.get('จํานวนที่ใช้ได้', 0),
                        jst_min_limit=row.get('จำนวนน้อยสุดในการเติมสินค้า (MIN)', 0),
                        note=row.get('หมายเหตุสินค้า', ''),
                        # raw_type=row.get('Type', '')
                    )

                # Sync MasterItem current_stock to match JST (Source of Truth)
                master_item.current_stock = row.get('จํานวนที่ใช้ได้', 0)
                master_item.save()
                
                results["success"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Row {index}: {e}")
                
        return results

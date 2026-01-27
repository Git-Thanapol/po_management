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

        # Helper to get value from multiple possible keys
        def get_val(row, keys, default=None):
            for key in keys:
                if key in row and pd.notna(row[key]):
                    return row[key]
            return default

        for index, row in df.iterrows():
            try:
                # Map columns (Thai/English)
                order_id = str(get_val(row, ['หมายเลขคำสั่งซื้อออนไลน์', 'Order ID', 'หมายเลขออเดอร์ภายใน'], '')).strip()
                sku_code = str(get_val(row, ['รหัสสินค้า', 'SKU'], '')).strip()
                
                if not order_id or not sku_code:
                    continue
                
                # Try to get MasterItem
                try:
                    master_item = MasterItem.objects.get(product_code=sku_code)
                except MasterItem.DoesNotExist:
                    results["failed"] += 1
                    # Auto-create if missing to allow import? 
                    # Providing a fallback/unknown item is better than blocking if allowed. 
                    # For now, just create as Unknown to let user fix later or use dummy.
                    master_item = MasterItem.objects.create(product_code=sku_code, name=f"Unknown {sku_code}")
                
                # Data Extraction
                qty = int(get_val(row, ['จำนวน', 'Quantity'], 0))
                total_price = float(get_val(row, ['รายละเอียดยอดที่ชำระแล้ว', 'Total Price', 'ยอดขาย Upsell'], 0))
                unit_price = float(get_val(row, ['ราคาต่อชิ้น', 'Unit Price'], 0))
                
                # Calc Unit Price if missing
                if unit_price == 0 and qty > 0:
                    unit_price = total_price / qty
                    
                status = get_val(row, ['สถานะคำสั่งซื้อ', 'Status'], 'Completed')
                platform = get_val(row, ['แพลตฟอร์ม', 'Platform'], 'Shopee')
                date_val = get_val(row, ['เวลาสั่งซื้อ', 'Date'], datetime.today())
                shop_name = get_val(row, ['ร้านค้า', 'Shop Name'], '')

                sale, created = Sale.objects.get_or_create(
                    order_id=order_id,
                    sku=master_item,
                    defaults={
                        'qty': qty,
                        'price': unit_price,
                        'total_price': total_price,
                        'net_price': total_price, # Default net to total if no fee info
                        'status': status,
                        'platform': platform,
                        'date': date_val,
                        'shop_name': shop_name,
                    }
                )
                
                if not created:
                     pass
                
                results["success"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Row {index}: {e}")
        
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
                code = str(row.get('รหัสสินค้า', '')).strip()
                if not code or code == 'nan':
                    continue

                try:
                    master_item = MasterItem.objects.get(product_code=code)
                except MasterItem.DoesNotExist:
                     master_item = MasterItem.objects.create(product_code=code, name=row.get('ชื่อสินค้า', f"Unknown {code}"))
                
                JSTStockSnapshot.objects.create(
                    sku=master_item,
                    quantity=row.get('คงเหลือ', 0),
                    jst_min_limit=row.get('Min_Limit', 0),
                    note=row.get('Note', ''),
                    # raw_type=row.get('Type', '')
                )
                
                results["success"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Row {index}: {e}")
                
        return results

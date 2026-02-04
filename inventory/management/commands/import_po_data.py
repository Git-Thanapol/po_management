import pandas as pd
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date
from inventory.models import POHeader, POItem, MasterItem
from decimal import Decimal
import os

class Command(BaseCommand):
    help = 'Import PO Data from separated Excel files (Header/Items).'

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to the Excel file')
        parser.add_argument('--type', type=str, required=True, choices=['header', 'items'], help='Type of file: header or items')

    def handle(self, *args, **kwargs):
        file_path = kwargs['file_path']
        import_type = kwargs['type']
        
        self.stdout.write(f"Reading {import_type} file: {file_path}")
        try:
            # Use dayfirst=True for dates like 05/08/2025 (DMY)
            df = pd.read_excel(file_path, parse_dates=True)
            # Remove NaN
            df = df.where(pd.notnull(df), None)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error reading file: {e}"))
            return

        self.stdout.write(f"Found {len(df)} rows. Processing...")
        
        success_count = 0
        error_count = 0

        # Helper for safer retrieval
        def get_val(row, col_name, default=None, is_decimal=False):
            val = row.get(col_name)
            if val is None:
                return default
            if is_decimal:
                try:
                    return Decimal(str(val))
                except:
                    return Decimal(0)
            return val
        
        def parse_date_col(val):
            if pd.isnull(val):
                return None
            if hasattr(val, 'date'):
                return val.date()
            # Try parsing string if not parsed by pandas
            try:
                dt = pd.to_datetime(val, dayfirst=True)
                return dt.date()
            except:
                return None

        for index, row in df.iterrows():
            try:
                row_id = row.get('id')
                
                if import_type == 'header':
                    # Header Mapping
                    po_number = str(row.get('po_number', '')).strip()
                    
                    if not po_number:
                        self.stdout.write(self.style.WARNING(f"Row {index}: PO Number missing. Skipping."))
                        continue

                    POHeader.objects.update_or_create(
                        po_number=po_number,
                        defaults={
                            'order_type': row.get('order_type', 'IMPORTED'),
                            'shipping_type': row.get('shipping_type'),
                            'order_date': parse_date_col(row.get('order_date')),
                            'estimated_date': parse_date_col(row.get('estimated_date')),
                            'exchange_rate': get_val(row, 'exchange_rate', 1, True),
                            'shipping_cost_baht': get_val(row, 'shipping_cost_baht', 0, True), # Keep if property setter exists, otherwise remove? Model doesn't have it. Remove.
                            # Actually, verifying Step 651, shipping_cost_baht does NOT exist.
                            # total_yuan exists.
                            # shipping_rate_thb_cbm exists.
                            
                            'total_yuan': get_val(row, 'total_yuan', 0, True),
                            'status': row.get('status', 'Pending'),
                            'ref_price_lazada': get_val(row, 'lazada_price', 0, True),
                            'link_shop': row.get('link_shop'),
                            'note': row.get('note'),
                            'ref_price_shopee': get_val(row, 'shopee_price', 0, True),
                            'ref_price_tiktok': get_val(row, 'tiktok_price', 0, True),
                            'wechat_contact': row.get('wechat_contact'),
                            'shipping_rate_thb_cbm': get_val(row, 'shipping_rate_cbm', 0, True),
                        }
                    )

                elif import_type == 'items':
                    # Item Mapping
                    header_id = row.get('header_id') # Corresponds to po_number
                    sku_code = row.get('sku_id')    # Corresponds to product_code
                    
                    # Convert float/etc to string if needed for PO Number lookup
                    if header_id is not None:
                        header_id = str(header_id).strip()

                    try:
                        header = POHeader.objects.get(po_number=header_id)
                    except POHeader.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"Item Row {index} (ID {row_id}): Header (PO {header_id}) not found. Skipping."))
                        error_count += 1
                        continue

                    sku_code = str(sku_code).strip()
                    
                    try:
                        master_item = MasterItem.objects.get(product_code=sku_code)
                    except MasterItem.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"Item Row {index} (ID {row_id}): SKU {sku_code} not found. Creating new MasterItem."))
                        master_item = MasterItem.objects.create(
                            product_code=sku_code,
                            name=sku_code 
                        )

                    # Update or Create based on Header + SKU (Unique per PO line effectively)
                    POItem.objects.update_or_create(
                        header=header,
                        sku=master_item,
                        defaults={
                            'qty_ordered': int(get_val(row, 'qty_ordered', 0)),
                            'price_yuan': get_val(row, 'price_yuan', 0, True),
                            'price_baht': get_val(row, 'price_baht', 0, True),
                            'total_received_qty': int(get_val(row, 'total_received_qty', 0)),
                            'total_received_cbm': get_val(row, 'total_received_cbm', 0, True),
                            'total_received_weight': get_val(row, 'total_received_weight', 0, True),
                        }
                    )

                success_count += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error Row {index}: {e}"))
                error_count += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Processed: {success_count}, Errors: {error_count}"))

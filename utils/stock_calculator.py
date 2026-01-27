from inventory.models import MasterItem, JSTStockSnapshot, Sale, ReceivedPOItem
from django.db.models import Sum
from datetime import date

class StockService:
    @staticmethod
    def calculate_stock(sku_code):
        """
        Calculates stock based on Hybrid Logic:
        Priority 1: JST Stock Snapshot (today's)
        Priority 2: System Calculation (Initial + Received - Sold)
        """
        # Fetch Master Item
        try:
             master_item = MasterItem.objects.get(product_code=sku_code)
        except MasterItem.DoesNotExist:
             return {"qty": 0, "status": "Unknown", "source": "None"}

        today = date.today()
        
        # 1. Check JST Snapshot (Today)
        jst_snapshot = JSTStockSnapshot.objects.filter(sku=master_item, snapshot_date=today).order_by('-id').first()
        
        if jst_snapshot:
            final_qty = jst_snapshot.quantity
            source = "File (JST)"
        else:
            # 2. System Calculation
            # Initial (from MasterItem current_check? Or we assume current_stock IS the system stock?)
            # Prompt said: "Calculate Initial + Total Received - Total Sold"
            # I'll assume master_item.current_stock is the 'Initial' or 'Base' for this calc if distinct?
            # Or usually, we should just query transactions. 
            # Let's assume master_item.current_stock IS the running system total for simplicity unless we have a separate 'initial_balance'.
            # I will use master_item.current_stock as the base.
            
            # Received
            # Note: If master_item.current_stock is ALREADY updated by signals, we just use it.
            # But the signals on 'ReceivedPOItem' update 'POItem.total_received'. behavior of MasterItem stock update wasn't explicitly asked in models.
            # I should probably update MasterItem.current_stock when Receiving items or Selling?
            # If I haven't implemented signals to update MasterItem.current_stock, I should do dynamic calc here.
            
            # Dynamic Calc:
            # total_received = ReceivedPOItem.objects.filter(po_item__sku=master_item).aggregate(Sum('received_qty'))['received_qty__sum'] or 0
            # total_sold = Sale.objects.filter(sku=master_item).aggregate(Sum('qty'))['qty__sum'] or 0
            # final_qty = master_item.current_stock + total_received - total_sold
            
            # However, if 'current_stock' in MasterItem is meant to BE the current stock, I should use it.
            # The prompt says: "Fetch System Stock: Calculate Initial + Total Received - Total Sold."
            # This implies dynamic calculation from a base.
            # Let's assume 'current_stock' in MasterItem is the "Initial" legacy stock before using this system.
            
            initial = master_item.current_stock
            received = ReceivedPOItem.objects.filter(po_item__sku=master_item).aggregate(t=Sum('received_qty'))['t'] or 0
            sold = Sale.objects.filter(sku=master_item).aggregate(t=Sum('qty'))['t'] or 0
            
            final_qty = initial + received - sold
            source = "Calculated"

        # Determine Status
        # "Qty <= 0: 🔴 หมดเกลี้ยง"
        # "Qty <= min_limit: ⚠️ ของใกล้หมด"
        # "Else: 🟢 มีของ"
        
        if final_qty <= 0:
            status = "🔴 หมดเกลี้ยง"
        elif final_qty <= master_item.min_limit:
            status = "⚠️ ของใกล้หมด"
        else:
            status = "🟢 มีของ"
            
        return {
            "sku": sku_code,
            "qty": final_qty,
            "status": status,
            "source": source
        }


import os
import django
import sys
from datetime import date, timedelta
from decimal import Decimal

# Setup Django Environment
sys.path.append('c:/Users/Thana/dev/po_management')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jst_system.settings')  
# Adjust settings module if needed. Assuming standard django project layout. 
# If settings cannot be loaded, this script might fail.
# Actually I need to check where manage.py is to be sure of proper root.
# 'c:\Users\Thana\dev\po_management' seems correct root.

try:
    django.setup()
except Exception as e:
    print(f"Error setting up Django: {e}")
    # Fallback/Mock if we can't truly run it, but finding the error is better.
    sys.exit(1)

from inventory.models import POHeader, POItem, MasterItem, ReceivedPOItem
from django.db import transaction

def run_verification():
    print("Starting Verification...")
    
    # Cleanup for test
    POHeader.objects.all().delete()
    MasterItem.objects.all().delete()
    
    # 1. Setup Master Items
    item_a = MasterItem.objects.create(product_code="SKU001", name="Test Item A")
    item_b = MasterItem.objects.create(product_code="SKU002", name="Test Item B")
    print("Created Master Items.")
    
    # 2. Create PO Header
    po = POHeader.objects.create(
        po_number="PO-TEST-001",
        order_type='IMPORTED',
        shipping_type='CAR',
        order_date=date.today(),
        exchange_rate=Decimal("5.0"),
        total_yuan=Decimal("1000.00"),
        shipping_rate_thb_cbm=Decimal("4000.00")
    )
    print(f"Created PO {po.po_number} with Total Yuan: {po.total_yuan}")
    
    # 3. Add PO Items
    # Adding items should trigger proration
    po_item_a = POItem.objects.create(header=po, sku=item_a, qty_ordered=10)
    po_item_b = POItem.objects.create(header=po, sku=item_b, qty_ordered=40)
    
    # Verify Proration
    # Total Qty = 50.
    # Item A: 10/50 * 1000 = 200 Yuan.
    # Item B: 40/50 * 1000 = 800 Yuan.
    
    po_item_a.refresh_from_db()
    po_item_b.refresh_from_db()
    
    print(f"Item A Proration: {po_item_a.price_yuan} (Expected 200.00)")
    print(f"Item B Proration: {po_item_b.price_yuan} (Expected 800.00)")
    
    assert po_item_a.price_yuan == Decimal("200.00")
    assert po_item_b.price_yuan == Decimal("800.00")
    print("Proration Verified!")
    
    # 4. Receiving Logic Verification (Batch)
    # We simulate the batch receiving logic here manually as per plan
    # Batch 1: Bill Date Today. CBM 10, KG 100.
    # Items: A=5, B=20.
    batch_cbm = Decimal("10.0")
    batch_kg = Decimal("100.0")
    batch_items = {po_item_a: 5, po_item_b: 20}
    total_batch_qty = sum(batch_items.values()) # 25
    
    bill_date = date.today()
    
    print("Receiving Batch 1...")
    for item, qty in batch_items.items():
        # Interpolate
        ratio = Decimal(qty) / Decimal(total_batch_qty)
        line_cbm = batch_cbm * ratio
        line_kg = batch_kg * ratio
        
        ReceivedPOItem.objects.create(
            po_item=item,
            bill_date=bill_date,
            received_qty=qty,
            received_cbm=line_cbm,
            received_weight=line_kg
        )
        
    po_item_a.refresh_from_db()
    po_item_b.refresh_from_db()
    
    print(f"Item A Received CBM: {po_item_a.total_received_cbm} (Expected 2.0)")
    # 10 * 5/25 = 2.0
    assert po_item_a.total_received_cbm == Decimal("2.0000")
    print("Batch Interpolation Verified!")
    
    # 5. Check Status
    po.refresh_from_db()
    print(f"PO Status: {po.status} (Expected 'Incomplete')")
    assert po.status == po.STATUS_INCOMPLETE
    
    # Complete the rest
    # Batch 2: Remaining
    print("Receiving Batch 2 (Completion)...")
    ReceivedPOItem.objects.create(po_item=po_item_a, received_qty=5, received_cbm=0, received_weight=0)
    ReceivedPOItem.objects.create(po_item=po_item_b, received_qty=20, received_cbm=0, received_weight=0)
    
    po.refresh_from_db()
    print(f"PO Status: {po.status} (Expected 'Complete')")
    assert po.status == po.STATUS_COMPLETE
    
    print("All Checks Passed!")

if __name__ == "__main__":
    run_verification()

from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import timedelta, date

class MasterItem(models.Model):
    product_code = models.CharField(max_length=100, primary_key=True, verbose_name="รหัสสินค้า") # SKU
    name = models.CharField(max_length=255, verbose_name="ชื่อสินค้า")
    image = models.ImageField(upload_to='products/', blank=True, null=True, verbose_name="รูปภาพ")
    product_format = models.CharField(max_length=100, blank=True, null=True, verbose_name="รูปแบบสินค้า")
    category = models.CharField(max_length=100, blank=True, null=True, verbose_name="หมวดหมู่สินค้า") # Type
    
    # Stock info
    current_stock = models.IntegerField(default=0, verbose_name="สินค้าคงเหลือ") # System logic
    min_limit = models.IntegerField(default=0, verbose_name="Min Limit")
    
    note = models.TextField(blank=True, null=True, verbose_name="Note")
    
    # Selling Price Benchmarks
    shopee_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="ราคาขาย Shopee")
    lazada_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="ราคาขาย Lazada")
    tiktok_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="ราคาขาย TikTok")
    
    # Financials (implied from sales/po, but good to have master price if needed, though not explicitly in Master Design)

    def __str__(self):
        return f"{self.product_code} - {self.name}"

class POHeader(models.Model):
    ORDER_TYPE_CHOICES = [
        ('IMPORTED', 'Imported'),
        ('DOMESTIC', 'Domestic'),
    ]
    SHIPPING_TYPE_CHOICES = [
        ('CAR', 'Car'),
        ('SHIP', 'Ship'),
    ]
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Complete', 'Complete'),
        # Add more if needed
    ]

    po_number = models.CharField(max_length=50, unique=True, verbose_name="เลข PO")
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, verbose_name="ประเภทรายการ")
    shipping_type = models.CharField(max_length=20, choices=SHIPPING_TYPE_CHOICES, blank=True, null=True, verbose_name="ขนส่ง")
    
    order_date = models.DateField(verbose_name="วันที่สั่งซื้อ")
    estimated_date = models.DateField(blank=True, null=True, verbose_name="วันคาดการณ์")
    
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1.0, verbose_name="เรทเงิน")
    total_yuan = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="ยอดหยวน (¥)")
    shipping_cost_baht = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="ต้นทุน/ชิ้น (฿)") # This name is ambiguous in design, might be header level cost? "shipping_cost_baht"
    shipping_rate_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="ค่าส่ง/กก.")
    shipping_rate_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="ค่าส่ง/กก.")
    shipping_rate_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="ค่าส่ง/CBM (บาท)") # Keep for legacy or if needed, but Primary now is Yuan
    shipping_rate_yuan_per_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="ค่าส่ง/CBM (หยวน)")
    
    # Extra fields from user design
    link_shop = models.CharField(max_length=255, blank=True, null=True, verbose_name="ลิงค์ร้านค้า")
    wechat_contact = models.CharField(max_length=100, blank=True, null=True, verbose_name="WeChat / ติดต่อ")
    
    # Selling Price Benchmarks?
    shopee_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="ราคาขาย Shopee")
    lazada_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="ราคาขาย Lazada")
    tiktok_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="ราคาขาย TikTok")
    
    note = models.TextField(blank=True, null=True, verbose_name="หมายเหตุ")
    # Using FileField allowing multiple files is tricky in Django model directly, usually needs M2M or separate model.
    # But for simplicity, let's add one file field or rely on a separate Attachment model if multiple needed.
    # User said "Allow to upload multiple files". 
    # I will create a separate model POAttachment later or just add one field for now to satisfy model schema.
    # Actually, let's keep it simple: One file for now, or use a JSON field for paths?
    # Spec says "Attach box" -> "Allow to upload multiple files".
    # Best practice: POAttachment model.
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending', verbose_name="สถานะ")

    def save(self, *args, **kwargs):
        if not self.estimated_date and self.order_date:
            if self.shipping_type == 'CAR':
                self.estimated_date = self.order_date + timedelta(days=14)
            elif self.shipping_type == 'SHIP':
                self.estimated_date = self.order_date + timedelta(days=25)
        super().save(*args, **kwargs)

    @property
    def total_cbm(self):
        # Assumes POItem.cbm is the Total CBM for that line (as decided in po_create)
        # Using aggregate to sum
        from django.db.models import Sum
        return self.items.aggregate(total=Sum('cbm'))['total'] or 0

    @property
    def transportation_cost(self):
        # Formula: Sum CBM * Exchange Rate * Yuan Rate/CBM
        from decimal import Decimal
        
        total_cbm = self.total_cbm or 0
        rate_yuan = self.shipping_rate_yuan_per_cbm or 0
        ex_rate = self.exchange_rate or 1
        
        # Ensure proper types for calculation
        # Use straight Decimal conversion
        return Decimal(str(total_cbm)) * Decimal(str(rate_yuan)) * Decimal(str(ex_rate))

    def __str__(self):
        return self.po_number

class POItem(models.Model):
    header = models.ForeignKey(POHeader, on_delete=models.CASCADE, related_name='items')
    sku = models.ForeignKey(MasterItem, on_delete=models.CASCADE, verbose_name="รหัสสินค้า")
    qty_ordered = models.IntegerField(verbose_name="สั่งซื้อ")
    
    price_yuan = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="ยอดหยวน (¥)")
    price_baht = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="ยอดบาทรวม (฿)")
    
    # Dimensions
    # Removed W/L/H as per request
    cbm = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True, verbose_name="CBM")
    weight = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="น้ำหนัก (KG)")
    
    total_received_qty = models.IntegerField(default=0, verbose_name="รับแล้ว (จำนวน)")
    total_received_cbm = models.DecimalField(max_digits=10, decimal_places=4, default=0, verbose_name="รับแล้ว (CBM)")
    total_received_weight = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="รับแล้ว (Weight)")

    def save(self, *args, **kwargs):
        # Calculate Price Baht
        # "price_baht: IF order_type is Imported -> price_yuan * header.exchange_rate"
        if self.header.order_type == 'IMPORTED' and self.price_yuan:
            self.price_baht = self.price_yuan * self.header.exchange_rate
        
        super().save(*args, **kwargs)

    @property
    def remaining_qty(self):
        return max(0, self.qty_ordered - self.total_received_qty)

    @property
    def unit_price_yuan(self):
        if self.qty_ordered and self.qty_ordered > 0:
            return self.price_yuan / self.qty_ordered
        return 0

    @property
    def total_shipping_cost(self):
        # CBM Cost: (CBM (default 1) * Yuan Rate * Exchange Rate)
        cbm_val = self.cbm if (self.cbm is not None and self.cbm > 0) else 1
        rate_yuan = self.header.shipping_rate_yuan_per_cbm or 0
        ex_rate = self.header.exchange_rate or 1
        
        cost_cbm = cbm_val * rate_yuan * ex_rate
        
        # KG Cost: Weight * Rate KG (Assuming Rate KG is still Baht? Or Yuan too? 
        # User only specified Yuan change for CBM transportation rate. maintaining KG as is or assuming Baht unless specified.
        # Let's assume KG is still Baht for now as user was specific about "Transportation rate ... calculate ... via Yuan rate * Transportation rate * CBM"
        cost_kg = (self.weight or 0) * (self.header.shipping_rate_kg or 0)

        return cost_kg + cost_cbm

    @property
    def unit_cost_baht(self):
        # (Total Baht + Total Shipping) / Qty
        total_b = (self.price_baht or 0) + self.total_shipping_cost
        if self.qty_ordered and self.qty_ordered > 0:
            return total_b / self.qty_ordered
        return 0
        
    @property
    def duration_days(self):
        # Days from Order to Today (if pending) or Order to Received (if complete?)
        # Simple logic: Order to Today
        if self.header.order_date:
            return (date.today() - self.header.order_date).days
        return 0

    def __str__(self):
        return f"{self.sku.product_code} in {self.header.po_number}"

class ReceivedPOItem(models.Model):
    po_item = models.ForeignKey(POItem, on_delete=models.CASCADE, related_name='receipts')
    received_qty = models.IntegerField(default=0, verbose_name="จำนวนที่รับ")
    received_cbm = models.DecimalField(max_digits=10, decimal_places=4, default=0, verbose_name="CBM ที่รับ")
    received_weight = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="น้ำหนักที่รับ")
    received_date = models.DateField(default=date.today, verbose_name="วันที่รับ")

    @property
    def duration_from_order(self):
        if not self.po_item.header.order_date:
            return "-"
        delta = (self.received_date - self.po_item.header.order_date).days
        # "ถ้าเป็นวันเดียวกันให้นับ 1" implies min 1 day
        return delta if delta > 0 else 1

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.update_po_item_received()

    def delete(self, *args, **kwargs):
        # Keep reference to update parent
        parent_item = self.po_item
        super().delete(*args, **kwargs)
        # Recalculate parent
        # We can call the method on a fresh instance or static, 
        # but since the logic is on the instance method `update_po_item_received` which uses `self.po_item`,
        # we can't call it on deleted `self`.
        # Better to have helper or call logic here.
        # Actually logic is: data = parent_item.receipts.aggregate...
        # So we can just replicate or move logic to parent model?
        # Or Just:
        data = parent_item.receipts.aggregate(
            total_qty=models.Sum('received_qty'),
            total_cbm=models.Sum('received_cbm'),
            total_weight=models.Sum('received_weight')
        )
        parent_item.total_received_qty = data['total_qty'] or 0
        parent_item.total_received_cbm = data['total_cbm'] or 0
        parent_item.total_received_weight = data['total_weight'] or 0
        parent_item.save()
        # Also check completion?
        # Re-check completion logic on parent?
        # `ReceivedPOItem` assumes `self.check_po_completion()` exists.
        # It's better to move `update_po_item_received` to `POItem` model ideally, but user asked for quick fix.
        # For now, I will manually update parent here.
        
        # Check completion logic replicate or call method if exists?
        # `check_po_completion` is on ReceivedPOItem (Line 182 in viewed content).
        # It updates `header`.
        # I should verify `check_po_completion` implementation. 
        # Line 182-206. It checks `header.items.all()`.
        # So I should copy that logic or make it reusable.
        # Ideally, move these to `POItem` model methods: `update_received_stats()` and `header.check_completion()`.
        # But to be safe and minimal:
        self.update_po_completion_manual(parent_item)

    def update_po_completion_manual(self, po_item):
        # Re-implementation of check_po_completion for delete usage
        header = po_item.header
        all_items = header.items.all()
        is_complete = True
        if not all_items.exists():
            is_complete = False
        else:
            for item in all_items:
                 qty_met = (item.qty_ordered > 0 and item.total_received_qty >= item.qty_ordered)
                 cbm_met = (item.cbm and item.cbm > 0 and item.total_received_cbm >= item.cbm)
                 weight_met = (item.weight and item.weight > 0 and item.total_received_weight >= item.weight)
                 if not (qty_met or cbm_met or weight_met):
                    is_complete = False
                    break
        
        if is_complete:
            header.status = 'Complete'
        else:
            header.status = 'Pending'
        header.save()

    def update_po_item_received(self):
        data = self.po_item.receipts.aggregate(
            total_qty=models.Sum('received_qty'),
            total_cbm=models.Sum('received_cbm'),
            total_weight=models.Sum('received_weight')
        )
        self.po_item.total_received_qty = data['total_qty'] or 0
        self.po_item.total_received_cbm = data['total_cbm'] or 0
        self.po_item.total_received_weight = data['total_weight'] or 0
        self.po_item.save()
        self.check_po_completion()

    def check_po_completion(self):
        header = self.po_item.header
        all_items = header.items.all()
        is_complete = True
        
        if not all_items.exists():
            is_complete = False
        else:
            for item in all_items:
                # User Rule: Complete if QTY OR CBM OR Weight meets target
                # Check if ANY target is met (and target must be > 0 to be valid)
                qty_met = (item.qty_ordered > 0 and item.total_received_qty >= item.qty_ordered)
                cbm_met = (item.cbm and item.cbm > 0 and item.total_received_cbm >= item.cbm)
                weight_met = (item.weight and item.weight > 0 and item.total_received_weight >= item.weight)
                
                if not (qty_met or cbm_met or weight_met):
                    is_complete = False
                    break
        
        if is_complete:
            header.status = 'Complete'
        else:
            header.status = 'Pending' # Allow reverting to pending if edits happen
            
        header.save()

class POAttachment(models.Model):
    header = models.ForeignKey(POHeader, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='po_attachments/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name)

    def __str__(self):
        return f"File for {self.header.po_number}"

class Sale(models.Model):
    PLATFORM_CHOICES = [
        ('Shopee', 'Shopee'),
        ('Lazada', 'Lazada'),
        ('TikTok', 'TikTok'),
    ]
    
    order_id = models.CharField(max_length=100, verbose_name="Order ID")
    sku = models.ForeignKey(MasterItem, on_delete=models.CASCADE, verbose_name="SKU")
    qty = models.IntegerField(default=1, verbose_name="Quantity")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Unit Price")
    
    status = models.CharField(max_length=50, verbose_name="Status")
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES, verbose_name="Platform")
    date = models.DateField(verbose_name="Date")
    shop_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Shop Name")
    
    # Financial columns from Excel
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Extra fields for accurate record
    payment_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    commission_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    voucher_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        unique_together = ('order_id', 'sku')

    def __str__(self):
        return f"{self.order_id} - {self.sku.product_code}"

class JSTStockSnapshot(models.Model):
    """
    Stores the daily snapshot of stock from the JST legacy system file.
    Used for the Hybrid Stock priority rule.
    Maps to data_stock_jst.xlsx columns: รหัสสินค้า, ชื่อสินค้า, คงเหลือ, Type, Stock, Min_Limit, Note
    """
    sku = models.ForeignKey(MasterItem, on_delete=models.CASCADE)
    quantity = models.IntegerField(verbose_name="คงเหลือ") # This is 'Current_Stock' / 'Real_Stock_File'
    jst_min_limit = models.IntegerField(default=0, blank=True, null=True)
    snapshot_date = models.DateField(auto_now_add=True)
    
    # We might want to store the raw values just in case
    raw_type = models.CharField(max_length=100, blank=True, null=True)
    note = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-snapshot_date']

class ImportLog(models.Model):
    IMPORT_TYPE_CHOICES = [
        ('master', 'Master Data'),
        ('stock', 'Stock JST'),
        ('sales', 'Sales History'),
    ]
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Processing', 'Processing'),
        ('Success', 'Success'),
        ('Failed', 'Failed'),
    ]
    
    import_type = models.CharField(max_length=20, choices=IMPORT_TYPE_CHOICES)
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    
    success_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    error_log = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"{self.import_type} - {self.started_at.strftime('%Y-%m-%d %H:%M')}"


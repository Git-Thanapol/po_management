from django.db import models
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum, F
from datetime import timedelta, date
from decimal import Decimal

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
    # Status constants for logic
    STATUS_PENDING = 'Pending' # Waiting for shipment
    STATUS_ARRIVING = 'Arriving Soon'
    STATUS_OVERDUE = 'Overdue'
    STATUS_INCOMPLETE = 'Incomplete'
    STATUS_COMPLETE = 'Complete'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Waiting for Shipment (สินค้ารอจัดส่ง)'),
        (STATUS_ARRIVING, 'Arriving Soon (สินค้าใกล้ถึง)'),
        (STATUS_OVERDUE, 'Overdue (เลยกำหนดจัดส่ง)'),
        (STATUS_INCOMPLETE, 'Incomplete (สินค้าไม่ครบ)'),
        (STATUS_COMPLETE, 'Complete (เรียบร้อย)'),
    ]

    po_number = models.CharField(max_length=50, unique=True, verbose_name="เลข PO")
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, verbose_name="ประเภทรายการ")
    shipping_type = models.CharField(max_length=20, choices=SHIPPING_TYPE_CHOICES, blank=True, null=True, verbose_name="ขนส่ง")
    
    order_date = models.DateField(verbose_name="วันที่สั่งซื้อ")
    estimated_date = models.DateField(blank=True, null=True, verbose_name="วันคาดการณ์")
    
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1.0, verbose_name="เรทเงิน")
    
    # New Costing Fields
    total_yuan = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="ยอดหยวนรวม (Input)")
    shipping_rate_thb_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="เรทค่าขนส่ง THB/คิว")
    bill_date = models.DateField(blank=True, null=True, verbose_name="วันที่บิลเรียกเก็บ") # New field requested

    # Extra fields
    link_shop = models.CharField(max_length=255, blank=True, null=True, verbose_name="ลิงค์ร้านค้า")
    wechat_contact = models.CharField(max_length=100, blank=True, null=True, verbose_name="WeChat / ติดต่อ")
    
    # Benchmarks
    ref_price_shopee = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="ราคา Shopee (Ref)")
    ref_price_lazada = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="ราคา Lazada (Ref)")
    ref_price_tiktok = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="ราคา TikTok (Ref)")
    
    note = models.TextField(blank=True, null=True, verbose_name="หมายเหตุ")
    
    # Status is now dynamic but we can store the last calculated state for easy querying
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_PENDING, verbose_name="สถานะ")

    def save(self, *args, **kwargs):
        # Auto-calculate estimated date if missing
        if not self.estimated_date and self.order_date:
            if self.shipping_type == 'CAR':
                self.estimated_date = self.order_date + timedelta(days=14)
            elif self.shipping_type == 'SHIP':
                self.estimated_date = self.order_date + timedelta(days=25)
        
        super().save(*args, **kwargs)
        # Trigger Proration update after save? 
        # Better to do it explicitly or via signal, but here is safe for simple updates.
        # Only if PK exists (update)
        if self.pk:
            self.prorate_costs()
            self.update_status()

    def prorate_costs(self):
        """
        Prorate total_yuan to items based on qty_ordered.
        Item Total Yuan = Header.total_yuan * (Item.qty_ordered / Header.total_qty)
        """
        items = self.items.all()
        total_qty = items.aggregate(sum_qty=Sum('qty_ordered'))['sum_qty'] or 0
        
        if total_qty > 0 and self.total_yuan > 0:
            for item in items:
                ratio = Decimal(item.qty_ordered) / Decimal(total_qty)
                item.price_yuan = self.total_yuan * ratio
                item.price_baht = item.price_yuan * self.exchange_rate
                item.save(update_fields=['price_yuan', 'price_baht'])
        elif total_qty == 0:
             # Reset if no qty
             items.update(price_yuan=0, price_baht=0)

    def update_status(self):
        """
        Update status based on logic:
        1. Complete: Rx >= Ordered
        2. Incomplete: Rx > 0 but < Ordered
        3. Overdue: Rx == 0 and Today > Est Date
        4. Arriving Soon: Rx == 0 and 0 <= (Est - Today) <= 7
        5. Waiting: Default
        """
        items = self.items.all()
        if not items.exists():
            self.status = self.STATUS_PENDING
            # Avoid recursion if called from save, use update or separate save
            POHeader.objects.filter(pk=self.pk).update(status=self.status)
            return

        # Aggregates
        aggs = items.aggregate(
            total_ordered=Sum('qty_ordered'),
            total_received=Sum('total_received_qty')
        )
        total_ordered = aggs['total_ordered'] or 0
        total_received = aggs['total_received'] or 0
        
        today = date.today()
        est_date = self.estimated_date
        
        new_status = self.STATUS_PENDING
        
        if total_received >= total_ordered and total_ordered > 0:
            new_status = self.STATUS_COMPLETE
        elif total_received > 0:
            new_status = self.STATUS_INCOMPLETE
        else:
            # Not received yet
            if est_date:
                delta = (est_date - today).days
                if delta < 0:
                    new_status = self.STATUS_OVERDUE
                elif 0 <= delta <= 7:
                    new_status = self.STATUS_ARRIVING
                else:
                    new_status = self.STATUS_PENDING
            else:
                 new_status = self.STATUS_PENDING

        if self.status != new_status:
            self.status = new_status
            POHeader.objects.filter(pk=self.pk).update(status=new_status)

    @property
    def total_received_cbm(self):
        return self.items.aggregate(t=Sum('total_received_cbm'))['t'] or 0

    @property
    def total_received_weight(self):
        return self.items.aggregate(t=Sum('total_received_weight'))['t'] or 0

    def __str__(self):
        return self.po_number

class POItem(models.Model):
    header = models.ForeignKey(POHeader, on_delete=models.CASCADE, related_name='items')
    sku = models.ForeignKey(MasterItem, on_delete=models.CASCADE, verbose_name="รหัสสินค้า")
    qty_ordered = models.IntegerField(verbose_name="สั่งซื้อ")
    
    # Prorated Costs (Calculated)
    price_yuan = models.DecimalField(max_digits=12, decimal_places=4, default=0, verbose_name="Total Yuan (Prorated)")
    price_baht = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Total Baht")
    
    # Receiving Stats
    total_received_qty = models.IntegerField(default=0, verbose_name="รับแล้ว (จำนวน)")
    total_received_cbm = models.DecimalField(max_digits=10, decimal_places=4, default=0, verbose_name="รับแล้ว (CBM)")
    total_received_weight = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="รับแล้ว (Weight)")

    def save(self, *args, **kwargs):
        # We don't calc price here primarily anymore, header does it. 
        # But if we update qty, we should trigger header proration? 
        # Ideally yes.
        super().save(*args, **kwargs)

    @property
    def unit_price_yuan(self):
        if self.qty_ordered > 0:
            return self.price_yuan / Decimal(self.qty_ordered)
        return 0

    @property
    def total_shipping_cost(self):
        # Calculated from Received CBM * Header Rate
        # This is an approximation if viewed before full receipt? 
        # Or summation of actual receipts? 
        # Requirement: "Line Estimated Shipping Cost (THB) = Line Received CBM * Header.shipping_rate_thb_cbm"
        cbm = self.total_received_cbm or 0
        rate = self.header.shipping_rate_thb_cbm or 0
        return Decimal(cbm) * Decimal(rate)

    @property
    def unit_cost_thb(self):
        # (Total Baht + Total Shipping Cost) / Qty
        total_thb = (self.price_baht or 0) + self.total_shipping_cost
        if self.qty_ordered > 0:
            return total_thb / Decimal(self.qty_ordered)
        return 0
    
    def __str__(self):
        return f"{self.sku.product_code} (PO {self.header.po_number})"

class POReceiptBatch(models.Model):
    """
    Represents a single "Receive Operation" (Batch) for a PO.
    Groups multiple ReceivedPOItems together.
    """
    header = models.ForeignKey(POHeader, on_delete=models.CASCADE, related_name='receipt_batches')
    batch_no = models.IntegerField(default=1, verbose_name="ครั้งที่")
    bill_date = models.DateField(default=date.today, verbose_name="วันที่บิล")
    received_date = models.DateField(default=date.today, verbose_name="วันที่รับ")
    
    # Batch Totals (Inputs)
    total_cbm = models.DecimalField(max_digits=10, decimal_places=4, default=0, verbose_name="รวม CBM (Batch)")
    total_weight = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="รวม KG (Batch)")
    
    note = models.TextField(blank=True, null=True, verbose_name="หมายเหตุ")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['batch_no']

    def __str__(self):
        return f"Batch {self.batch_no} - PO {self.header.po_number}"

class ReceivedPOItem(models.Model):
    po_item = models.ForeignKey(POItem, on_delete=models.CASCADE, related_name='receipts')
    # Link to Batch
    batch = models.ForeignKey(POReceiptBatch, on_delete=models.CASCADE, related_name='items', null=True, blank=True)
    
    bill_date = models.DateField(default=date.today, verbose_name="วันที่บิล") # Deprecated? Keep for now just in case.
    received_qty = models.IntegerField(default=0, verbose_name="จำนวนที่รับ")
    received_cbm = models.DecimalField(max_digits=10, decimal_places=4, default=0, verbose_name="CBM ที่รับ")
    received_weight = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="น้ำหนักที่รับ")
    received_date = models.DateField(default=date.today, verbose_name="วันที่รับ")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.update_po_item_received()

    def delete(self, *args, **kwargs):
        item = self.po_item
        super().delete(*args, **kwargs)
        # Update parent after delete
        # Can't call checking logic on self, need helper
        self._update_parent_stats(item)

    def update_po_item_received(self):
        self._update_parent_stats(self.po_item)

    def _update_parent_stats(self, item):
        data = item.receipts.aggregate(
            total_qty=Sum('received_qty'),
            total_cbm=Sum('received_cbm'),
            total_weight=Sum('received_weight')
        )
        item.total_received_qty = data['total_qty'] or 0
        item.total_received_cbm = data['total_cbm'] or 0
        item.total_received_weight = data['total_weight'] or 0
        item.save()
        # Trigger header status update
        item.header.update_status()

class POAttachment(models.Model):
    header = models.ForeignKey(POHeader, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='po_attachments/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name)

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
    sku = models.ForeignKey(MasterItem, on_delete=models.CASCADE)
    quantity = models.IntegerField(verbose_name="คงเหลือ") 
    jst_min_limit = models.IntegerField(default=0, blank=True, null=True)
    snapshot_date = models.DateField(auto_now_add=True)
    
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

# Signals to ensure Proration happens when Items are changed
@receiver(post_save, sender=POItem)
def update_header_proration_on_save(sender, instance, created, **kwargs):
    # Avoid infinite loop: Proration updates items, which triggers save.
    # Check if this save was triggered by proration update?
    # We can check if 'price_yuan' was in update_fields.
    if kwargs.get('update_fields') and 'price_yuan' in kwargs['update_fields']:
        return
    
    # Recalculate header's proration for ALL items
    if instance.header:
        instance.header.prorate_costs()
        instance.header.update_status()

@receiver(post_delete, sender=POItem)
def update_header_proration_on_delete(sender, instance, **kwargs):
    if instance.header:
        instance.header.prorate_costs()
        instance.header.update_status()

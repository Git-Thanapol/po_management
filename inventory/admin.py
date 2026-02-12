from django.contrib import admin
from .models import MasterItem, POHeader, POItem, ReceivedPOItem, Sale, JSTStockSnapshot, POReceiptBatch

# Customize Admin Site
admin.site.site_header = "JST System Administration"
admin.site.site_title = "JST Admin Portal"
admin.site.index_title = "Welcome to JST Inventory Management"

@admin.register(MasterItem)
class MasterItemAdmin(admin.ModelAdmin):
    list_display = ('product_code', 'name', 'current_stock', 'min_limit', 'status', 'is_favourite', 'category')
    search_fields = ('product_code', 'name', 'category')
    list_filter = ('category', 'status', 'is_favourite')
    list_editable = ('status', 'is_favourite', 'min_limit')
    ordering = ('product_code',)

class POItemInline(admin.TabularInline):
    model = POItem
    extra = 0
    fields = ('sku', 'qty_ordered', 'total_received_qty')
    readonly_fields = ('total_received_qty',)

@admin.register(POHeader)
class POHeaderAdmin(admin.ModelAdmin):
    list_display = ('po_number', 'order_date', 'status', 'total_yuan', 'estimated_date')
    list_filter = ('status', 'order_date')
    search_fields = ('po_number',)
    inlines = [POItemInline]
    date_hierarchy = 'order_date'

@admin.register(POItem)
class POItemAdmin(admin.ModelAdmin):
    list_display = ('header', 'sku', 'qty_ordered', 'total_received_qty')
    search_fields = ('header__po_number', 'sku__product_code', 'sku__name')
    list_filter = ('header__status',)

@admin.register(ReceivedPOItem)
class ReceivedPOItemAdmin(admin.ModelAdmin):
    list_display = ('po_item', 'received_qty', 'received_date')
    list_filter = ('received_date',)
    search_fields = ('po_item__sku__product_code', 'po_item__header__po_number')

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('order_id', 'sku', 'qty', 'total_price', 'platform', 'date', 'status')
    list_filter = ('platform', 'status', 'date')
    search_fields = ('order_id', 'sku__product_code', 'sku__name')
    date_hierarchy = 'date'

@admin.register(JSTStockSnapshot)
class JSTStockSnapshotAdmin(admin.ModelAdmin):
    list_display = ('sku', 'quantity', 'jst_min_limit', 'snapshot_date')
    list_filter = ('snapshot_date',)
    search_fields = ('sku__product_code', 'sku__name')

@admin.register(POReceiptBatch)
class POReceiptBatchAdmin(admin.ModelAdmin):
    list_display = ('header', 'batch_no', 'bill_date', 'received_date', 'total_cbm', 'total_weight')
    list_filter = ('received_date',)

from django.contrib import admin
from .models import MasterItem, POHeader, POItem, ReceivedPOItem, Sale, JSTStockSnapshot

@admin.register(MasterItem)
class MasterItemAdmin(admin.ModelAdmin):
    list_display = ('product_code', 'name', 'current_stock', 'min_limit', 'product_format', 'category')
    search_fields = ('product_code', 'name', 'category')
    list_filter = ('category', 'product_format')
    ordering = ('product_code',)

class POItemInline(admin.TabularInline):
    model = POItem
    extra = 0
    fields = ('sku', 'qty_ordered', 'total_received_qty', 'price_yuan', 'price_baht', 'cbm')
    readonly_fields = ('cbm', 'price_baht', 'total_received_qty')

@admin.register(POHeader)
class POHeaderAdmin(admin.ModelAdmin):
    list_display = ('po_number', 'order_date', 'status', 'order_type', 'shipping_type', 'total_yuan', 'estimated_date')
    list_filter = ('status', 'order_type', 'shipping_type')
    search_fields = ('po_number',)
    inlines = [POItemInline]
    date_hierarchy = 'order_date'

@admin.register(POItem)
class POItemAdmin(admin.ModelAdmin):
    list_display = ('header', 'sku', 'qty_ordered', 'total_received_qty', 'price_yuan', 'price_baht')
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
    search_fields = ('order_id', 'sku__product_code', 'sku__name', 'shop_name')
    date_hierarchy = 'date'

@admin.register(JSTStockSnapshot)
class JSTStockSnapshotAdmin(admin.ModelAdmin):
    list_display = ('sku', 'quantity', 'jst_min_limit', 'snapshot_date')
    list_filter = ('snapshot_date',)
    search_fields = ('sku__product_code', 'sku__name')

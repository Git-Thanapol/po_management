from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('otp-verify/', views.otp_verify_view, name='otp_verify'),
    
    # Core Pages
    path('', views.daily_sales_view, name='sales_summary'), # Home is sales summary
    path('stock/', views.stock_report_view, name='stock_report'),
    path('po/', views.po_list_view, name='po_list'),
    path('import/', views.import_data_view, name='import_data'),
    
    # Ajax/Actions
    path('po/<int:po_id>/', views.po_detail_view, name='po_detail'),
    path('po/receive/<int:po_item_id>/', views.receive_po_item, name='receive_po_item'),
    path('po/create/', views.po_create_view, name='po_create'), # New URL
    path('stock/update-limit/<str:sku>/', views.update_min_limit, name='update_min_limit'),
    path('products/', views.product_list_view, name='product_list'),
    path('products/get/<str:sku>/', views.get_product_detail, name='get_product_detail'),
    path('products/save/', views.save_product_view, name='save_product'),
    path('stock/history/<str:sku>/', views.get_po_history, name='get_po_history'),
    path('sales/history/<str:sku>/', views.get_sales_history, name='get_sales_history'),
]

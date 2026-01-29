from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, F, Q, DecimalField
from django.db.models.functions import Coalesce
from django.views.decorators.http import require_POST
from datetime import datetime, date, timedelta

# Import Models and Utils
from .models import MasterItem, Sale, POHeader, POItem, ReceivedPOItem, JSTStockSnapshot
from utils.auth_utils import send_otp_email, create_token, generate_otp
from utils.importers import ImportService
from utils.stock_calculator import StockService

import os
import json
import threading
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
# ... other imports
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from .models import MasterItem, Sale, POHeader, POItem, ReceivedPOItem, JSTStockSnapshot
from utils.auth_utils import send_otp_email, create_token, generate_otp

def get_allowed_users():
    # Parse allowed users from env
    allowed_str = os.getenv('ALLOWED_USERS', '[]')
    try:
        # It's stored as a string representation of list in .env usually?
        # User env: allowed_users = ["stock@swiftpassion.net", ...]
        # json.loads might fail if single quotes are used.
        return json.loads(allowed_str.replace("'", '"')) 
    except:
        # Fallback manual parse or loose check
        return [u.strip().strip('"').strip("'") for u in allowed_str.strip('[]').split(',')]

def login_view(request):
    if request.user.is_authenticated:
        return redirect('sales_summary')
        
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        allowed_users = get_allowed_users()
        
        # Check if email is in allowed list
        # Simple string matching
        if email not in [u.lower() for u in allowed_users]:
            messages.error(request, "❌ อีเมลนี้ไม่มีสิทธิ์ใช้งานระบบ")
            return redirect('login')
        
        # Generate OTP
        otp_code = generate_otp()
        
        # Save to session
        request.session['otp_code'] = otp_code
        request.session['otp_email'] = email
        
        # Send Email
        if send_otp_email(email, otp_code):
            messages.success(request, f"✅ ส่งรหัส OTP ไปยัง {email} แล้ว")
            return redirect('otp_verify')
        else:
            messages.error(request, "❌ เกิดข้อผิดพลาดในการส่งอีเมล")
            
    return render(request, 'inventory/login.html')

def otp_verify_view(request):
    if 'otp_email' not in request.session:
        return redirect('login')
        
    if request.method == 'POST':
        input_otp = request.POST.get('otp', '').strip()
        session_otp = request.session.get('otp_code')
        email = request.session.get('otp_email')
        
        if input_otp == session_otp:
            # Login Success
            # Get or Create User
            user, created = User.objects.get_or_create(username=email, defaults={'email': email})
            login(request, user)
            
            # Clear session
            del request.session['otp_code']
            del request.session['otp_email']
            
            messages.success(request, "เข้าสู่ระบบสำเร็จ!")
            return redirect('sales_summary')
        else:
            messages.error(request, "❌ รหัส OTP ไม่ถูกต้อง")
            
    return render(request, 'inventory/otp_verify.html')

@login_required
def import_data_view(request):
    from .models import ImportLog
    
    # Handle File Upload
    if request.method == 'POST':
        import_type = request.POST.get('type')
        uploaded_file = request.FILES.get('file')
        
        if not uploaded_file:
            messages.error(request, "กรุณาเลือกไฟล์ (Please select a file)")
            return redirect('import_data')
            
        # 1. Save file to disk (temp or media) so thread can access it
        # We use default_storage
        file_path = default_storage.save(f"imports/{uploaded_file.name}", ContentFile(uploaded_file.read()))
        full_path = default_storage.path(file_path)
        
        # 2. Create Log Entry
        log = ImportLog.objects.create(
            import_type=import_type,
            filename=uploaded_file.name,
            status='Pending'
        )
        
        # 3. Start Background Thread
        thread = threading.Thread(target=process_import_background, args=(log.id, full_path, import_type))
        thread.setDaemon(True)
        thread.start()
        
        messages.info(request, f"⏳ เริ่มต้นประมวลผล {uploaded_file.name} ในเบื้องหลังแล้ว (Started in background)...")
        
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
            
        return redirect('import_data')

    # GET: Show Logs
    recent_logs = ImportLog.objects.all().order_by('-started_at')[:10]
            
    return render(request, 'inventory/import_data.html', {'result_log': None, 'recent_logs': recent_logs})

def process_import_background(log_id, file_path, import_type):
    from .models import ImportLog
    from django.utils import timezone
    
    # Re-fetch log to ensure thread safety connection
    try:
        log = ImportLog.objects.get(id=log_id)
        log.status = 'Processing'
        log.save()
        
        result = None
        
        # We need a mock file object or modify ImportService to accept path.
        # ImportService expects "file-like object" for pd.read_excel usually, or path string.
        # pandas read_excel accepts string path.
        
        try:
            if import_type == 'master':
                # ImportService static methods might expect request.FILES object (UploadedFile).
                # Checking ImportService... it likely uses pd.read_excel(file).
                # passing full_path (str) works for pandas.
                result = ImportService.import_master_items(file_path)
            elif import_type == 'stock':
                result = ImportService.import_stock_jst(file_path)
            elif import_type == 'sales':
                result = ImportService.import_sales_data(file_path)
            
            if result:
                log.success_count = result.get('success', 0)
                log.failed_count = result.get('failed', 0)
                
                if result.get('errors'):
                     log.error_log = "\n".join(result['errors'])
                
                if log.failed_count == 0 and log.success_count > 0:
                    log.status = 'Success'
                elif log.success_count > 0:
                     log.status = 'Success' # Partial success is still success usually, or Warning
                else:
                     log.status = 'Failed'
            else:
                log.status = 'Failed'
                log.error_log = "No result returned from service."

        except Exception as e:
            log.status = 'Failed'
            log.error_log = str(e)
            
    except Exception as outer_e:
        print(f"Background Thread Error: {outer_e}")
    finally:
        # Cleanup
        if 'log' in locals():
            log.completed_at = timezone.now()
            log.save()
            
        # Optional: Delete file after processing
        # if os.path.exists(file_path):
        #    os.remove(file_path)


@login_required
def po_list_view(request):
    po_number_query = request.GET.get('po_number', '').strip()
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    
    # Revert to showing Items as per user requirement
    items = POItem.objects.all().select_related('header', 'sku').prefetch_related('receipts', 'header__attachments').order_by('-header__order_date')
    
    if po_number_query:
        items = items.filter(header__po_number__icontains=po_number_query)
        
    if search_query:
        items = items.filter(Q(sku__product_code__icontains=search_query) | Q(sku__name__icontains=search_query))
        
    if status_filter:
        if status_filter == 'arriving_soon':
            # Next 7 days
            next_week = date.today() + timedelta(days=7)
            items = items.filter(header__status__in=['Pending'], header__estimated_date__range=[date.today(), next_week])
        elif status_filter == 'incomplete':
            # Pending but has received some? Or just Pending?
            # "สินค้าไม่ครบ" usually means Received Partial.
            # We need to filter items that have receipts but not complete.
            # Filtering on property or aggregate is hard without annotation.
            # Let's assume for now it means "Pending" but we want to differentiate from "Waiting Shipment".
            # Actually, "Waiting Shipment" (รอจัดส่ง) -> Qty Received = 0
            # "Incomplete" (สินค้าไม่ครบ) -> Qty Received > 0 but not Complete
            # Annotating received qty first:
            from django.db.models import Sum
            items = items.annotate(received_sum=Sum('receipts__received_qty'))
            items = items.filter(header__status='Pending', received_sum__gt=0)
        elif status_filter == 'waiting_shipment':
            # Pending and No Receipts
            from django.db.models import Sum
            items = items.annotate(received_sum=Sum('receipts__received_qty'))
            items = items.filter(header__status='Pending', received_sum__isnull=True) # or 0
        elif status_filter == 'overdue':
            items = items.exclude(header__status='Complete').filter(header__estimated_date__lt=date.today())
        else:
            # Match standard status (Pending, Complete)
            items = items.filter(header__status=status_filter)
        
    # Category Filter
    categories = MasterItem.objects.values_list('category', flat=True).distinct().order_by('category')
    selected_category = request.GET.get('category', '')
    if selected_category:
        items = items.filter(sku__category=selected_category)
        
    context = {
        'po_items': items,
        'po_number_query': po_number_query,
        'search_query': search_query,
        'search_query': search_query,
        'status_filter': status_filter,
        'categories': categories,
        'selected_category': selected_category,
    }
    return render(request, 'inventory/po_list.html', context)

@login_required
def po_detail_view(request, po_id):
    po = get_object_or_404(POHeader, id=po_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_header':
            # Edit Header fields
            try:
                # Update all editable fields
                new_po_number = request.POST.get('po_number')
                if new_po_number != po.po_number:
                     # Check duplicate
                     if POHeader.objects.filter(po_number=new_po_number).exclude(id=po.id).exists():
                         messages.error(request, f"❌ PO Number {new_po_number} already exists!")
                         return redirect('po_detail', po_id=po.id)
                     po.po_number = new_po_number

                order_date_str = request.POST.get('order_date')
                if order_date_str:
                    po.order_date = order_date_str
                
                est_date_str = request.POST.get('estimated_date')
                if est_date_str:
                    po.estimated_date = est_date_str
                else:
                    po.estimated_date = None

                po.order_type = request.POST.get('order_type')
                po.shipping_type = request.POST.get('shipping_type')
                
                po.exchange_rate = float(request.POST.get('exchange_rate', 1.0) or 1.0)
                po.shipping_cost_baht = float(request.POST.get('shipping_cost_baht', 0) or 0)
                po.shipping_rate_kg = float(request.POST.get('shipping_rate_kg', 0) or 0)
                # po.shipping_rate_cbm = float(request.POST.get('shipping_rate_cbm', 0) or 0) # Deprecated
                
                # New Fields
                po.shipping_rate_yuan_per_cbm = float(request.POST.get('shipping_rate_yuan_per_cbm', 0) or 0)
                
                # Prices
                po.shopee_price = float(request.POST.get('shopee_price', 0) or 0) if request.POST.get('shopee_price') else None
                po.lazada_price = float(request.POST.get('lazada_price', 0) or 0) if request.POST.get('lazada_price') else None
                po.tiktok_price = float(request.POST.get('tiktok_price', 0) or 0) if request.POST.get('tiktok_price') else None

                po.note = request.POST.get('note', '')
                po.link_shop = request.POST.get('link_shop', '')
                po.wechat_contact = request.POST.get('wechat_contact', '')
                po.tracking_no = request.POST.get('tracking_no', '')
                
                po.save()
                
                # Handle Files
                files = request.FILES.getlist('attachments')
                for f in files:
                    from .models import POAttachment
                    POAttachment.objects.create(header=po, file=f)
                
                messages.success(request, "✅ บันทึกข้อมูล PO เรียบร้อย")
            except Exception as e:
                messages.error(request, f"Error: {e}")
                
        elif action == 'receive_items':
            # Bulk Receive with Qty, CBM, Weight support
            count = 0
            receive_date_str = request.POST.get('receive_date')
            if not receive_date_str:
                receive_date = date.today()
            else:
                receive_date = receive_date_str

            for key, value in request.POST.items():
                if key.startswith('receive_qty_'):
                    try:
                        # Extract Item ID
                        item_id = key.split('_')[2]
                        
                        # Get Values (Qty, CBM, Weight)
                        qty = int(value) if value else 0
                        cbm = float(request.POST.get(f'receive_cbm_{item_id}', 0) or 0)
                        weight = float(request.POST.get(f'receive_weight_{item_id}', 0) or 0)
                        
                        if qty > 0 or cbm > 0 or weight > 0:
                            item = POItem.objects.get(id=item_id, header=po)
                            
                            # Create Receipt
                            ReceivedPOItem.objects.create(
                                po_item=item,
                                received_qty=qty,
                                received_cbm=cbm,
                                received_weight=weight,
                                received_date=receive_date
                            )
                            count += 1
                    except Exception as e:
                        print(f"Error receiving item {key}: {e}")
                        messages.warning(request, f"Error receiving item {key}: {e}")
                        continue
            
            if count > 0:
                messages.success(request, f"✅ รับสินค้าเรียบร้อย {count} รายการ")
            else:
                messages.warning(request, "⚠️ ไม่มีการรับสินค้า (ตรวจสอบจำนวนที่ระบุ)")
                
                # Auto-check status if we want to auto-complete PO?
                # Need logic to check if all items fully received.
                
    return render(request, 'inventory/po_detail.html', {'po': po})

@login_required
def receive_po_item(request, po_item_id):
    if request.method == 'POST':
        item = get_object_or_404(POItem, id=po_item_id)
        qty = int(request.POST.get('received_qty', 0))
        date_str = request.POST.get('received_date')
        
        if qty > 0 and date_str:
            ReceivedPOItem.objects.create(
                po_item=item,
                received_qty=qty,
                received_date=date_str
            )
            # Update Master Stock? 
            # Requirements say: "Fetch System Stock: Calculate Initial + Total Received - Total Sold"
            # So creating ReceivedPOItem is enough for the calculation logic in StockService.
            # But if we want to update the cache `current_stock` in MasterItem immediately, we should do it here.
            # Given the StockService logic "initial + received - sold", if 'initial' is 'current_stock' field, then updating it here double counts?
            # I decided StockService uses dynamic calc. But to make "Initial" valid, we should NOT update master_item.current_stock for every receipt IF it acts as "Base".
            # BUT, usually 'current_stock' IS the cache.
            # I'll update MasterItem.current_stock just in case it's used elsewhere as a cache.
            # "Fetch System Stock: Calculate Initial + Total Received - Total Sold." -> If I update MasterItem.current_stock, then 'Initial' grows.
            # This implies `current_stock` should NOT be touched if it's "Initial".
            # Result: I will NOT update MasterItem.current_stock here to avoid double counting logic, unless user meant 'current_stock' is the LIVE value.
            # Requirement: "Fetch System Stock: Calculate Initial + Total Received - Total Sold".
            # This strongly implies dynamic calculation from a static base. I'll stick to not updating MasterItem.
            
            messages.success(request, f"บันทึกการรับสินค้า {item.sku.product_code} จำนวน {qty} ชิ้น เรียบร้อย")
        else:
            messages.error(request, "ข้อมูลไม่ถูกต้อง")
            
    return redirect('po_list')

def logout_view(request):
    logout(request)
    messages.info(request, "ออกจากระบบแล้ว")
    return redirect('login')

@login_required
def daily_sales_view(request):
    # Standard Date Handling
    today = date.today()
    default_end = today + timedelta(days=30)
    
    # Session Persistence Logic
    if not request.GET and 'sales_filter_mode' in request.session:
        # Load from session if no GET params and session exists
        start_date_str = request.session.get('sales_start_date')
        end_date_str = request.session.get('sales_end_date')
        search_query = request.session.get('sales_search', '')
        filter_mode = request.session.get('sales_filter_mode', 'general')
        movement_filter = request.session.get('sales_movement', 'all')
        focus_date_str = request.session.get('sales_focus_date', '')
        selected_category = request.session.get('sales_category', '')
    else:
        # Load from GET or use Defaults (and save to session if GET is present, or just always save current state)
        # Using .get() returns None if missing, so we handle defaults below
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        search_query = request.GET.get('search', '').strip()
        filter_mode = request.GET.get('filter_mode', 'general')
        movement_filter = request.GET.get('movement', 'all')
        focus_date_str = request.GET.get('focus_date', '')
        selected_category = request.GET.get('category', '').strip()
        
        # Save to session (only if meaningful? easier to always save current view state)
        request.session['sales_start_date'] = start_date_str
        request.session['sales_end_date'] = end_date_str
        request.session['sales_search'] = search_query
        request.session['sales_filter_mode'] = filter_mode
        request.session['sales_movement'] = movement_filter
        request.session['sales_focus_date'] = focus_date_str
        request.session['sales_category'] = selected_category
    
    # Parse dates or use defaults
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today
    except (ValueError, TypeError):
        start_date = today

    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else default_end
    except (ValueError, TypeError):
        end_date = default_end
    
    focus_date = None
    if focus_date_str:
        try:
            focus_date = datetime.strptime(focus_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

    # 1. Base Product Query
    products = MasterItem.objects.all().order_by('product_code')

    if search_query:
        products = products.filter(Q(product_code__icontains=search_query) | Q(name__icontains=search_query))

    # Category Filter
    categories = MasterItem.objects.values_list('category', flat=True).distinct().order_by('category')
    if selected_category:
        products = products.filter(category=selected_category)
        
    # 2. Annotate Total Period Sales/Qty
    products = products.annotate(
        period_qty=Coalesce(Sum('sale__qty', filter=Q(sale__date__range=(start_date, end_date))), 0),
        period_amount=Coalesce(Sum('sale__total_price', filter=Q(sale__date__range=(start_date, end_date))), 0, output_field=DecimalField())
    )

    # 3. Apply Filters based on Mode
    if filter_mode == 'focus' and focus_date:
        # Focus Mode: Filter products that have ANY sale on the specific focus date
        # Use distinct() to avoid duplicates if multiple sales occur on that date
        products = products.filter(sale__date=focus_date).distinct()
        
    else:
        # General Mode: Apply Movement Filter
        if movement_filter == 'active': # "มีการเคลื่อนไหว"
            products = products.filter(period_qty__gt=0)
        elif movement_filter == 'inactive': # "ไม่มีการเคลื่อนไหว"
            products = products.filter(period_qty=0)
    
    # 4. Fetch Daily Sales Breakdown (Optimization)
    # Get all sales within the range for the filtered products
    # To avoid N+1, we fetch all relevant sales and map them in Python
    # Need to execute the product query first to get IDs? 
    # Or just filter Sales by the same criteria? Filtering sales by product criteria is complex if search/category involved.
    # Simple approach: Fetch sales for ALL products (or use product__in if list is small, but list might be large).
    # Better: Fetch sales joined with product filters.
    
    sales_qs = Sale.objects.filter(
        date__range=(start_date, end_date)
    ).values('sku_id', 'date', 'qty')
    
    if search_query:
        sales_qs = sales_qs.filter(Q(sku__product_code__icontains=search_query) | Q(sku__name__icontains=search_query))
    if selected_category:
        sales_qs = sales_qs.filter(sku__category=selected_category)
        
    # Build Sales Map: sales_map[product_id][date_obj] = qty
    sales_map = {}
    for entry in sales_qs:
        p_id = entry['sku_id']
        d = entry['date']
        q = entry['qty']
        
        if p_id not in sales_map:
            sales_map[p_id] = {}
        
        # Sales might appear multiple times per day if multiple Sale records?
        # values('sku_id', 'date', 'qty') doesn't sum if not annotated.
        # correctly: .values('sku_id', 'date').annotate(total_qty=Sum('qty'))
        # But for now let's just sum it up here to be safe or fix the query.
        sales_map[p_id][d] = sales_map[p_id].get(d, 0) + q

    # 5. Generate Date Columns
    date_columns = []
    curr = start_date
    while curr <= end_date:
        date_columns.append(curr)
        curr += timedelta(days=1)
        
    # 6. Attach Daily Sales List to Products
    # We need to evaluate the products queryset now
    products = list(products) # Hit DB
    
    for p in products:
        p.daily_sales = []
        p_sales = sales_map.get(p.pk, {})
        for d in date_columns:
            qty = p_sales.get(d, 0)
            p.daily_sales.append(qty)
            
    # Thai Date Headers
    thai_months_abbr = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    date_headers = []
    for d in date_columns:
        # e.g. "1 ม.ค."
        date_headers.append(f"{d.day} {thai_months_abbr[d.month]}")

    context = {
        'products': products,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'date_headers': date_headers,
        'search_query': search_query,
        'categories': categories,
        'selected_category': selected_category,
        'movement_filter': movement_filter,
        'filter_mode': filter_mode,
        'focus_date': focus_date_str,
        'total_period_sales': sum(p.period_amount for p in products), # Calculate explicitly since queryset was evaluated
    }
    
    return render(request, 'inventory/sales_summary.html', context)

@login_required
def stock_report_view(request):
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    
    # optimize: prefetch logic needed for StockService? 
    # StockService fetches JSTStockSnapshot and aggregations.
    # To avoid N+1, ideally we fetch all snapshots and aggregations in bulk.
    # But for simplicity/time, we iterate. 800 items might take 1-2s. Acceptable for prototype V1.
    
    items_qs = MasterItem.objects.all().order_by('product_code')
    if search_query:
        items_qs = items_qs.filter(Q(product_code__icontains=search_query) | Q(name__icontains=search_query))
        
    # Category Filter
    categories = MasterItem.objects.values_list('category', flat=True).distinct().order_by('category')
    selected_category = request.GET.get('category', '')
    if selected_category:
        items_qs = items_qs.filter(category=selected_category)
        
    stock_data = []
    
    for item in items_qs:
        # Calculate Stock
        calc = StockService.calculate_stock(item.product_code)
        
        # Build display object
        obj = {
            'sku': item.product_code,
            'name': item.name,
            'image_url': item.image.url if item.image else None,
            'qty': calc['qty'],
            'status': calc['status'],
            'source': calc['source'],
            'min_limit': item.min_limit,
            'min_limit': item.min_limit,
            'note': item.note,
            'total_sales': Sale.objects.filter(sku=item).aggregate(total=Sum('qty'))['total'] or 0,
            'total_received': ReceivedPOItem.objects.filter(po_item__sku=item).aggregate(total=Sum('received_qty'))['total'] or 0,
        }
        
        # Apply Status Filter in Python
        include = True
        if status_filter == 'empty':
            if "หมด" not in obj['status']: include = False
        elif status_filter == 'low':
            if "ใกล้" not in obj['status']: include = False
        elif status_filter == 'ok':
            if "มี" not in obj['status']: include = False
            
        if include:
            stock_data.append(obj)
            
    context = {
        'stock_items': stock_data,
        'stock_items': stock_data,
        'search_query': search_query,
        'selected_status': status_filter,
        'categories': categories,
        'selected_category': selected_category,
    }
    return render(request, 'inventory/stock_report.html', context)

@login_required
def update_min_limit(request, sku):
    if request.method == 'POST':
        # Check if Bulk Update (special sku 'bulk')
        if sku == 'bulk':
            count = 0
            for key, value in request.POST.items():
                if key.startswith('limit_'):
                    real_sku = key.split('limit_')[1]
                    try:
                        new_limit = int(value)
                        MasterItem.objects.filter(product_code=real_sku).update(min_limit=new_limit)
                        count += 1
                    except ValueError:
                        continue
            messages.success(request, f"บันทึกค่าจุดเตือนเรียบร้อยแล้ว ({count} รายการ)")
            return redirect('stock_report')
        else:
            # Single Update (unused in current template but good for API)
            pass
            
    return redirect('stock_report')

def get_po_history(request, sku):
    """
    Returns HTML partial for PO History of a specific SKU
    """
    if not request.user.is_authenticated:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()
    
    # Fetch Items
    po_items = POItem.objects.filter(sku__product_code=sku).select_related('header', 'sku').prefetch_related('receipts').order_by('-header__order_date')
    
    formatted_data = []
    for item in po_items:
        # received date (first receipt?)
        first_receipt = item.receipts.order_by('received_date').first()
        received_date = first_receipt.received_date if first_receipt else None
        
        duration = None
        if received_date and item.header.order_date:
            duration = (received_date - item.header.order_date).days
            
        # Cost per piece (Baht)
        cost_per_piece = 0
        if item.qty_ordered > 0:
            cost_per_piece = item.price_baht / item.qty_ordered
            
        # Total Yuan (if price_yuan is unit)
        total_yuan = item.price_yuan * item.qty_ordered
        
        formatted_data.append({
            'item': item,
            'received_date': received_date,
            'duration': duration,
            'cost_per_piece': cost_per_piece,
            'total_yuan': total_yuan
        })
        
    return render(request, 'inventory/partials/po_history_table.html', {'history_items': formatted_data})

def get_sales_history(request, sku):
    if not request.user.is_authenticated:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()
    
    # Fetch Sales
    sales_qs = Sale.objects.filter(sku__product_code=sku).order_by('-date')
    
    sales_data = []
    for s in sales_qs:
        # Status Logic
        status_class = 'bg-secondary'
        s_text = str(s.status).strip() if s.status else ''
        
        if s_text in ['Comp', 'สำเร็จ', 'Complete', 'จัดส่งแล้ว', 'Delivered']:
            status_class = 'bg-success'
        elif s_text in ['Cancel', 'ยกเลิก', 'ผิดปกติ', 'Failed', 'Cancelled', 'Return']:
            status_class = 'bg-danger'
        elif s_text in ['ที่ต้องจัดส่ง', 'Pending', 'To Ship', 'นัดรับสินค้า']:
            status_class = 'bg-warning text-dark'
        elif 'ชำระแล้ว' in s_text or s_text in ['Paid', 'Confirmed']:
            status_class = 'bg-info text-dark'
            
        # Platform Logic
        platform_color = None
        if s.platform == 'Shopee': platform_color = '#ee4d2d'
        elif s.platform == 'Lazada': platform_color = '#0f146d'
        elif s.platform == 'TikTok': platform_color = '#000000'
        
        # Fees
        fees = (s.payment_fee or 0) + (s.commission_fee or 0) + (s.service_fee or 0) + (s.shipping_fee or 0)
        
        sales_data.append({
            'date': s.date,
            'status': s.status,
            'status_class': status_class,
            'platform': s.platform,
            'platform_color': platform_color,
            'order_id': s.order_id,
            'shop_name': s.shop_name,
            'qty': s.qty,
            'price': s.price,
            'total_price': s.total_price,
            'fees': fees,
            'net_price': s.net_price,
        })
    
    # Calc totals
    t_qty = sales_qs.aggregate(Sum('qty'))['qty__sum'] or 0
    t_amt = sales_qs.aggregate(Sum('total_price'))['total_price__sum'] or 0
    t_net = sales_qs.aggregate(Sum('net_price'))['net_price__sum'] or 0
    
    return render(request, 'inventory/partials/sales_history_table.html', {
        'sales': sales_data,
        'total_qty': t_qty,
        'total_amount': t_amt,
        'total_net': t_net
    })

@login_required
def po_create_view(request):
    # Fetch Master Items for Datalist
    master_items = MasterItem.objects.all().order_by('product_code')
    
    if request.method == 'POST':
        try:
            # 1. Create Header
            # Extract basic fields
            po_number = request.POST.get('po_number')
            order_date_str = request.POST.get('order_date')
            order_type = request.POST.get('order_type')
            shipping_type = request.POST.get('shipping_type')
            estimated_date_str = request.POST.get('estimated_date') or None
            
            # Parse Dates
            order_date = datetime.strptime(order_date_str, '%Y-%m-%d').date()
            if estimated_date_str:
                estimated_date = datetime.strptime(estimated_date_str, '%Y-%m-%d').date()
            else:
                estimated_date = None
            
            # Function helper for floats
            def get_float(name, default=0):
                val = request.POST.get(name, '')
                try: return float(val)
                except: return default

            exchange_rate = get_float('exchange_rate', 1.0)
            shipping_cost_baht = get_float('shipping_cost_baht')
            
            link_shop = request.POST.get('link_shop')
            wechat = request.POST.get('wechat_contact')
            shopee_price = get_float('shopee_price', None)
            lazada_price = get_float('lazada_price', None)
            tiktok_price = get_float('tiktok_price', None)
            note = request.POST.get('note')
             # New Fields
            shipping_rate_yuan = get_float('shipping_rate_yuan_per_cbm', 0)
            shipping_rate_kg = get_float('shipping_rate_kg', 0)

            # Create PO Header
            po = POHeader.objects.create(
                po_number=po_number,
                order_date=order_date, # Now a date object
                order_type=order_type,
                shipping_type=shipping_type if order_type == 'IMPORTED' else None,
                estimated_date=estimated_date, # Now a date object or None
                exchange_rate=exchange_rate,
                shipping_cost_baht=shipping_cost_baht,
                shipping_rate_yuan_per_cbm=shipping_rate_yuan,
                shipping_rate_kg=shipping_rate_kg,
                link_shop=link_shop,
                wechat_contact=wechat,
                shopee_price=shopee_price,
                lazada_price=lazada_price,
                tiktok_price=tiktok_price,
                note=note,
                status='Pending'
            )
            
            # Handle Attachment (Single file for now as per simple implementation)
            if 'attachments' in request.FILES:
                from .models import POAttachment
                for f in request.FILES.getlist('attachments'):
                    POAttachment.objects.create(header=po, file=f)

            # 2. Process Items (Dynamic Rows)
            for key in request.POST:
                if key.startswith('sku_'):
                    row_id = key.split('_')[1]
                    sku_code = request.POST.get(key)
                    
                    if not sku_code: continue
                    
                    # Verify SKU exists
                    try:
                        master_item = MasterItem.objects.get(product_code=sku_code)
                    except MasterItem.DoesNotExist:
                        # If AJAX, we might want to return error or just skip?
                        # Skipping with warning is arguably better for bulk entry.
                        # messages.warning(request, f"Review: SKU {sku_code} not found, skipped.")
                        continue
                        
                    qty = get_float(f'qty_{row_id}', 1)
                    yuan = get_float(f'yuan_{row_id}', 0)
                    baht = get_float(f'baht_{row_id}', 0)
                    # Dimensions removed
                    
                    cbm_val = get_float(f'cbm_{row_id}', None) # User total line CBM? Or Unit?
                    # In html logic we assumed total CBM for summary. 
                    # But usually data is stored as per-unit specs OR total line specs?
                    # Model has `cbm` field. Is it unit or total?
                    # User prompt: "Direct CBM Input ... defaulting to 1.0".
                    # Let's save what user input.
                    
                    weight_val = get_float(f'kg_{row_id}', None)
                    
                    POItem.objects.create(
                        header=po,
                        sku=master_item,
                        qty_ordered=int(qty),
                        price_yuan=yuan, 
                        price_baht=baht,
                        cbm=cbm_val,
                        weight=weight_val
                    )
            
            # Success
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'status': 'success', 'redirect_url': '/po/'})
            
            messages.success(request, f"✅ สร้างใบสั่งซื้อ {po_number} สำเร็จ!")
            return redirect('po_list')

        except Exception as e:
            # Error Handling
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'status': 'error', 'message': str(e)})
                
            messages.error(request, f"❌ Error creating PO: {e}")
            # Fallback for non-AJAX
            
    return render(request, 'inventory/po_create.html', {'master_items': master_items})

@login_required
def product_list_view(request):
    search_query = request.GET.get('search', '').strip()
    
    products = MasterItem.objects.all().order_by('product_code')
    
    if search_query:
        products = products.filter(Q(product_code__icontains=search_query) | Q(name__icontains=search_query))
        
    # Category Filter
    categories = MasterItem.objects.values_list('category', flat=True).distinct().order_by('category')
    selected_category = request.GET.get('category', '')
    if selected_category:
        products = products.filter(category=selected_category)
        
    return render(request, 'inventory/product_list.html', {
        'products': products,
        'products': products,
        'search_query': search_query,
        'categories': categories,
        'selected_category': selected_category,
    })

@login_required
def get_product_detail(request, sku):
    try:
        p = MasterItem.objects.get(product_code=sku)
        data = {
            'product_code': p.product_code,
            'name': p.name,
            'min_limit': p.min_limit,
            'category': p.category or '',
            'product_format': p.product_format or '',
            'note': p.note or '',
            'image_url': p.image.url if p.image else ''
        }
        return JsonResponse(data)
    except MasterItem.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

@login_required
def save_product_view(request):
    if request.method == 'POST':
        product_code = request.POST.get('product_code')
        name = request.POST.get('name')
        min_limit = request.POST.get('min_limit', 0)
        category = request.POST.get('category')
        product_format = request.POST.get('product_format')
        note = request.POST.get('note')
        
        # Check if creating or editing
        # We use a hidden flag `mode` or check if object exists?
        # Usually for Edit, SKU is readonly but passed.
        # But for Create, SKU is new.
        mode = request.POST.get('mode', 'create') # create or edit
        
        try:
            if mode == 'create':
                if MasterItem.objects.filter(product_code=product_code).exists():
                     messages.error(request, f"❌ รหัสสินค้า {product_code} มีอยู่ในระบบแล้ว")
                     return redirect('product_list')
                item = MasterItem(product_code=product_code)
            else:
                item = get_object_or_404(MasterItem, product_code=product_code)
            
            item.name = name
            item.min_limit = int(min_limit) if min_limit else 0
            item.category = category
            item.product_format = product_format
            item.note = note
            
            if 'image' in request.FILES:
                item.image = request.FILES['image']
                
            item.save()
            messages.success(request, f"✅ บันทึกข้อมูล {product_code} เรียบร้อย")
            
        except Exception as e:
            messages.error(request, f"❌ Error: {e}")
            
    return redirect('product_list')

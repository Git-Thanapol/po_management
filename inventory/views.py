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
from .models import MasterItem, Sale, POHeader, POItem, ReceivedPOItem, JSTStockSnapshot, POReceiptBatch
from utils.auth_utils import send_otp_email, create_token, generate_otp
from utils.importers import ImportService
from utils.stock_calculator import StockService

import os
import json
import threading
from decimal import Decimal
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

                if request.POST.get('order_type'):
                    po.order_type = request.POST.get('order_type')
                
                if request.POST.get('shipping_type'):
                    po.shipping_type = request.POST.get('shipping_type')
                
                # Check presence before converting for numeric fields that might be disabled
                # Check presence before converting for numeric fields that might be disabled
                if 'exchange_rate' in request.POST:
                    po.exchange_rate = Decimal(request.POST.get('exchange_rate', 1.0) or 1.0)
                
                if 'shipping_cost_baht' in request.POST:
                    # Note: shipping_cost_baht might not be in model anymore based on previous checks, but if it is transient or deprecated model field:
                    # Model has 'shipping_rate_thb_cbm'.
                    # Let's check models.py line 67 in Step 651. It NO LONGER has shipping_cost_baht.
                    # It has total_yuan, shipping_rate_thb_cbm.
                    # I should probably remove this line or map it correctly if needed.
                    # Assuming legacy field removal, I will comment it out or remove it to avoid AttributeError if field is gone.
                    # Warning: The user code might still have it in models.py if I missed a migration or something.
                    # But Step 651 showed models.py from line 29. shipping_cost_baht was NOT in lines 62-66.
                    pass 
                
                # shipping_rate_kg -> removed from model in step 651 checklist.
                if 'shipping_rate_kg' in request.POST:
                     pass
                
                # Deprecated cbm rate
                # po.shipping_rate_cbm = ...
                
                # New Fields
                po.total_yuan = Decimal(request.POST.get('total_yuan', 0) or 0)
                po.shipping_rate_thb_cbm = Decimal(request.POST.get('shipping_rate_thb_cbm', 0) or 0)
                
                bill_date_str = request.POST.get('bill_date')
                if bill_date_str:
                    po.bill_date = datetime.strptime(bill_date_str, '%Y-%m-%d').date()
                else:
                    po.bill_date = None

                # Prices
                if request.POST.get('shopee_price'):
                    po.ref_price_shopee = Decimal(request.POST.get('shopee_price', 0))
                else:
                    po.ref_price_shopee = None

                if request.POST.get('lazada_price'):
                    po.ref_price_lazada = Decimal(request.POST.get('lazada_price', 0))
                else:
                    po.ref_price_lazada = None

                if request.POST.get('tiktok_price'):
                    po.ref_price_tiktok = Decimal(request.POST.get('tiktok_price', 0))
                else:
                    po.ref_price_tiktok = None

                po.note = request.POST.get('note', '')
                po.link_shop = request.POST.get('link_shop', '')
                po.wechat_contact = request.POST.get('wechat_contact', '')
                # po.tracking_no = request.POST.get('tracking_no', '')
                
                po.save()
                po.prorate_costs() # Recalculate costs if Total Yuan Changed
                po.update_status()
                
                # Handle Files
                files = request.FILES.getlist('attachments')
                for f in files:
                    from .models import POAttachment
                    POAttachment.objects.create(header=po, file=f)
                
                messages.success(request, "✅ บันทึกข้อมูล PO เรียบร้อย")
            except Exception as e:
                messages.error(request, f"Error: {e}")
                


        elif action == 'update_items':
            # Combined Logic: Update Info AND Receive Items
            count_update = 0
            count_receive = 0
            
            receive_date_str = request.POST.get('receive_date')
            if not receive_date_str:
                receive_date = date.today()
            else:
                receive_date = receive_date_str

            for key, value in request.POST.items():
                # 1. Update Logic
                if key.startswith('qty_ordered_'):
                    try:
                        item_id = key.split('_')[2]
                        qty = int(value)
                        
                        item = POItem.objects.get(id=item_id, header=po)
                        item.qty_ordered = qty
                        item.save()
                        count_update += 1
                    except Exception as e:
                        print(f"Error updating item {key}: {e}")

            # 2. Receive Logic (Static 5 Batches)
            # Loop through Batch 1 to 5
            saved_batches = []
            
            for i in range(1, 6):
                # Check if this batch has data submitted
                # We check key presence like 'batch_{i}_bill_date' or just try to process all
                b_bill_date_str = request.POST.get(f'batch_{i}_bill_date')
                b_recv_date_str = request.POST.get(f'batch_{i}_recv_date')
                
                # Proceed even if dates empty? Maybe logic depends on Qty inputs.
                # Let's collect qtys first.
                batch_qtys = {} # { item_id: qty }
                total_qty_in_batch = 0
                
                for key, value in request.POST.items():
                    # Expected key: receive_qty_{item_id}_{batch_no}
                    if key.startswith('receive_qty_') and key.endswith(f'_{i}'):
                        try:
                            parts = key.split('_')
                            # receive_qty_123_1 -> parts: ['receive', 'qty', '123', '1']
                            item_id = parts[2]
                            qty = int(value)
                            if qty >= 0: # Allow 0 to update/clear
                                batch_qtys[item_id] = qty
                                total_qty_in_batch += qty
                        except: pass
                
                # If we have any data (dates or qtys), we process this batch
                if b_bill_date_str or b_recv_date_str or total_qty_in_batch > 0:
                    try:
                        # Get or Create Batch
                        batch, created = POReceiptBatch.objects.get_or_create(
                            header=po,
                            batch_no=i,
                            defaults={
                                'bill_date': date.today(),
                                'received_date': date.today()
                            }
                        )
                        
                        # Update Batch Headers
                        if b_bill_date_str:
                            batch.bill_date = datetime.strptime(b_bill_date_str, '%Y-%m-%d').date()
                        if b_recv_date_str:
                             batch.received_date = datetime.strptime(b_recv_date_str, '%Y-%m-%d').date()
                        
                        try:
                            batch.total_cbm = Decimal(request.POST.get(f'batch_{i}_total_cbm', 0) or 0)
                            batch.total_weight = Decimal(request.POST.get(f'batch_{i}_total_kg', 0) or 0)
                        except: pass
                        
                        batch.save()
                        saved_batches.append(i)

                        # Update Items
                        for item_id, qty in batch_qtys.items():
                            if not item_id: continue # specific fix for potentially empty IDs
                            try:
                                item_obj = POItem.objects.get(id=item_id, header=po)
                                
                                # Interpolate
                                ratio = 0
                                if total_qty_in_batch > 0:
                                    ratio = Decimal(qty) / Decimal(total_qty_in_batch)
                                
                                i_cbm = batch.total_cbm * ratio
                                i_kg = batch.total_weight * ratio
                                
                                # Update or Create Receipt
                                ReceivedPOItem.objects.update_or_create(
                                    po_item=item_obj,
                                    batch=batch,
                                    defaults={
                                        'received_qty': qty,
                                        'received_cbm': i_cbm,
                                        'received_weight': i_kg,
                                        'received_date': batch.received_date,
                                        'bill_date': batch.bill_date
                                    }
                                )
                            except Exception as e:
                                print(f"Error processing item {item_id} batch {i}: {e}")

                    except Exception as e:
                        print(f"Error processing batch {i}: {e}")

            if count_update > 0 or saved_batches:
                messages.success(request, f"✅ Updated Info & Batches: {saved_batches}")
            
            return redirect('po_detail', po_id=po.id)

        elif action == 'add_item':
            sku_code = request.POST.get('sku_code')
            if sku_code:
                try:
                    # Get SKU
                    sku = MasterItem.objects.get(product_code=sku_code.strip())
                    
                    POItem.objects.create(
                        header=po,
                        sku=sku,
                        qty_ordered=int(request.POST.get('new_qty', 1))
                    )
                    po.prorate_costs() # Trigger Proration
                    messages.success(request, f"✅ เพิ่มสินค้า {sku_code} เรียบร้อย")
                    return redirect('po_detail', po_id=po.id)
                except MasterItem.DoesNotExist:
                     messages.error(request, f"❌ ไม่พบสินค้า SKU: {sku_code}")
                except Exception as e:
                     messages.error(request, f"❌ Error: {e}")

        elif action and action.startswith('delete_item_'):
             try:
                 item_id = action.split('_')[2]
                 POItem.objects.filter(id=item_id, header=po).delete()
                 messages.success(request, "✅ ลบรายการสินค้าเรียบร้อย")
                 return redirect('po_detail', po_id=po.id)
             except Exception as e:
                 messages.error(request, f"Cannot delete item: {e}")

    # --- GET LOGIC ---
    # Prepare 5 Static Batches
    batch_range = range(1, 6)
    
    # 1. Fetch existing batches maps
    # map: batch_no -> POReceiptBatch
    batch_map = { b.batch_no: b for b in po.receipt_batches.all() }
    
    # 2. Build Columns Context
    batch_columns = []
    for i in batch_range:
        existing = batch_map.get(i)
        batch_columns.append({
            'no': i,
            'obj': existing, # Can be None
        })

    # 3. Attach row values to items
    # item.batch_qtys = { 1: 10, 2: 0, ... }
    # item.summary = { total_qty, total_cbm, total_kg }
    
    items = po.items.all()
    for item in items:
        # Fetch receipts
        receipts = item.receipts.all()
        r_map = { r.batch.batch_no: r for r in receipts if r.batch }
        
        batch_values = [] # List of objects or dicts? simpler: list of qtys
        
        for i in batch_range:
            r = r_map.get(i)
            batch_values.append({
                'qty': r.received_qty if r else 0,
                'cbm': r.received_cbm if r else 0,
                'weight': r.received_weight if r else 0
            })
        
        item.batch_values = batch_values
        
        # Recalculate Summary (just to be safe/fresh)
        item.sum_qty = sum(r.received_qty for r in receipts)
        item.sum_cbm = sum(r.received_cbm for r in receipts)
        item.sum_weight = sum(r.received_weight for r in receipts)
                
    master_items = MasterItem.objects.all().order_by('product_code')
    
    context = {
        'po': po,
        'items': items,
        'master_items': master_items,
        'batch_columns': batch_columns,
    }
    return render(request, 'inventory/po_detail.html', context)

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

@login_required
def delete_received_item_view(request, receipt_id):
    if request.method == 'POST':
        receipt = get_object_or_404(ReceivedPOItem, id=receipt_id)
        po_id = receipt.po_item.header.id
        # Delete triggers recalculation via model delete() override
        receipt.delete()
        messages.success(request, "✅ ลบประวัติการรับเรียบร้อย")
        return redirect('po_detail', po_id=po_id)
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
            
            # Function helper for Decimals
            def get_decimal(name, default=0):
                val = request.POST.get(name, '')
                try: 
                    return Decimal(val)
                except: 
                    return Decimal(default)

            exchange_rate = get_decimal('exchange_rate', 1.0)
            
            link_shop = request.POST.get('link_shop')
            wechat = request.POST.get('wechat_contact')
            shopee_price = get_decimal('shopee_price', 0)
            lazada_price = get_decimal('lazada_price', 0)
            tiktok_price = get_decimal('tiktok_price', 0)
            note = request.POST.get('note')
            
             # New Fields
            total_yuan = get_decimal('total_yuan', 0)
            shipping_rate_thb_cbm = get_decimal('shipping_rate_thb_cbm', 0)
            bill_date_str = request.POST.get('bill_date')
            bill_date = None
            if bill_date_str:
                bill_date = datetime.strptime(bill_date_str, '%Y-%m-%d').date()

            # Create PO Header
            po = POHeader.objects.create(
                po_number=po_number,
                order_date=order_date, # Now a date object
                order_type=order_type,
                shipping_type=shipping_type if order_type == 'IMPORTED' else None,
                estimated_date=estimated_date, # Now a date object or None
                exchange_rate=exchange_rate,
                total_yuan=total_yuan,
                shipping_rate_thb_cbm=shipping_rate_thb_cbm,
                bill_date=bill_date,
                link_shop=link_shop,
                wechat_contact=wechat,
                ref_price_shopee=shopee_price,
                ref_price_lazada=lazada_price,
                ref_price_tiktok=tiktok_price,
                note=note,
                status='Pending'
            )
            
            # Handle Attachment (Single file for now as per simple implementation)
            if 'attachments' in request.FILES:
                from .models import POAttachment
                for f in request.FILES.getlist('attachments'):
                    POAttachment.objects.create(header=po, file=f)

            # 2. Process Items (Dynamic Rows)
            count_items = 0
            for key in request.POST:
                if key.startswith('sku_'):
                    row_id = key.split('_')[1]
                    sku_code = request.POST.get(key)
                    
                    if not sku_code: continue
                    
                    # Verify SKU exists
                    try:
                        master_item = MasterItem.objects.get(product_code=sku_code)
                    except MasterItem.DoesNotExist:
                        messages.warning(request, f"⚠️ SKU not found: {sku_code} (Skipped)")
                        continue
                        
                    qty = int(get_decimal(f'qty_{row_id}', 1))
                    
                    # Create Item with Qty Only (Costs calc later)
                    POItem.objects.create(
                        header=po,
                        sku=master_item,
                        qty_ordered=int(qty)
                    )
                    count_items += 1
            
            # Trigger Proration
            po.prorate_costs()
            po.update_status()
            
            # Success
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'status': 'success', 'redirect_url': '/po/'})
            
            if count_items == 0:
                messages.warning(request, f"⚠️ PO {po_number} created but NO items were added (Check SKUs).")
            else:
                messages.success(request, f"✅ สร้างใบสั่งซื้อ {po_number} สำเร็จ ({count_items} รายการ)!")
            
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

@login_required
def get_po_history(request, sku):
    # Fetch all receipts for this SKU
    receipts = ReceivedPOItem.objects.filter(po_item__sku__product_code=sku).select_related('po_item', 'po_item__header', 'po_item__sku').order_by('-received_date')
    
    # Pre-calculate fields matching template expectations
    processed_receipts = []
    for r in receipts:
        # Template expects:
        # row.cost_per_piece
        # row.total_yuan
        # row.item.header.shipping_cost_baht (handled by model property?)
        
        # Calculate Cost Per Piece (Baht)
        # Unit Cost = (Price Baht + Shipping Cost for this item) / Qty Ordered
        unit_cost = r.po_item.unit_cost_thb or 0
        r.cost_per_piece = unit_cost
        
        # Total Yuan (Price Yuan * Qty Ordered)
        # Note: Template uses 'total_yuan', assuming for the whole line item or just received? 
        # Usually history shows the PO Item specs.
        r.total_yuan = (r.po_item.price_yuan or 0) * (r.po_item.qty_ordered or 0)
        
        processed_receipts.append(r)
    
    return render(request, 'inventory/partials/po_history_table.html', {'history_items': processed_receipts})

@login_required
def get_sales_history(request, sku):
    # Fetch sales for the given SKU
    sales = Sale.objects.filter(sku__product_code=sku).order_by('-date')
    
    # Platform Colors
    platform_colors = {
        'Shopee': '#EE4D2D',
        'Lazada': '#0f146d',
        'TikTok': '#000000',
    }
    
    # Status Colors (Bootstrap classes)
    status_classes = {
        'สำเร็จ': 'bg-success',
        'Completed': 'bg-success',
        'ยกเลิก': 'bg-danger',
        'Cancelled': 'bg-danger',
        'ที่ต้องจัดส่ง': 'bg-warning text-dark',
        'To Ship': 'bg-warning text-dark',
    }
    
    processed_sales = []
    total_qty = 0
    total_amount = Decimal(0)
    total_net = Decimal(0)
    
    for s in sales:
        # Calculate Total Fees (Sum of all fee fields)
        fees = (s.payment_fee or 0) + (s.commission_fee or 0) + (s.service_fee or 0) + (s.shipping_fee or 0) + (s.voucher_amount or 0)
        
        # Determine Colors
        status_cls = status_classes.get(s.status, 'bg-secondary')
        plat_color = platform_colors.get(s.platform, None)
        
        # Attach attributes for template (Python object wrapper or dict)
        # Since we are passing 'sales' to template which expects object attributes, 
        # we can attach to the model instance directly for this request.
        s.fees = fees
        s.status_class = status_cls
        s.platform_color = plat_color
        
        processed_sales.append(s)
        
        # Totals
        total_qty += s.qty
        total_amount += s.total_price
        total_net += s.net_price

    context = {
        'sales': processed_sales,
        'total_qty': total_qty,
        'total_amount': total_amount,
        'total_net': total_net,
    }
    
    return render(request, 'inventory/partials/sales_history_table.html', context)

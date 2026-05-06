# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

JST PO Management — a Django-based purchase order and inventory management system for a retail/wholesale business with multi-platform e-commerce sales (Shopee, Lazada, TikTok).

## Commands

```bash
# Start database (PostgreSQL on port 5433)
docker-compose up -d

# Run development server
python manage.py runserver

# Apply migrations
python manage.py migrate

# Run tests
python manage.py test inventory

# Import PO data from Excel
python manage.py import_po_data

# Collect static files (production)
python manage.py collectstatic
```

## Architecture

**Single-app Django project** — all models, views, and business logic live in the `inventory/` app.

### Models (`inventory/models.py`)

| Model | Purpose |
|---|---|
| `MasterItem` | Product master data (SKU, stock, prices, images) |
| `POHeader` | Purchase order header (PO number, dates, exchange rate) |
| `POItem` | PO line items (qty, prices, prorated shipping costs) |
| `POReceiptBatch` | Groups receipt operations per PO |
| `ReceivedPOItem` | Individual goods receipts (qty, CBM, weight) |
| `Sale` | Sales transactions per platform with fees |
| `JSTStockSnapshot` | Daily stock snapshots from JST external system |
| `SupplierInfo` | Vendor contacts (store links, WeChat) |
| `ImportLog` | Audit trail for Excel imports |
| `POAttachment` | File attachments on POs |

### Key Business Logic

**Stock calculation** (`utils/stock_calculator.py`): Hybrid priority — uses JST daily snapshots when available, falls back to system calculation (initial stock + received − sold).

**PO costing**: Total Yuan entered at header level → prorated across line items by value → converted to Baht via stored exchange rate. Shipping costs applied per CBM on receipt.

**PO status**: Dynamically derived from receipt qty vs. ordered qty and estimated arrival date (Pending / Arriving / Overdue / Incomplete / Complete).

### Views (`inventory/views.py` — ~1668 lines)

- **Auth**: Email-based OTP login (`login_view`, `otp_verify_view`). Allowed users are a JSON list in `.env`.
- **Pages**: `daily_sales_view`, `stock_report_view`, `po_list_view`, `po_detail_view`, `po_create_view`, `supplier_info_view`, `import_data_view`
- **Ajax endpoints**: `receive_po_item`, `get_product_detail`, `get_po_history`, `get_sales_history`, `update_min_limit`, `delete_po_view`, `delete_received_item_view`

### Utilities (`utils/`)

- `auth_utils.py` — OTP generation, email sending, Google Sheets credentials
- `importers.py` — Excel import service (master items, sales, stock snapshots) using pandas + openpyxl
- `stock_calculator.py` — Hybrid stock calculation logic

## Configuration

**Database**: PostgreSQL 15, credentials in `.env` (`DB_HOST`, `DB_PORT=5433`, `DB_NAME=jst`, `DB_USER=admin`). Docker Compose service is `postgres` (container `jst_db`).

**Settings**: `jst_system/settings.py` for development; `jst_system/settings_prod.py` for production overrides.

**Sessions**: 3-hour timeout, expires on browser close.

## Deployment

- **Web server**: Nginx (port 8001) → Gunicorn (3 workers) — see `deployment/nginx.conf` and `deployment/jst_system.service`
- **Static files**: served by Nginx from `staticfiles/`
- **Media files**: uploaded product images in `media/`

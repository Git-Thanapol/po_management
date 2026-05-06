# GEMINI.md - JST PO Management System

## Project Overview
JST PO Management is a specialized Django-based application designed for purchase order tracking and inventory management. It is tailored for a business operating across multiple e-commerce platforms including Shopee, Lazada, and TikTok. The system manages product master data, tracks the lifecycle of purchase orders from ordering to receipt, and provides insights into stock levels and sales performance.

### Core Technologies
- **Framework:** Django 6.0.1
- **Language:** Python 3.x
- **Database:** PostgreSQL 15 (running in Docker)
- **Data Processing:** Pandas, Openpyxl (for Excel imports)
- **Authentication:** Custom OTP-based email login
- **Deployment:** Nginx, Gunicorn, Systemd

## Architecture
The project follows a single-app Django architecture to keep related logic consolidated.

- **`inventory/`**: The primary application containing all models, views, and migrations.
- **`jst_system/`**: Project configuration directory containing settings (development and production), URL routing, and WSGI/ASGI entry points.
- **`utils/`**: Specialized business logic and service modules.
  - `auth_utils.py`: Handles OTP generation, email dispatch, and Google Sheets integration.
  - `importers.py`: Service for importing Master Items, Sales data, and Stock snapshots from Excel.
  - `stock_calculator.py`: Implements a hybrid stock calculation logic that prioritizes external snapshots when available.
- **`templates/`**: HTML templates organized by application.
- **`static/` & `media/`**: Directories for static assets and user-uploaded media (product images, PO attachments).

### Key Models
- `MasterItem`: Product catalog including SKUs, categories, system-calculated stock, and platform-specific pricing.
- `POHeader` & `POItem`: Purchase order tracking with multi-currency support (Yuan to Baht) and prorated costing.
- `ReceivedPOItem`: Detailed receipt tracking (quantity, CBM, weight) grouped by `POReceiptBatch`.
- `Sale`: Transactional data per platform including platform fees.
- `JSTStockSnapshot`: Daily stock records from external JST system used for reconciliation.

## Building and Running

### Prerequisites
- Python 3.10+
- Docker & Docker Compose (for PostgreSQL)

### Setup & Development
1.  **Environment Variables:** Create a `.env` file based on `.env_prod` or the requirements in `settings.py`.
2.  **Start Database:**
    ```bash
    docker-compose up -d
    ```
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Database Migrations:**
    ```bash
    python manage.py migrate
    ```
5.  **Run Development Server:**
    ```bash
    python manage.py runserver
    ```

### Testing
Run the test suite for the inventory app:
```bash
python manage.py test inventory
```

### Data Import
Import PO data or other records via management commands:
```bash
python manage.py import_po_data
```

## Development Conventions

### Business Logic
- **Stock Logic:** Stock is not a simple counter. It is a hybrid calculation found in `utils/stock_calculator.py`. Always refer to this module when modifying stock-related features.
- **Costing:** PO costs are entered in Yuan at the header level and prorated across items. Shipping costs are typically applied during the receipt phase based on volume (CBM).
- **PO Status:** The status of a PO (Pending, Arriving, Overdue, etc.) is dynamic and should be updated using the logic defined in `inventory/models.py`.

### UI/UX
- **Templates:** Uses Django templates with some dynamic components likely powered by simple JavaScript or HTMX-like patterns (refer to `inventory/views.py` Ajax endpoints).
- **Styling:** Custom CSS located in `static/`.

### Security
- **Auth:** Access is restricted to emails listed in the `.env` file. Login is via a 6-digit OTP sent to the user's email.
- **Credentials:** Never commit `.env` or sensitive keys. The `SECRET_KEY` in `settings.py` is for development only.

## Deployment
The application is deployed using Nginx as a reverse proxy to Gunicorn.
- **Service Management:** Managed via Systemd (`deployment/jst_system.service`).
- **Static Files:** Collected into `staticfiles/` using `python manage.py collectstatic`.
- **Nginx Config:** Located in `deployment/nginx.conf`.

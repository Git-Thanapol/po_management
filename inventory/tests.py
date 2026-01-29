from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from .models import MasterItem, POHeader, POItem
from datetime import date
from decimal import Decimal

class POCalculationTests(TestCase):
    def setUp(self):
        # Create Master Item
        self.sku = MasterItem.objects.create(
            product_code="TEST-SKU-001",
            name="Test Product",
            min_limit=10
        )

    def test_po_model_calculations(self):
        """
        Test that POHeader correctly calculates total CBM and transportation cost
        based on the new Yuan rate logic.
        """
        # Create Header
        header = POHeader.objects.create(
            po_number="PO-TEST-001",
            order_date=date.today(),
            order_type="IMPORTED",
            exchange_rate=Decimal("5.0"),
            shipping_rate_yuan_per_cbm=Decimal("100.00"), # 100 Yuan/CBM
            shipping_rate_kg=Decimal("10.00")
        )
        
        # Create Items
        # Item 1: 2 CBM
        POItem.objects.create(
            header=header,
            sku=self.sku,
            qty_ordered=10,
            price_yuan=Decimal("10.00"),
            cbm=Decimal("2.0"),
            weight=Decimal("10.0")
        )
        
        # Item 2: 3 CBM
        POItem.objects.create(
            header=header,
            sku=self.sku,
            qty_ordered=5,
            price_yuan=Decimal("20.00"),
            cbm=Decimal("3.0"),
            weight=Decimal("5.0")
        )
        
        # Check Total CBM => 2.0 + 3.0 = 5.0
        self.assertEqual(header.total_cbm, Decimal("5.0"))
        
        # Check Transportation Cost
        # Formula: Total CBM (5.0) * Rate Yuan (100) * Ex Rate (5.0)
        # Expected: 5.0 * 100 * 5.0 = 2500.00
        expected_cost = Decimal("5.0") * Decimal("100.00") * Decimal("5.0")
        self.assertEqual(header.transportation_cost, expected_cost)

class POCreateViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')
        
        self.sku = MasterItem.objects.create(
            product_code="SKU-VIEW-001", 
            name="View Test Item"
        )

    def test_create_po_via_ajax(self):
        """
        Test the AJAX submission flow for creating a PO.
        """
        url = reverse('po_create')
        
        data = {
            'po_number': 'PO-AJAX-001',
            'order_date': '2023-10-01',
            'order_type': 'IMPORTED',
            'shipping_type': 'CAR',
            'estimated_date': '2023-10-15',
            'exchange_rate': '5.2',
            'shipping_rate_yuan_per_cbm': '60.0',
            
            # Row 1
            'sku_1': 'SKU-VIEW-001',
            'qty_1': '10',
            'yuan_1': '15.5',
            'cbm_1': '1.5', # user inputs total CBM for line
            'kg_1': '20',
        }
        
        # Send AJAX request
        response = self.client.post(
            url, 
            data, 
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        
        # Check Response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(response.json()['redirect_url'], '/po/')
        
        # Verify DB
        po = POHeader.objects.get(po_number='PO-AJAX-001')
        self.assertEqual(po.shipping_rate_yuan_per_cbm, Decimal("60.0"))
        self.assertEqual(po.exchange_rate, Decimal("5.2"))
        
        item = po.items.first()
        self.assertEqual(item.sku.product_code, 'SKU-VIEW-001')
        self.assertEqual(item.cbm, Decimal("1.5"))
        
        # Verify Item Price Calculation
        # Price Baht = Yuan (15.5) * Ex Rate (5.2) = 80.6
        expected_baht = Decimal("15.5") * Decimal("5.2")
        self.assertAlmostEqual(item.price_baht, expected_baht, places=2)

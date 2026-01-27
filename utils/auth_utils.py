import hashlib
import random
import string
import smtplib # keeping raw smtplib or use django.core.mail? Django is better.
from datetime import date, datetime
import os
from django.core.mail import send_mail
from django.conf import settings
import json
from google.oauth2 import service_account
import gspread

# Load allowed users from env or define here
# In .env: allowed_users = ["..."]
# We will check this list during login view logic, not necessarily here, but good to have a helper.

def get_credentials():
    # Helper to get Google Sheet credentials if needed (as per original file)
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    # Try to load from env var
    service_account_json = os.getenv("GCP_SERVICE_ACCOUNT")
    if service_account_json:
        try:
            creds_dict = json.loads(service_account_json)
            # Handle newline in private key if passed as string
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            return service_account.Credentials.from_service_account_info(creds_dict, scopes=scope)
        except Exception as e:
            print(f"Error loading GCP creds from env: {e}")
    
    # Fallback to file
    if os.path.exists("credentials.json"):
        return service_account.Credentials.from_service_account_file("credentials.json", scopes=scope)
    
    return None

def create_token(email):
    salt = "jst_secret_salt" 
    raw = f"{email}{salt}{date.today()}"
    return hashlib.md5(raw.encode()).hexdigest()

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def send_otp_email(receiver_email, otp_code):
    subject = "รหัสยืนยันตัวตน (OTP) - JST Hybrid System"
    body = f"รหัสเข้าใช้งานของคุณคือ: {otp_code}\n\n(รหัสนี้ใช้สำหรับการเข้าสู่ระบบครั้งนี้เท่านั้น)"
    
    try:
        send_mail(
            subject,
            body,
            settings.EMAIL_HOST_USER,
            [receiver_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"❌ ส่งอีเมลไม่สำเร็จ: {e}")
        return False

MASTER_SHEET_ID = os.getenv("MASTER_SHEET_ID", "YOUR_SHEET_ID_HERE") # Need to confirm if this is needed

def log_login_activity(email):
    try:
        creds = get_credentials()
        if not creds:
             print("No GCP credentials found for logging.")
             return

        gc = gspread.authorize(creds)
        # Assuming MASTER_SHEET_ID is known or handled. 
        # If not provided, this will fail.
        # But we catch exception.
        
        # sh = gc.open_by_key(MASTER_SHEET_ID) 
        # Keeping logic commented or safe until MASTER_SHEET_ID is confirmed
        pass 
    except Exception as e:
        print(f"Login Log Error: {e}")

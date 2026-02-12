import os
import django
import logging

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jst_system.settings')
django.setup()

logger = logging.getLogger('utils')

print("Attempting to log a test message...")
logger.info("This is a test log message from the verification script.")
print("Log message sent.")

from django.conf import settings
log_file = settings.BASE_DIR / 'debug.log'

print(f"Checking log file at: {log_file}")
if log_file.exists():
    print(f"Log file exists. Size: {log_file.stat().st_size} bytes")
    print("Content of last 5 lines:")
    with open(log_file, 'r') as f:
        lines = f.readlines()
        for line in lines[-5:]:
            print(line.strip())
else:
    print("Log file does not exist yet.")

import os
import django
import re

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jst_system.settings')
django.setup()

from inventory.models import MasterItem
from django.conf import settings

def fix_images():
    products = MasterItem.objects.filter(image__isnull=False).exclude(image='')
    updated_count = 0
    not_found_count = 0
    
    print(f"Checking {products.count()} products with images...")

    for p in products:
        current_path = os.path.join(settings.MEDIA_ROOT, p.image.name)
        
        # Check if file exists
        if os.path.exists(current_path):
            continue
            
        print(f"Missing: {p.image.name}")
        
        # Try to find a clean version (remove _opt suffix)
        # Regex to strip _opt... before extension
        # Example: image_opt123.jpg -> image.jpg
        clean_name = re.sub(r'_opt[a-zA-Z0-9]+', '', p.image.name)
        clean_path = os.path.join(settings.MEDIA_ROOT, clean_name)
        
        if os.path.exists(clean_path):
            print(f"  -> Found clean version: {clean_name}")
            p.image.name = clean_name
            p.save()
            updated_count += 1
        else:
            print(f"  -> Clean version NOT found: {clean_name}")
            not_found_count += 1

    print("-" * 30)
    print(f"Fixed: {updated_count}")
    print(f"Still Missing: {not_found_count}")

if __name__ == "__main__":
    fix_images()

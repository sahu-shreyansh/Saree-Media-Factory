import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.getcwd())

from app.config import get_settings
from app.services.baserow import db, load_status_map

async def main():
    print("Fetching rows...")
    s = get_settings()
    await load_status_map()
    # Search for rows with status like FAILED
    rows = await db.search_rows(s.BASEROW_POSTS_TABLE_ID, limit=50)
    for row in rows:
        status_obj = row.get("Status")
        status_val = status_obj.get("value") if isinstance(status_obj, dict) else str(status_obj)
        
        if "FAILED" in status_val or "UPSCALING" in status_val:
            print(f"\n--- Row {row['id']} ({status_val}) ---")
            print(f"Error Message: {row.get('error_message')}")
            print(f"Last Error:    {row.get('last_error')}")
            print(f"Retry Count:   {row.get('retry_count')}")

if __name__ == "__main__":
    asyncio.run(main())

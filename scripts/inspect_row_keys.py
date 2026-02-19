import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.getcwd())

from app.config import get_settings
from app.services.baserow import db

async def main():
    s = get_settings()
    # No need to load status map for raw search
    print(f"Searching table {s.BASEROW_POSTS_TABLE_ID}...")
    try:
        rows = await db.search_rows(s.BASEROW_POSTS_TABLE_ID, limit=1)
        if rows:
            print("\n--- Row Keys ---")
            for k in sorted(rows[0].keys()):
                print(f"- {k}")
        else:
            print("No rows found.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())

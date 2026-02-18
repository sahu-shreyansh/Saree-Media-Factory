import sys
sys.path.append('.')
import asyncio
import time
from app.services.baserow import db
from app.config import get_settings

async def monitor():
    s = get_settings()
    print(f'Monitoring Rows 2 & 3 in Table {s.BASEROW_POSTS_TABLE_ID}...')
    while True:
        try:
            r2 = await db.get_row(s.BASEROW_POSTS_TABLE_ID, 2)
        except Exception:
            r2 = {}
        try:
            r3 = await db.get_row(s.BASEROW_POSTS_TABLE_ID, 3)
        except Exception:
            r3 = {}
            
            s2 = r2.get('Status', {}).get('value', 'Unknown')
            s3 = r3.get('Status', {}).get('value', 'Unknown')
            
            print(f'[{time.strftime("%H:%M:%S")}] Row 2: {s2} | Row 3: {s3}')
            
            if s2 == 'PUBLISHED' and s3 == 'PUBLISHED':
                print('✅ Both rows complete!')
                break
                
            if 'FAILED' in s2:
                 print(f'❌ Row 2 FAILED: {s2}')
                 break
                 
        except Exception as e:
            print(f'Error polling: {e}')
            
        time.sleep(30)

if __name__ == '__main__':
    asyncio.run(monitor())

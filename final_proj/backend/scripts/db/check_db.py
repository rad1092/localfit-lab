import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import DATABASE_PATH


conn = sqlite3.connect(DATABASE_PATH)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

print(f"=== 상권분석 DB ({DATABASE_PATH}) 적재 데이터 현황 ===\n")

for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT count(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"[{table_name}] 테이블: 총 {count:,} 건")
    
    if count > 0:
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = [col[1] for col in cursor.fetchall()]
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
        sample = cursor.fetchone()
        print(f"  컬럼: {cols}")
        print(f"  샘플 데이터: {sample}")
    print("-" * 50)
    
conn.close()

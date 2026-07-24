import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import DATABASE_PATH


conn = sqlite3.connect(DATABASE_PATH)
cursor = conn.cursor()
cursor.execute("SELECT DISTINCT industry_code, industry_name FROM district_sales WHERE industry_code LIKE 'CS%'")
rows = cursor.fetchall()
for r in rows:
    if r[0] == 'CS100005':
        print(r)

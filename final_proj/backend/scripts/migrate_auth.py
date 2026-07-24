import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import DATABASE_PATH


db_path = DATABASE_PATH

def migrate():
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tables_to_update = ["favorite_area", "saved_report", "chatbot_history"]
    
    for table in tables_to_update:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)")
            print(f"Added user_id to {table}")
        except sqlite3.OperationalError as e:
            print(f"Skipped {table}: {e}")
            
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import engine, Base
from sqlalchemy.orm import sessionmaker
from scripts.seed_data import seed_sales_from_api

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def main():
    print("Starting Sales Data Seed...")
    db = SessionLocal()
    try:
        seed_sales_from_api(db)
        print("Sales data seed completed successfully.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()

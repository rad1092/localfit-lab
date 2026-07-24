import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import engine
from app.models.commercial_area import Store, Sales
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def seed_estimated_sales():
    print("Starting Estimated Sales Data Seed...")
    db = SessionLocal()
    try:
        # Clear existing sales
        db.query(Sales).delete()
        
        # Aggregate store counts per area
        area_store_counts = db.query(
            Store.area_code, 
            func.sum(Store.store_count).label("total_stores")
        ).group_by(Store.area_code).all()
        
        sales_to_insert = []
        for area in area_store_counts:
            # Estimate sales: 15,000,000 KRW per store per quarter
            total_sales = area.total_stores * 15000000.0
            
            # Create a summary sales record for the area
            sale = Sales(
                area_code=area.area_code,
                quarter="20241",
                industry_code="ALL",
                total_sales=total_sales,
                sales_count=area.total_stores * 150 # estimated transactions
            )
            sales_to_insert.append(sale)
            
        db.add_all(sales_to_insert)
        db.commit()
        print(f"Successfully generated and inserted {len(sales_to_insert)} estimated sales records.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_estimated_sales()

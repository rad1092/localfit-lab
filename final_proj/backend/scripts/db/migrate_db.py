import sys
import sqlite3
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.settings import DATABASE_PATH
from app.database import engine, Base
from sqlalchemy.orm import sessionmaker

def migrate_db():
    print("Creating new tables...")
    # Import all models so they are registered with Base
    from app.models.commercial_area import (
        CommercialArea, DistrictPopulation, DistrictFloating,
        DistrictSales, DistrictStoreCount, DistrictGrowthHistory,
        AreaSalePriceProxy, AreaRoneCostReference
    )
    Base.metadata.create_all(bind=engine)
    print("Tables created.")

    print("Migrating data from old tables to new tables...")
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        # Migrate Store -> DistrictStoreCount
        print("Migrating store -> district_store_count")
        try:
            cursor.execute("SELECT area_code, industry_code, store_count, quarter FROM store")
            stores = cursor.fetchall()
            db.query(DistrictStoreCount).delete()
            for s in stores:
                db.add(DistrictStoreCount(
                    area_code=s['area_code'],
                    industry_code=s['industry_code'],
                    store_count=s['store_count'],
                    timestamp=s['quarter']
                ))
            db.commit()
            print(f"Migrated {len(stores)} store records.")
        except Exception as e:
            print("Error migrating stores:", e)

        # Migrate Population -> DistrictPopulation and DistrictFloating
        print("Migrating population -> district_population & district_floating")
        try:
            cursor.execute("SELECT area_code, quarter, residential_population, floating_population, worker_population FROM population")
            pops = cursor.fetchall()
            db.query(DistrictPopulation).delete()
            db.query(DistrictFloating).delete()
            
            for p in pops:
                db.add(DistrictPopulation(
                    area_code=p['area_code'],
                    resident_population=p['residential_population'],
                    worker_population=p['worker_population'],
                    timestamp=p['quarter']
                ))
                if p['floating_population'] > 0:
                    db.add(DistrictFloating(
                        area_code=p['area_code'],
                        floating_population=p['floating_population'],
                        timestamp=p['quarter']
                    ))
            db.commit()
            print(f"Migrated {len(pops)} population records.")
        except Exception as e:
            print("Error migrating populations:", e)

        # Migrate Sales -> DistrictSales
        print("Migrating sales -> district_sales")
        try:
            # We need industry_name for new table, old table didn't have it, but store table did
            cursor.execute("SELECT area_code, quarter, industry_code, total_sales FROM sales")
            sales = cursor.fetchall()
            
            # get mapping from store
            cursor.execute("SELECT industry_code, industry_name FROM store GROUP BY industry_code")
            mapping = {r['industry_code']: r['industry_name'] for r in cursor.fetchall()}

            db.query(DistrictSales).delete()
            for s in sales:
                db.add(DistrictSales(
                    area_code=s['area_code'],
                    industry_code=s['industry_code'],
                    industry_name=mapping.get(s['industry_code'], s['industry_code']),
                    sales_amount=s['total_sales'],
                    timestamp=s['quarter']
                ))
            db.commit()
            print(f"Migrated {len(sales)} sales records.")
        except Exception as e:
            print("Error migrating sales:", e)

        # Legacy real_estate.average_rent has no provider/unit/grain lineage. It must not be
        # re-labelled as either RTMS sale-price proxy or R-ONE rent. Canonical product seeding
        # populates the two explicit cost tables from their validated Gold bridges.
        print("Skipping legacy real_estate cost migration: canonical Gold cost lineage required")

        # Generate DistrictGrowthHistory (requires joining sales, population, store)
        print("Generating district_growth_history...")
        try:
            db.query(DistrictGrowthHistory).delete()
            # This is complex to join in python, let's just use SQL
            cursor.execute("""
                SELECT s.area_code, s.quarter, 
                       SUM(s.total_sales) as total_sales,
                       MAX(p.floating_population) as floating_population,
                       SUM(st.store_count) as store_count
                FROM sales s
                LEFT JOIN population p ON s.area_code = p.area_code AND s.quarter = p.quarter
                LEFT JOIN store st ON s.area_code = st.area_code AND s.quarter = st.quarter
                GROUP BY s.area_code, s.quarter
            """)
            histories = cursor.fetchall()
            for h in histories:
                db.add(DistrictGrowthHistory(
                    area_code=h['area_code'],
                    sales_amount=h['total_sales'] or 0.0,
                    floating_population=h['floating_population'] or 0,
                    store_count=h['store_count'] or 0,
                    timestamp=h['quarter']
                ))
            db.commit()
            print(f"Generated {len(histories)} growth history records.")
        except Exception as e:
            print("Error generating growth histories:", e)

    finally:
        db.close()
        conn.close()

if __name__ == '__main__':
    migrate_db()

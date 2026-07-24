import os
import sys
import requests
from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.database import engine, Base
from app.models.commercial_area import Store, Population, Sales, CommercialArea

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
SEOUL_API_KEY = os.getenv("SEOUL_DATA_API_KEY", "sample")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_latest_quarter(service_name):
    for y in [2026, 2025, 2024, 2023]:
        for q in [4, 3, 2, 1]:
            quarter = f"{y}{q}"
            url = f"http://openapi.seoul.go.kr:8088/{SEOUL_API_KEY}/json/{service_name}/1/1/{quarter}"
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                if service_name in data and data[service_name].get('list_total_count', 0) > 0:
                    return quarter
    return None

def fetch_all_openapi_data(service_name):
    latest_quarter = get_latest_quarter(service_name)
    if not latest_quarter:
        print(f"Could not find any data for {service_name}")
        return []
        
    print(f"Fetching latest quarter ({latest_quarter}) data for {service_name}...")
    all_rows = []
    start_idx = 1
    page_size = 1000
    
    while True:
        end_idx = start_idx + page_size - 1
        url = f"http://openapi.seoul.go.kr:8088/{SEOUL_API_KEY}/json/{service_name}/{start_idx}/{end_idx}/{latest_quarter}"
        response = requests.get(url)
        
        if response.status_code == 200:
            data = response.json()
            if service_name in data and 'row' in data[service_name]:
                rows = data[service_name]['row']
                all_rows.extend(rows)
                print(f"  Fetched {start_idx} to {start_idx + len(rows) - 1}...")
                
                if len(rows) < page_size:
                    break
                start_idx += page_size
            else:
                break
        else:
            print(f"  HTTP Error {response.status_code} at index {start_idx}")
            break
            
    print(f"Total {len(all_rows)} records fetched for {service_name} (Quarter {latest_quarter}).")
    return all_rows

def seed_full_stores(db):
    print("\n--- Seeding Full Stores ---")
    rows = fetch_all_openapi_data('VwsmTrdarStorQq')
    
    db.query(Store).delete()
    db.commit()
    
    stores = []
    for r in rows:
        store = Store(
            area_code=str(r.get('TRDAR_CD', '')),
            quarter=str(r.get('STDR_YYQU_CD', '')),
            industry_code=str(r.get('SVC_INDUTY_CD', '')),
            industry_name=str(r.get('SVC_INDUTY_CD_NM', '')),
            store_count=int(float(r.get('STOR_CO', 0))),
            franchise_count=int(float(r.get('FRC_STOR_CO', 0))),
            open_count=int(float(r.get('OPBIZ_STOR_CO', 0))),
            close_count=int(float(r.get('CLSBIZ_STOR_CO', 0)))
        )
        stores.append(store)
    
    chunk_size = 5000
    for i in range(0, len(stores), chunk_size):
        db.bulk_save_objects(stores[i:i+chunk_size])
    db.commit()
    print(f"Successfully inserted {len(stores)} store records.")

def seed_full_sales(db):
    print("\n--- Seeding Full Sales ---")
    rows = fetch_all_openapi_data('VwsmTrdarSelngQq')
    
    db.query(Sales).delete()
    db.commit()
    
    sales_list = []
    for r in rows:
        sale = Sales(
            area_code=str(r.get('TRDAR_CD', '')),
            quarter=str(r.get('STDR_YYQU_CD', '')),
            industry_code=str(r.get('SVC_INDUTY_CD', '')),
            total_sales=float(r.get('THSMON_SELNG_AMT', 0)),
            sales_count=int(float(r.get('THSMON_SELNG_CO', 0)))
        )
        sales_list.append(sale)
        
    chunk_size = 5000
    for i in range(0, len(sales_list), chunk_size):
        db.bulk_save_objects(sales_list[i:i+chunk_size])
    db.commit()
    print(f"Successfully inserted {len(sales_list)} sales records.")

def seed_full_population(db):
    print("\n--- Seeding Full Population ---")
    repop_rows = fetch_all_openapi_data('VwsmTrdarRepopQq')
    flpop_rows = fetch_all_openapi_data('VwsmTrdarFlpopQq')
    
    db.query(Population).delete()
    db.commit()
    
    pop_map = {}
    
    for r in repop_rows:
        key = (str(r.get('TRDAR_CD', '')), str(r.get('STDR_YYQU_CD', '')))
        if key not in pop_map:
            pop_map[key] = {'residential': 0, 'floating': 0}
        pop_map[key]['residential'] = int(float(r.get('TOT_REPOP_CO', 0)))
        
    for r in flpop_rows:
        # For floating pop, use its own quarter, but join by area_code
        # To merge cleanly for frontend, we can use the area_code as the primary link
        area_code = str(r.get('TRDAR_CD', ''))
        quarter = str(r.get('STDR_YYQU_CD', ''))
        key = (area_code, quarter)
        if key not in pop_map:
            pop_map[key] = {'residential': 0, 'floating': 0}
        pop_map[key]['floating'] = int(float(r.get('TOT_FLPOP_CO', 0)))
        
    populations = []
    for (area_code, quarter), data in pop_map.items():
        pop = Population(
            area_code=area_code,
            quarter=quarter,
            residential_population=data['residential'],
            floating_population=data['floating'],
            worker_population=0
        )
        populations.append(pop)
        
    chunk_size = 5000
    for i in range(0, len(populations), chunk_size):
        db.bulk_save_objects(populations[i:i+chunk_size])
    db.commit()
    print(f"Successfully inserted {len(populations)} population records.")

def main():
    print("Starting full OpenAPI Seeding Process...")
    db = SessionLocal()
    try:
        Base.metadata.create_all(bind=engine)
        
        seed_full_stores(db)
        seed_full_sales(db)
        seed_full_population(db)
        
        print("\n--- Verification Check (강남역 - 3110955 / 3110956 / 3120150 등) ---")
        gangnam = db.query(CommercialArea).filter(CommercialArea.area_name.like('%강남역%')).first()
        if gangnam:
            print(f"Found area: {gangnam.area_name} ({gangnam.area_code})")
            store_count = db.query(Store).filter(Store.area_code == gangnam.area_code).count()
            sales_count = db.query(Sales).filter(Sales.area_code == gangnam.area_code).count()
            pop_count = db.query(Population).filter(Population.area_code == gangnam.area_code).count()
            print(f"  Stores: {store_count}")
            print(f"  Sales: {sales_count}")
            print(f"  Population: {pop_count}")
        else:
            print("Gangnam station area not found.")
            
    except Exception as e:
        print(f"Error during seeding: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()

import os
import sys
import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker

# Add the parent directory to the path so we can import from app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import engine, Base
from app.models.commercial_area import CommercialArea, Store, Population, RealEstate, Sales

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
SEOUL_API_KEY = os.getenv("SEOUL_DATA_API_KEY", "sample")
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data'))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def seed_commercial_areas(db):
    print("Seeding commercial areas...")
    csv_path = os.path.join(DATA_DIR, '서울시_상권분석서비스_영역상권_통합.csv')
    try:
        df = pd.read_csv(csv_path, encoding='cp949')
    except Exception:
        df = pd.read_csv(csv_path, encoding='utf-8')
    
    df_unique = df.drop_duplicates(subset=['TRDAR_CD'])
    
    areas_to_insert = []
    for _, row in df_unique.iterrows():
        area = CommercialArea(
            area_code=str(row['TRDAR_CD']),
            area_name=str(row['TRDAR_CD_N']),
            district_code=str(row['SIGNGU_CD']) if 'SIGNGU_CD' in row else None
        )
        areas_to_insert.append(area)
        
    db.query(CommercialArea).delete()
    db.add_all(areas_to_insert)
    db.commit()
    print(f"Inserted {len(areas_to_insert)} commercial areas.")

def seed_stores_from_csv(db):
    print("Seeding stores from local CSV...")
    csv_path = os.path.join(DATA_DIR, 'clean_store_data.csv')
    
    try:
        df = pd.read_csv(csv_path, encoding='cp949', low_memory=False)
    except Exception:
        df = pd.read_csv(csv_path, encoding='utf-8', low_memory=False)
        
    expected_cols = ["기준_년분기_코드", "상권_구분_코드", "상권_구분_코드_명", "상권_코드", "상권_코드_명", "서비스_업종_코드", "서비스_업종_코드_명", "점포_수", "유사_업종_점포_수", "개업_율", "개업_점포_수", "폐업_률", "폐업_점포_수", "프랜차이즈_점포_수", "분기"]
    if len(df.columns) == len(expected_cols):
        df.columns = expected_cols

    df_store = pd.DataFrame()
    df_store['area_code'] = df['상권_코드'].astype(str)
    df_store['quarter'] = df['기준_년분기_코드'].astype(str)
    df_store['industry_code'] = df['서비스_업종_코드'].astype(str)
    df_store['industry_name'] = df['서비스_업종_코드_명']
    df_store['store_count'] = pd.to_numeric(df['점포_수'], errors='coerce').fillna(0)
    df_store['franchise_count'] = pd.to_numeric(df['프랜차이즈_점포_수'], errors='coerce').fillna(0)
    df_store['open_count'] = pd.to_numeric(df['개업_점포_수'], errors='coerce').fillna(0)
    df_store['close_count'] = pd.to_numeric(df['폐업_점포_수'], errors='coerce').fillna(0)
    
    # We will only insert a subset for demonstration speed (e.g., 100,000 rows max)
    # Alternatively we insert all
    print("Writing store data to SQLite... this might take a minute.")
    db.query(Store).delete()
    db.commit()
    df_store.to_sql('store', con=engine, if_exists='append', index=False)
    print(f"Inserted {len(df_store)} store records.")

def fetch_openapi_data(service_name, start_idx, end_idx):
    url = f"http://openapi.seoul.go.kr:8088/{SEOUL_API_KEY}/json/{service_name}/{start_idx}/{end_idx}/"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if service_name in data:
            return data[service_name]['row']
    return []

def seed_population_from_api(db):
    print("Seeding population data from OpenAPI (VwsmTrdarRepopQq)...")
    # Fetch 1000 records
    rows = fetch_openapi_data('VwsmTrdarRepopQq', 1, 1000)
    
    db.query(Population).delete()
    populations = []
    for r in rows:
        pop = Population(
            area_code=str(r.get('TRDAR_CD')),
            quarter=str(r.get('STDR_YYQU_CD', '')),
            residential_population=int(r.get('TOT_REPOP_CO', 0)),
            floating_population=0,
            worker_population=0
        )
        populations.append(pop)
    db.add_all(populations)
    db.commit()
    print(f"Inserted {len(populations)} population records.")

def seed_sales_from_api(db):
    print("Seeding sales data from OpenAPI (VwsmTrdarSelngQq)...")
    # Fetch 1000 records
    rows = fetch_openapi_data('VwsmTrdarSelngQq', 1, 1000)
    
    db.query(Sales).delete()
    sales_list = []
    for r in rows:
        sale = Sales(
            area_code=str(r.get('TRDAR_CD')),
            quarter=str(r.get('STDR_YYQU_CD', '')),
            industry_code=str(r.get('SVC_INDUTY_CD', '')),
            total_sales=float(r.get('THSMON_SELNG_AMT', 0)),
            sales_count=int(r.get('THSMON_SELNG_CO', 0))
        )
        sales_list.append(sale)
    db.add_all(sales_list)
    db.commit()
    print(f"Inserted {len(sales_list)} sales records.")

def seed_real_estate_from_csv(db):
    print("Seeding real estate data from local CSV...")
    csv_path = os.path.join(DATA_DIR, '서울시_공공데이터_매장용빌딩_임대료_공실률_및_수익률.csv')
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        return
        
    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig', header=1)
    except Exception:
        df = pd.read_csv(csv_path, encoding='cp949', header=1)
        
    # The CSV structure: 지역별(1), 지역별(2), 임대료 (천원/㎡), 공실률 (%), ...
    df = df.dropna(subset=['지역별(2)', '임대료 (천원/㎡)'])
    
    db.query(RealEstate).delete()
    real_estates = []
    
    # We will try to match `지역별(2)` to `commercial_area.area_name`
    all_areas = db.query(CommercialArea).all()
    
    for _, row in df.iterrows():
        region = str(row['지역별(2)']).strip()
        if region == '소계':
            continue
            
        rent_val = pd.to_numeric(row['임대료 (천원/㎡)'], errors='coerce')
        vacancy_val = pd.to_numeric(row['공실률 (%)'], errors='coerce')
        
        if pd.isna(rent_val): rent_val = 0.0
        if pd.isna(vacancy_val): vacancy_val = 0.0
        
        # Simple matching: find areas where area_name contains the region
        matched_areas = [a for a in all_areas if region in a.area_name]
        for matched in matched_areas:
            re = RealEstate(
                area_code=matched.area_code,
                quarter="20251",
                average_rent=float(rent_val * 1000), # convert to won
                average_deposit=0.0,
                vacancy_rate=float(vacancy_val)
            )
            real_estates.append(re)
            
    db.add_all(real_estates)
    db.commit()
    print(f"Inserted {len(real_estates)} real estate records mapped to areas.")

def main():
    print("Starting Data ETL process...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_commercial_areas(db)
        seed_stores_from_csv(db)
        seed_population_from_api(db)
        seed_sales_from_api(db)
        seed_real_estate_from_csv(db)
        print("ETL process completed successfully.")
    except Exception as e:
        print(f"Error during ETL: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()

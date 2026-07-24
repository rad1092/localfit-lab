import os

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.security import SECRET_KEY, ALGORITHM
from app.models.commercial_area import User
from app.repositories.commercial_area import CommercialAreaRepository
from app.services.commercial_area import CommercialAreaService, DashboardService
from app.services.comparison_report import ComparisonReportService
from app.services.single_report import SingleReportService

def get_commercial_area_repository(db: Session = Depends(get_db)) -> CommercialAreaRepository:
    return CommercialAreaRepository(db)

def get_commercial_area_service(repo: CommercialAreaRepository = Depends(get_commercial_area_repository)) -> CommercialAreaService:
    return CommercialAreaService(repo)

def get_dashboard_service(repo: CommercialAreaRepository = Depends(get_commercial_area_repository)) -> DashboardService:
    return DashboardService(repo)

def get_recommendation_service(repo: CommercialAreaRepository = Depends(get_commercial_area_repository)) -> SingleReportService:
    return SingleReportService(repo)

def get_comparison_service(repo: CommercialAreaRepository = Depends(get_commercial_area_repository)) -> ComparisonReportService:
    return ComparisonReportService(repo)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("email")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    canonical_email = str(email).strip().casefold()
    user = db.query(User).filter(func.lower(func.trim(User.email)) == canonical_email).first()
    if user is None:
        raise credentials_exception
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if not bool(current_user.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user

optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

def get_optional_user(token: str = Depends(optional_oauth2_scheme), db: Session = Depends(get_db)):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("email")
        if email is None:
            return None
    except JWTError:
        return None
        
    canonical_email = str(email).strip().casefold()
    user = db.query(User).filter(func.lower(func.trim(User.email)) == canonical_email).first()
    return user


def require_environment_admin(current_user: User | None = Depends(get_optional_user)) -> User | None:
    """Keep local admin tools open while enforcing account authorization in production."""
    environment = os.getenv("LOCALFIT_ENV", "development").strip().casefold()
    if environment not in {"prod", "production"}:
        return current_user
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator login required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not bool(current_user.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user

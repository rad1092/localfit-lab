from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from fastapi.security import OAuth2PasswordRequestForm
from app.database import get_db
from app.models.commercial_area import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate, Token
from app.core.security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from app.dependencies import get_current_user
from app.runtime_schema import development_admin_bootstrap_enabled, is_configured_admin_email

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse)
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    canonical_email = str(user.email).strip().casefold()
    db_user = db.query(User).filter(func.lower(func.trim(User.email)) == canonical_email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    if is_configured_admin_email(canonical_email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Configured administrator emails must be provisioned before they are promoted",
        )
        
    hashed_password = get_password_hash(user.password)
    first_development_admin = (
        development_admin_bootstrap_enabled()
        and db.query(User.id).limit(1).first() is None
    )
    new_user = User(
        email=canonical_email,
        password_hash=hashed_password,
        nickname=user.nickname,
        created_at=datetime.now().isoformat(),
        is_admin=first_development_admin,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.post("/login", response_model=Token)
def login_for_access_token(db: Session = Depends(get_db), form_data: OAuth2PasswordRequestForm = Depends()):
    canonical_email = str(form_data.username).strip().casefold()
    user = db.query(User).filter(func.lower(func.trim(User.email)) == canonical_email).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"email": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_users_me(
    update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if update.nickname is None and update.new_password is None:
        raise HTTPException(status_code=400, detail="No account changes requested")

    if update.new_password is not None:
        if not update.current_password or not verify_password(
            update.current_password,
            current_user.password_hash,
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        current_user.password_hash = get_password_hash(update.new_password)

    if update.nickname is not None:
        current_user.nickname = update.nickname

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user

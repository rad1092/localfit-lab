import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import bcrypt

from app.core.settings import RUNTIME_ROOT


def _load_secret_key() -> str:
    configured = os.getenv("SECRET_KEY", "").strip()
    if configured:
        return configured

    secret_path = RUNTIME_ROOT / "auth" / "jwt_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        persisted = secret_path.read_text(encoding="utf-8").strip()
        if len(persisted) >= 32:
            return persisted
        raise RuntimeError(f"JWT signing key is invalid: {secret_path}")

    generated = secrets.token_urlsafe(48)
    try:
        with secret_path.open("x", encoding="utf-8") as secret_file:
            secret_file.write(generated)
    except FileExistsError:
        persisted = secret_path.read_text(encoding="utf-8").strip()
        if len(persisted) < 32:
            raise RuntimeError(f"JWT signing key is invalid: {secret_path}")
        return persisted
    return generated


SECRET_KEY = _load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 1 week

def verify_password(plain_password: str, hashed_password: str):
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode('utf-8')
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password)

def get_password_hash(password: str):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

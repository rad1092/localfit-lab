from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    nickname: str

class UserResponse(BaseModel):
    id: int
    email: str
    nickname: str
    created_at: str
    is_admin: bool = False

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    current_password: Optional[str] = None
    new_password: Optional[str] = Field(default=None, min_length=8)

    @field_validator("nickname", mode="before")
    @classmethod
    def normalize_nickname(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("nickname must not be blank")
        return normalized

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

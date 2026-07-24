"""Create or promote the first administrator from the local backend shell.

This command is deliberately not exposed as an HTTP endpoint.  It provides a
production-safe bootstrap path when public registration must not be allowed to
claim an address reserved by ``LOCALFIT_ADMIN_EMAIL(S)``.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.security import get_password_hash  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models.commercial_area import User  # noqa: E402
from app.runtime_schema import ensure_runtime_schema  # noqa: E402


class PasswordRequired(ValueError):
    pass


def _canonical_email(value: str) -> str:
    email = value.strip().casefold()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("유효한 관리자 이메일을 입력하세요.")
    return email


def provision_admin(
    email: str,
    *,
    password: str | None = None,
    nickname: str | None = None,
) -> tuple[User, bool]:
    """Promote an existing account, or create one when a password is supplied."""
    canonical = _canonical_email(email)
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()

    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .filter(func.lower(func.trim(User.email)) == canonical)
            .first()
        )
        created = user is None
        if user is None:
            if password is None:
                raise PasswordRequired("새 관리자 계정에는 비밀번호가 필요합니다.")
            if len(password) < 8:
                raise ValueError("비밀번호는 8자 이상이어야 합니다.")
            user = User(
                email=canonical,
                password_hash=get_password_hash(password),
                nickname=(nickname or canonical.split("@", 1)[0]).strip()[:50],
                created_at=datetime.now().isoformat(),
                is_admin=1,
            )
            db.add(user)
        else:
            user.email = canonical
            user.is_admin = 1
            if nickname and nickname.strip():
                user.nickname = nickname.strip()[:50]

        db.commit()
        db.refresh(user)
        return user, created
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="로컬 셸에서 관리자 계정을 생성하거나 기존 계정을 승격합니다."
    )
    parser.add_argument("--email", help="관리자 이메일(생략 시 안전하게 입력)")
    parser.add_argument("--nickname", help="새 계정 표시 이름")
    args = parser.parse_args()

    email = args.email or input("관리자 이메일: ").strip()
    try:
        user, created = provision_admin(email, nickname=args.nickname)
    except PasswordRequired:
        first = getpass.getpass("새 관리자 비밀번호(8자 이상): ")
        second = getpass.getpass("비밀번호 확인: ")
        if first != second:
            parser.error("비밀번호가 일치하지 않습니다.")
        user, created = provision_admin(
            email,
            password=first,
            nickname=args.nickname,
        )
    except ValueError as exc:
        parser.error(str(exc))

    action = "생성" if created else "승격"
    print(f"관리자 계정 {action} 완료: {user.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Команды обслуживания, которые дёргает install.sh.

    python -m app.cli admin-exists    # код 0, если администратор заведён
    python -m app.cli create-admin    # логин и пароль берутся из окружения
"""

import asyncio
import os
import sys

from sqlalchemy import func, select

from app.core.security import hash_password
from shared.db.models import Admin
from app.db.session import SessionLocal, engine


async def _admin_exists() -> bool:
    async with SessionLocal() as db:
        count = await db.scalar(select(func.count()).select_from(Admin))
        return bool(count)


async def _create_admin(login: str, password: str) -> None:
    async with SessionLocal() as db:
        if await db.scalar(select(Admin).where(Admin.login == login)):
            print(f"Администратор «{login}» уже существует", file=sys.stderr)
            raise SystemExit(1)
        db.add(
            Admin(
                login=login,
                password_hash=hash_password(password),
                is_owner=True,
            )
        )
        await db.commit()


async def _main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    try:
        if command == "admin-exists":
            return 0 if await _admin_exists() else 1

        if command == "create-admin":
            # Через окружение, а не аргументы: аргументы видны в `ps`.
            login = os.environ.get("ADMIN_LOGIN", "").strip()
            password = os.environ.get("ADMIN_PASSWORD", "")
            if not login or len(password) < 12:
                print(
                    "нужны ADMIN_LOGIN и ADMIN_PASSWORD (от 12 символов)",
                    file=sys.stderr,
                )
                return 1
            await _create_admin(login, password)
            print(f"Администратор «{login}» создан")
            return 0

        print(__doc__, file=sys.stderr)
        return 2
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv)))

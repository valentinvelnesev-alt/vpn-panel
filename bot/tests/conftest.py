"""Окружение для тестов бота (см. backend/tests/conftest.py)."""

import os
import tempfile

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{os.path.join(tempfile.mkdtemp(prefix='vpn-bot-tests-'), 'test.db')}",
)
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _reset_reconcile_throttle():
    """Сверка с 3x-ui раз в минуту на клиента — между тестами id совпадают."""
    from app.services import subscriptions

    subscriptions._last_reconcile.clear()
    yield
    subscriptions._last_reconcile.clear()

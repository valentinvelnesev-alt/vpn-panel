"""Окружение для тестов: без него pytest падал на импорте настроек, и
переменные приходилось экспортировать руками. Заданные снаружи значения
не перетираются."""

import os
import tempfile

from cryptography.fernet import Fernet

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{os.path.join(tempfile.mkdtemp(prefix='vpn-panel-tests-'), 'test.db')}",
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
# Режим ip: cookie без Secure, иначе TestClient по http их не отправляет.
os.environ.setdefault("DEPLOY_MODE", "ip")
os.environ.setdefault("BACKUP_DIR", tempfile.mkdtemp(prefix="vpn-panel-backups-"))
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="vpn-panel-uploads-"))

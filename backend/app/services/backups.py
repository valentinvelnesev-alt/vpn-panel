"""Резервные копии панели: создание, хранение, восстановление и расписание.

Копия — архив .tar.gz, в котором всё, что нужно, чтобы поднять панель на
новом сервере как было:
- database.sql — `pg_dump --clean --if-exists`: клиенты, балансы, тарифы,
  кнопки и их цвета, тексты, платежи, настройки. Такой дамп сам удаляет и
  пересоздаёт таблицы, поэтому накатывается поверх работающей базы;
- uploads/ — картинка меню бота, фото рассылок, фон панели (в базе лежат
  только имена файлов);
- meta.json — версия формата и ENCRYPTION_KEY сервера. Токен бота, ключи
  платёжек и прокси зашифрованы этим ключом; на новом сервере он другой, и
  без старого ключа секреты из копии было бы не расшифровать. При
  восстановлении они перешифровываются ключом нового сервера.

Из-за ключа файл копии секретен так же, как .env, — об этом говорит панель.
Старые копии .sql.gz (только база) по-прежнему восстанавливаются.

pg_dump/psql запускаются без shell, пароль БД передаётся переменной
окружения: раньше вся строка подключения с паролем подставлялась в
shell-команду (и ломалась на кавычке в пароле).
"""

import asyncio
import gzip
import io
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import IO
from urllib.parse import unquote, urlsplit, urlunsplit

from app.core.config import settings

log = logging.getLogger("backups")

NAME_RE = re.compile(r"^backup_\d{8}_\d{6}(_[a-z_]+)?\.(sql|tar)\.gz$")
CHUNK = 64 * 1024
FORMAT_VERSION = 2
DUMP_MEMBER = "database.sql"
META_MEMBER = "meta.json"
UPLOADS_PREFIX = "uploads/"
# Имена файлов, которые панель сама кладёт в uploads, — без путей.
_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")

# Настройки расписания (таблица settings).
AUTO_ENABLED = "backup_auto_enabled"
INTERVAL_HOURS = "backup_interval_hours"
KEEP = "backup_keep"
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_KEEP = 7
SCHEDULER_TICK = 300  # секунд
# Первая проверка — не сразу: при старте контейнера миграции и бот и так
# нагружают базу, а копия подождёт минуту.
SCHEDULER_START_DELAY = 60

# Одна операция за раз: копия во время восстановления (или два
# восстановления подряд) дали бы мусор.
_lock = asyncio.Lock()


class BackupError(Exception):
    """Сообщение показывается администратору как есть."""


@dataclass(frozen=True, slots=True)
class BackupFile:
    name: str
    size_bytes: int
    created_at: datetime

    @property
    def full(self) -> bool:
        """Новый формат: база + картинки + ключ шифрования."""
        return self.name.endswith(".tar.gz")


@dataclass(frozen=True, slots=True)
class RestoreResult:
    safety: BackupFile
    uploads: int
    reencrypted: int


def busy() -> bool:
    return _lock.locked()


def _directory() -> str:
    return settings.backup_dir


def path_of(name: str) -> str:
    """Путь к копии по имени. Имя проверяется — иначе «../» в запросе
    давал бы читать и удалять произвольные файлы."""
    if not NAME_RE.match(name):
        raise BackupError("Некорректное имя резервной копии")
    return os.path.join(_directory(), name)


def list_files() -> list[BackupFile]:
    directory = _directory()
    if not os.path.isdir(directory):
        return []
    files = []
    for name in os.listdir(directory):
        if not NAME_RE.match(name):
            continue
        stat = os.stat(os.path.join(directory, name))
        files.append(
            BackupFile(
                name=name,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        )
    return sorted(files, key=lambda f: (f.created_at, f.name), reverse=True)


def _get(name: str) -> BackupFile:
    return next(f for f in list_files() if f.name == name)


def new_name(suffix: str = "", ext: str = "tar") -> str:
    tail = f"_{suffix}" if suffix else ""
    return f"backup_{datetime.now(UTC):%Y%m%d_%H%M%S}{tail}.{ext}.gz"


def _pg_target() -> tuple[str, dict[str, str]]:
    """libpq-адрес без пароля и окружение с PGPASSWORD."""
    parts = urlsplit(settings.database_url.replace("+asyncpg", ""))
    env = dict(os.environ)
    netloc = parts.netloc
    if parts.password is not None:
        env["PGPASSWORD"] = unquote(parts.password)
        user = parts.username or ""
        host = netloc.rsplit("@", 1)[1]
        netloc = f"{user}@{host}" if user else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")), env


def _tail(stderr: bytes) -> str:
    return stderr.decode(errors="replace").strip()[-400:]


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ── Создание ──────────────────────────────────────────────────────────
async def create(suffix: str = "") -> BackupFile:
    async with _lock:
        return await _create_locked(suffix)


async def _create_locked(suffix: str = "") -> BackupFile:
    os.makedirs(_directory(), exist_ok=True)
    name = new_name(suffix)
    final = os.path.join(_directory(), name)
    # Временный файл с другим расширением: недописанная копия не должна
    # выглядеть в списке как настоящая.
    partial = final + ".part"
    with tempfile.TemporaryDirectory(prefix="backup-", dir=_directory()) as work:
        dump = os.path.join(work, DUMP_MEMBER)
        await _pg_dump(dump)
        try:
            await asyncio.to_thread(_pack, partial, dump)
        except BaseException:
            _remove(partial)
            raise
    os.replace(partial, final)
    log.info("Создана резервная копия %s", name)
    return _get(name)


async def _pg_dump(target: str) -> None:
    dsn, env = _pg_target()
    try:
        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            f"--dbname={dsn}",
            f"--file={target}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupError("В контейнере нет pg_dump") from exc
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise BackupError(f"pg_dump завершился с ошибкой: {_tail(stderr)}")


def _upload_files() -> list[str]:
    directory = settings.upload_dir
    if not os.path.isdir(directory):
        return []
    return sorted(
        name
        for name in os.listdir(directory)
        if _UPLOAD_NAME.match(name) and os.path.isfile(os.path.join(directory, name))
    )


def _pack(target: str, dump: str) -> None:
    meta = {
        "format": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "encryption_key": settings.encryption_key,
    }
    with tarfile.open(target, "w:gz") as tar:
        tar.add(dump, arcname=DUMP_MEMBER)
        raw = json.dumps(meta).encode()
        info = tarfile.TarInfo(META_MEMBER)
        info.size = len(raw)
        info.mtime = int(datetime.now(UTC).timestamp())
        tar.addfile(info, io.BytesIO(raw))
        for name in _upload_files():
            tar.add(os.path.join(settings.upload_dir, name), arcname=UPLOADS_PREFIX + name)


# ── Удаление, хранение, загрузка ──────────────────────────────────────
def delete(name: str) -> None:
    path = path_of(name)
    if not os.path.isfile(path):
        raise BackupError("Резервная копия не найдена")
    os.remove(path)


def prune(keep: int) -> list[str]:
    """Оставляет `keep` самых свежих копий, остальные удаляет."""
    removed = []
    for old in list_files()[max(keep, 1):]:
        _remove(os.path.join(_directory(), old.name))
        removed.append(old.name)
    if removed:
        log.info("Удалены старые резервные копии: %s", ", ".join(removed))
    return removed


def _is_full_archive(path: str) -> bool:
    try:
        with tarfile.open(path, "r:gz") as tar:
            return DUMP_MEMBER in tar.getnames()
    except (tarfile.TarError, OSError, EOFError):
        return False


def save_upload(source: IO[bytes]) -> BackupFile:
    """Загруженная админом копия (например, с прошлого сервера)."""
    if source.read(2) != b"\x1f\x8b":
        raise BackupError("Нужен файл резервной копии панели (.tar.gz или .sql.gz)")
    source.seek(0)
    os.makedirs(_directory(), exist_ok=True)
    partial = os.path.join(_directory(), f"upload-{os.getpid()}-{id(source)}.part")
    with open(partial, "wb") as out:
        shutil.copyfileobj(source, out, CHUNK)
    ext = "tar" if _is_full_archive(partial) else "sql"
    if ext == "sql":
        try:
            with gzip.open(partial, "rb") as check:
                check.read(CHUNK)
        except (OSError, EOFError) as exc:
            _remove(partial)
            raise BackupError("Файл повреждён или это не резервная копия панели") from exc
    name = new_name("upload", ext)
    os.replace(partial, os.path.join(_directory(), name))
    return _get(name)


# ── Восстановление ────────────────────────────────────────────────────
async def restore(name: str) -> RestoreResult:
    """Накатывает копию поверх текущей базы одной транзакцией.

    Перед этим делается страховочная копия текущего состояния: ошибиться
    файлом — не повод терять данные. После восстановления схема доводится
    до текущей версии (`alembic upgrade head`) — копия могла быть сделана
    старой версией панели, — возвращаются картинки и перешифровываются
    секреты, если копия с другого сервера."""
    path = path_of(name)
    if not os.path.isfile(path):
        raise BackupError("Резервная копия не найдена")
    async with _lock:
        safety = await _create_locked("before_restore")
        if name.endswith(".sql.gz"):
            with gzip.open(path, "rb") as dump:
                await _psql_restore(dump)
            meta: dict = {}
            uploads = 0
        else:
            meta, uploads = await _restore_archive(path)
        await _alembic_upgrade()
        old_key = meta.get("encryption_key")
        reencrypted = 0
        if old_key and old_key != settings.encryption_key:
            reencrypted = await reencrypt(old_key)
    log.warning(
        "База восстановлена из %s (страховочная копия %s, картинок %s, перешифровано %s)",
        name,
        safety.name,
        uploads,
        reencrypted,
    )
    return RestoreResult(safety=safety, uploads=uploads, reencrypted=reencrypted)


async def _restore_archive(path: str) -> tuple[dict, int]:
    try:
        tar = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"Файл копии повреждён: {exc}") from exc
    with tar:
        try:
            dump = tar.extractfile(DUMP_MEMBER)
        except KeyError:
            dump = None
        if dump is None:
            raise BackupError("В архиве нет базы данных — это не копия панели")
        meta = _read_meta(tar)
        await _psql_restore(dump)
        uploads = await asyncio.to_thread(_restore_uploads, tar)
    return meta, uploads


def _read_meta(tar: tarfile.TarFile) -> dict:
    try:
        member = tar.extractfile(META_MEMBER)
    except KeyError:
        return {}
    if member is None:
        return {}
    try:
        data = json.loads(member.read())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _restore_uploads(tar: tarfile.TarFile) -> int:
    """Возвращает картинки. Только плоские имена — путь из архива не
    должен выводить за пределы каталога uploads."""
    os.makedirs(settings.upload_dir, exist_ok=True)
    count = 0
    for member in tar.getmembers():
        if not member.isfile() or not member.name.startswith(UPLOADS_PREFIX):
            continue
        name = member.name[len(UPLOADS_PREFIX):]
        if not _UPLOAD_NAME.match(name):
            continue
        source = tar.extractfile(member)
        if source is None:
            continue
        with open(os.path.join(settings.upload_dir, name), "wb") as out:
            shutil.copyfileobj(source, out, CHUNK)
        count += 1
    return count


async def _psql_restore(dump: IO[bytes]) -> None:
    dsn, env = _pg_target()
    try:
        process = await asyncio.create_subprocess_exec(
            "psql",
            "--quiet",
            "--single-transaction",
            "--set=ON_ERROR_STOP=1",
            f"--dbname={dsn}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupError("В контейнере нет psql") from exc
    assert process.stdin is not None and process.stderr is not None
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        while chunk := dump.read(CHUNK):
            process.stdin.write(chunk)
            await process.stdin.drain()
    except (OSError, EOFError, tarfile.TarError) as exc:
        process.kill()
        await process.wait()
        raise BackupError(f"Файл копии повреждён: {exc}") from exc
    process.stdin.close()
    await process.wait()
    stderr = await stderr_task
    if process.returncode != 0:
        raise BackupError(f"Восстановление не удалось, база не изменена: {_tail(stderr)}")


async def _alembic_upgrade() -> None:
    process = await asyncio.create_subprocess_exec(
        "alembic",
        "upgrade",
        "head",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise BackupError(f"Копия восстановлена, но миграции не прошли: {_tail(stderr)}")


async def reencrypt(old_key: str) -> int:
    """Секреты из копии другого сервера — под ключ этого. Значение, которое
    уже расшифровывается текущим ключом, не трогаем."""
    from cryptography.fernet import Fernet, InvalidToken
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from shared.crypto import decrypt, encrypt
    from shared.db.models import BotConfig, Setting

    try:
        old = Fernet(old_key.encode())
    except ValueError:
        log.warning("В копии некорректный ключ шифрования — секреты не перенесены")
        return 0

    def convert(value: str | None) -> str | None:
        if not value:
            return None
        try:
            decrypt(value)
            return None
        except ValueError:
            pass
        try:
            return encrypt(old.decrypt(value.encode()).decode())
        except InvalidToken:
            return None

    changed = 0
    async with SessionLocal() as db:
        for row in await db.scalars(select(Setting).where(Setting.is_secret.is_(True))):
            if (new := convert(row.value)) is not None:
                row.value = new
                changed += 1
        for row in await db.scalars(select(BotConfig)):
            for field in ("token_encrypted", "proxy_encrypted", "support_token_encrypted"):
                if (new := convert(getattr(row, field))) is not None:
                    setattr(row, field, new)
                    changed += 1
        await db.commit()
    return changed


# ── Расписание ────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Schedule:
    enabled: bool
    interval_hours: int
    keep: int


async def load_schedule() -> Schedule:
    from app.db.session import SessionLocal
    from app.services import settings_service as cfg

    async with SessionLocal() as db:
        values = await cfg.get_many(db, AUTO_ENABLED, INTERVAL_HOURS, KEEP)
    return Schedule(
        enabled=values[AUTO_ENABLED] == "true",
        interval_hours=int(values[INTERVAL_HOURS] or DEFAULT_INTERVAL_HOURS),
        keep=int(values[KEEP] or DEFAULT_KEEP),
    )


def due(schedule: Schedule, files: list[BackupFile], now: datetime | None = None) -> bool:
    if not schedule.enabled:
        return False
    if not files:
        return True
    now = now or datetime.now(UTC)
    return now - files[0].created_at >= timedelta(hours=schedule.interval_hours)


async def run_scheduled() -> bool:
    schedule = await load_schedule()
    if not due(schedule, list_files()) or busy():
        return False
    await create()
    prune(schedule.keep)
    return True


async def scheduler() -> None:
    await asyncio.sleep(SCHEDULER_START_DELAY)
    while True:
        try:
            await run_scheduled()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Автоматическая резервная копия не создана")
        await asyncio.sleep(SCHEDULER_TICK)

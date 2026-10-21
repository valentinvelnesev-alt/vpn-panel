"""Резервные копии БД: создание, хранение, восстановление и расписание.

Копия — `pg_dump --clean --if-exists` в gzip: такой дамп сам удаляет и
пересоздаёт таблицы, поэтому его можно накатить поверх работающей базы.
pg_dump/psql запускаются без shell, адрес БД передаётся аргументом
процесса, а пароль — переменной окружения: раньше вся строка подключения
с паролем подставлялась в shell-команду (и ломалась на кавычке в пароле).

Расписание живёт в процессе панели: раз в несколько минут смотрим, не
пора ли сделать очередную копию, и удаляем лишние сверх лимита хранения.
"""

import asyncio
import gzip
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit, urlunsplit

from app.core.config import settings

log = logging.getLogger("backups")

NAME_RE = re.compile(r"^backup_\d{8}_\d{6}(_[a-z_]+)?\.sql\.gz$")
CHUNK = 64 * 1024

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

# Одна операция за раз: копия во время восстановления (или две
# восстановления подряд) дали бы мусор.
_lock = asyncio.Lock()


class BackupError(Exception):
    """Сообщение показывается администратору как есть."""


@dataclass(frozen=True, slots=True)
class BackupFile:
    name: str
    size_bytes: int
    created_at: datetime


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


def new_name(suffix: str = "") -> str:
    tail = f"_{suffix}" if suffix else ""
    return f"backup_{datetime.now(UTC):%Y%m%d_%H%M%S}{tail}.sql.gz"


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


async def create(suffix: str = "") -> BackupFile:
    async with _lock:
        return await _create_locked(suffix)


async def _create_locked(suffix: str = "") -> BackupFile:
    os.makedirs(_directory(), exist_ok=True)
    name = new_name(suffix)
    final = os.path.join(_directory(), name)
    partial = final + ".part"
    dsn, env = _pg_target()
    try:
        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            f"--dbname={dsn}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupError("В контейнере нет pg_dump") from exc

    assert process.stdout is not None and process.stderr is not None
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        # Пишем во временный файл и переименовываем в конце: недописанная
        # копия не должна выглядеть в списке как настоящая.
        with gzip.open(partial, "wb") as out:
            while chunk := await process.stdout.read(CHUNK):
                out.write(chunk)
        await process.wait()
        stderr = await stderr_task
    except BaseException:
        process.kill()
        _remove(partial)
        raise
    if process.returncode != 0:
        _remove(partial)
        raise BackupError(f"pg_dump завершился с ошибкой: {_tail(stderr)}")
    os.replace(partial, final)
    log.info("Создана резервная копия %s", name)
    return next(f for f in list_files() if f.name == name)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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


def save_upload(data: bytes) -> BackupFile:
    """Загруженная админом копия (например, с прошлого сервера)."""
    if data[:2] != b"\x1f\x8b":
        raise BackupError("Нужен файл .sql.gz, созданный панелью")
    os.makedirs(_directory(), exist_ok=True)
    name = new_name("upload")
    with open(os.path.join(_directory(), name), "wb") as f:
        f.write(data)
    return next(f for f in list_files() if f.name == name)


async def restore(name: str) -> BackupFile:
    """Накатывает копию поверх текущей базы одной транзакцией.

    Перед этим делается страховочная копия текущего состояния: ошибиться
    файлом — не повод терять данные. После восстановления схема доводится
    до текущей версии (`alembic upgrade head`) — копия могла быть сделана
    старой версией панели."""
    path = path_of(name)
    if not os.path.isfile(path):
        raise BackupError("Резервная копия не найдена")
    async with _lock:
        safety = await _create_locked("before_restore")
        await _psql_restore(path)
        await _alembic_upgrade()
    log.warning("База восстановлена из %s (страховочная копия %s)", name, safety.name)
    return safety


async def _psql_restore(path: str) -> None:
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
        with gzip.open(path, "rb") as dump:
            while chunk := dump.read(CHUNK):
                process.stdin.write(chunk)
                await process.stdin.drain()
    except (OSError, EOFError) as exc:
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

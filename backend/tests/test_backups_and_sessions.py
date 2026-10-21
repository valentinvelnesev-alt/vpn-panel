"""Резервные копии (без настоящего pg_dump) и отзыв сессий админа."""

import gzip
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import backups as svc
from tests.test_panel_api import LOGIN, PASSWORD, _db  # noqa: F401  — фикстура БД


@pytest.fixture(autouse=True)
def backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.v1.backups.bus.publish", _noop)
    with TestClient(app) as c:
        assert c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}).status_code == 200
        yield c


def _make(backup_dir, name: str, age_hours: float = 0) -> None:
    path = backup_dir / name
    with gzip.open(path, "wb") as f:
        f.write(b"-- dump")
    stamp = (datetime.now(UTC) - timedelta(hours=age_hours)).timestamp()
    os.utime(path, (stamp, stamp))


# ── Список, удаление, скачивание ──────────────────────────────────────
def test_list_newest_first_and_ignores_foreign_files(client, backup_dir) -> None:
    _make(backup_dir, "backup_20260101_000000.sql.gz", age_hours=48)
    _make(backup_dir, "backup_20260102_000000.sql.gz", age_hours=1)
    (backup_dir / "notes.txt").write_text("x")
    body = client.get("/api/v1/backups").json()
    assert [f["name"] for f in body["files"]] == [
        "backup_20260102_000000.sql.gz",
        "backup_20260101_000000.sql.gz",
    ]
    assert body["schedule"] == {"enabled": False, "interval_hours": 24, "keep": 7}


def test_download_and_delete(client, backup_dir) -> None:
    name = "backup_20260101_000000.sql.gz"
    _make(backup_dir, name)
    r = client.get(f"/api/v1/backups/{name}/download")
    assert r.status_code == 200
    assert gzip.decompress(r.content) == b"-- dump"
    assert client.delete(f"/api/v1/backups/{name}").status_code == 204
    assert client.get("/api/v1/backups").json()["files"] == []
    assert client.delete(f"/api/v1/backups/{name}").status_code == 404


def test_path_traversal_rejected(client) -> None:
    assert client.get("/api/v1/backups/..%2F..%2Fetc%2Fpasswd/download").status_code in (404, 422)
    with pytest.raises(svc.BackupError):
        svc.path_of("../backup_20260101_000000.sql.gz")


def test_upload_requires_gzip(client) -> None:
    bad = client.post("/api/v1/backups/upload", files={"file": ("x.sql.gz", b"plain text")})
    assert bad.status_code == 422
    ok = client.post(
        "/api/v1/backups/upload", files={"file": ("x.sql.gz", gzip.compress(b"-- dump"))}
    )
    assert ok.status_code == 201
    assert ok.json()["name"].endswith("_upload.sql.gz")


def test_restore_requires_confirmation(client, backup_dir) -> None:
    _make(backup_dir, "backup_20260101_000000.sql.gz")
    r = client.post(
        "/api/v1/backups/backup_20260101_000000.sql.gz/restore", json={"confirm": "да"}
    )
    assert r.status_code == 422


def test_restore_makes_safety_copy_first(client, backup_dir, monkeypatch) -> None:
    _make(backup_dir, "backup_20260101_000000.sql.gz", age_hours=5)
    steps: list[str] = []

    async def fake_create(suffix: str = "") -> svc.BackupFile:
        steps.append(f"backup:{suffix}")
        name = svc.new_name(suffix)
        _make(backup_dir, name)
        return next(f for f in svc.list_files() if f.name == name)

    async def fake_psql(path: str) -> None:
        steps.append(f"restore:{os.path.basename(path)}")

    async def fake_alembic() -> None:
        steps.append("alembic")

    monkeypatch.setattr(svc, "_create_locked", fake_create)
    monkeypatch.setattr(svc, "_psql_restore", fake_psql)
    monkeypatch.setattr(svc, "_alembic_upgrade", fake_alembic)

    r = client.post(
        "/api/v1/backups/backup_20260101_000000.sql.gz/restore", json={"confirm": "восстановить"}
    )
    assert r.status_code == 200, r.text
    assert steps == ["backup:before_restore", "restore:backup_20260101_000000.sql.gz", "alembic"]
    assert r.json()["safety_backup"].endswith("_before_restore.sql.gz")


# ── Расписание ────────────────────────────────────────────────────────
def test_schedule_saved(client) -> None:
    r = client.put(
        "/api/v1/backups/schedule", json={"enabled": True, "interval_hours": 12, "keep": 3}
    )
    assert r.status_code == 200
    assert client.get("/api/v1/backups").json()["schedule"] == {
        "enabled": True,
        "interval_hours": 12,
        "keep": 3,
    }
    assert client.put(
        "/api/v1/backups/schedule", json={"enabled": True, "interval_hours": 0, "keep": 3}
    ).status_code == 422


def test_due_and_prune(backup_dir) -> None:
    schedule = svc.Schedule(enabled=True, interval_hours=24, keep=2)
    assert svc.due(schedule, []) is True
    _make(backup_dir, "backup_20260101_000000.sql.gz", age_hours=30)
    assert svc.due(schedule, svc.list_files()) is True
    _make(backup_dir, "backup_20260102_000000.sql.gz", age_hours=20)
    _make(backup_dir, "backup_20260103_000000.sql.gz", age_hours=1)
    assert svc.due(schedule, svc.list_files()) is False
    assert svc.due(svc.Schedule(False, 24, 2), []) is False

    assert svc.prune(2) == ["backup_20260101_000000.sql.gz"]
    assert len(svc.list_files()) == 2


def test_pg_target_hides_password(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://vpn:p%40ss'w@postgres:5432/vpnpanel"
    )
    dsn, env = svc._pg_target()
    assert dsn == "postgresql://vpn@postgres:5432/vpnpanel"
    assert env["PGPASSWORD"] == "p@ss'w"


# ── Сессии ────────────────────────────────────────────────────────────
def test_logout_all_kills_existing_access_tokens() -> None:
    with TestClient(app) as first, TestClient(app) as second:
        for c in (first, second):
            assert c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}).status_code == 200
        assert second.get("/api/v1/auth/me").status_code == 200

        assert first.post("/api/v1/auth/logout-all").status_code == 204
        # Access-токен второго устройства ещё не истёк, но уже не действует,
        # а refresh отозван — сессия не продлевается.
        assert second.get("/api/v1/auth/me").status_code == 401
        assert second.post("/api/v1/auth/refresh").status_code == 401


def test_change_password_keeps_this_session_only() -> None:
    new = "another-strong-password"
    with TestClient(app) as me, TestClient(app) as other:
        for c in (me, other):
            assert c.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}).status_code == 200

        wrong = me.post(
            "/api/v1/auth/password",
            json={"current_password": "nope", "new_password": new},
        )
        assert wrong.status_code == 422
        short = me.post(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "short"},
        )
        assert short.status_code == 422

        ok = me.post(
            "/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": new}
        )
        assert ok.status_code == 200
        assert me.get("/api/v1/auth/me").status_code == 200
        assert other.get("/api/v1/auth/me").status_code == 401

    with TestClient(app) as fresh:
        assert fresh.post("/api/v1/auth/login", json={"login": LOGIN, "password": PASSWORD}).status_code == 401
        assert fresh.post("/api/v1/auth/login", json={"login": LOGIN, "password": new}).status_code == 200

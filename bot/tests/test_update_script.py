"""Проверка безопасного выбора прокси при повторном обновлении."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("deploy", "proxy", "compose", "expected"),
    [
        ("domain", "nginx", "docker-compose.domain.yml", "nginx"),
        ("domain", "", "docker-compose.nginx.yml", "nginx"),
        ("domain", "caddy", "docker-compose.nginx.yml", "domain"),
        ("ip", "caddy", "docker-compose.ip.yml", "ip"),
    ],
)
def test_update_preserves_proxy_and_retries(tmp_path, deploy, proxy, compose, expected):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = Path(__file__).resolve().parents[2] / "update.sh"
    (tmp_path / ".env").write_text(
        f"DEPLOY_MODE={deploy}\nPROXY_MODE={proxy}\nCOMPOSE_FILE={compose}\n"
        "PANEL_PUBLIC_URL=https://example.invalid\n"
    )
    commands = {
        "id": "echo 0",
        "git": 'case "$1" in rev-parse) echo same;; esac',
        "docker": 'printf "%s\\n" "$*" >> "$CALLS"',
    }
    for name, body in commands.items():
        command = bin_dir / name
        command.write_text(f"#!/bin/sh\n{body}\n")
        command.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CALLS=str(tmp_path / "calls"))
    env.pop("VPN_PANEL_UPDATE_REEXEC", None)
    result = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text()
    assert f"-f docker-compose.{expected}.yml pull" in calls
    assert f"-f docker-compose.{expected}.yml up -d --remove-orphans" in calls
    assert f"-f docker-compose.{expected}.yml run --rm panel-web" in calls
    assert f"COMPOSE_FILE=docker-compose.yml:docker-compose.{expected}.yml" in (tmp_path / ".env").read_text()


def test_update_refuses_other_installation(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = Path(__file__).resolve().parents[2] / "update.sh"
    (tmp_path / ".env").write_text("DEPLOY_MODE=domain\nPROXY_MODE=nginx\n")
    for name, body in {
        "id": "echo 0",
        "docker": 'case "$1" in ps) echo existing;; inspect) echo /nonexistent/other-installation;; esac',
    }.items():
        command = bin_dir / name
        command.write_text(f"#!/bin/sh\n{body}\n")
        command.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    env.pop("VPN_PANEL_UPDATE_REEXEC", None)
    result = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "другой папки" in result.stderr
    assert not (tmp_path / "backups").exists()

"""Сверка клиента с официальной спекой Remnawave.

Клиент написан руками (генерация всех схем 2.8.1 дала бы неподъёмный объём),
поэтому расхождения ловит этот тест: он проверяет, что каждый эндпоинт, к
которому мы обращаемся, есть в спеке с тем же методом, и что поля, которые
панель читает, объявлены в схемах ответов.

Обновили `shared/openapi/*.json` — прогоните тест: он покажет, что именно
сломалось, до того, как это увидит пользователь.
"""

import json
import re
from pathlib import Path

import pytest

SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "shared/openapi/remnawave-2.8.1.json"
)

# (метод, путь) — ровно то, что дёргает RemnawaveClient.
CALLED_ENDPOINTS = [
    ("get", "/api/system/metadata"),
    ("get", "/api/system/stats"),
    ("get", "/api/nodes"),
    ("get", "/api/nodes/{uuid}"),
    ("post", "/api/nodes/{uuid}/actions/enable"),
    ("post", "/api/nodes/{uuid}/actions/disable"),
    ("post", "/api/nodes/{uuid}/actions/restart"),
    ("post", "/api/nodes/actions/restart-all"),
    ("get", "/api/users"),
    ("post", "/api/users"),
    ("patch", "/api/users"),
    ("get", "/api/users/{uuid}"),
    ("get", "/api/users/by-telegram-id/{telegramId}"),
    ("get", "/api/users/by-username/{username}"),
    ("get", "/api/users/by-email/{email}"),
    ("post", "/api/users/bulk/extend-expiration-date"),
    ("post", "/api/users/bulk/reset-traffic"),
    ("post", "/api/users/bulk/update-squads"),
    ("post", "/api/users/bulk/delete"),
    ("get", "/api/internal-squads"),
    ("get", "/api/hwid/devices/{userUuid}"),
    ("post", "/api/hwid/devices/delete"),
    ("post", "/api/hwid/devices/delete-all"),
]

# Поля, на которые опираются модели и UI: путь до схемы → имена полей.
REQUIRED_FIELDS = {
    ("get", "/api/nodes"): (
        "response[]",
        ["uuid", "name", "address", "isConnected", "isDisabled", "usersOnline",
         "trafficUsedBytes", "countryCode"],
    ),
    ("get", "/api/users"): (
        "response.users[]",
        ["uuid", "username", "status", "expireAt", "telegramId", "subscriptionUrl"],
    ),
    ("get", "/api/system/stats"): (
        "response",
        ["users", "onlineStats", "nodes"],
    ),
    ("get", "/api/hwid/devices/{userUuid}"): (
        "response.devices[]",
        ["hwid", "platform", "deviceModel"],
    ),
}


@pytest.fixture(scope="module")
def spec() -> dict:
    if not SPEC_PATH.exists():
        pytest.skip(f"спека не найдена: {SPEC_PATH}")
    return json.loads(SPEC_PATH.read_text())


def _resolve(spec: dict, schema: dict) -> dict:
    seen = 0
    while "$ref" in schema:
        seen += 1
        if seen > 20:
            raise AssertionError("циклическая ссылка в спеке")
        node: dict = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def _walk(spec: dict, schema: dict, path: str) -> dict:
    """Идёт по пути вида `response.users[]` внутри схемы ответа."""
    schema = _resolve(spec, schema)
    for step in filter(None, re.split(r"\.", path)):
        array = step.endswith("[]")
        key = step.removesuffix("[]")
        if key:
            props = schema.get("properties", {})
            assert key in props, f"нет поля {key!r} в {path!r}"
            schema = _resolve(spec, props[key])
        if array:
            schema = _resolve(spec, schema["items"])
    return schema


@pytest.mark.parametrize("method,path", CALLED_ENDPOINTS)
def test_endpoint_exists(spec: dict, method: str, path: str) -> None:
    assert path in spec["paths"], f"эндпоинт {path} исчез из спеки Remnawave"
    assert method in spec["paths"][path], (
        f"{path} больше не поддерживает {method.upper()}"
    )


@pytest.mark.parametrize("key,expected", REQUIRED_FIELDS.items())
def test_response_fields(spec: dict, key: tuple[str, str], expected: tuple) -> None:
    method, path = key
    sub_path, fields = expected

    responses = spec["paths"][path][method]["responses"]
    ok = responses.get("200") or responses.get("201")
    schema = ok["content"]["application/json"]["schema"]

    target = _walk(spec, schema, sub_path)
    properties = target.get("properties", {})
    missing = [f for f in fields if f not in properties]
    assert not missing, f"{method.upper()} {path}: пропали поля {missing}"


def test_user_status_enum_matches(spec: dict) -> None:
    """Статусы захардкожены в UI (цвета, фильтры) — сверяем со спекой."""
    from shared.remnawave.models import UserStatus

    schema = _walk(
        spec,
        spec["paths"]["/api/users"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"],
        "response.users[]",
    )
    spec_values = set(_resolve(spec, schema["properties"]["status"])["enum"])
    assert spec_values == {s.value for s in UserStatus}

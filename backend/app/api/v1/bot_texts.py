"""Тексты сообщений и подписи кнопок бота: стандартные значения и свои.

Свои тексты хранятся в bot_config.bot_texts, подписи кнопок — в menu_labels
(их сохраняют вместе с остальными настройками меню). Здесь отдаётся реестр
(стандартные значения, подстановки, примеры для превью) и сохраняются тексты.
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession
from app.api.v1.bot import _audit, _config
from shared import bus
from shared.botcopy import BUTTONS, MESSAGES, PLAIN_ICONS, Copy, CopyError, validate_message

router = APIRouter(prefix="/bot/texts", tags=["bot-texts"])


class VarOut(BaseModel):
    name: str
    hint: str
    example: str


class CopyOut(BaseModel):
    key: str
    default: str
    title: str
    where: str
    variables: list[VarOut]
    # Своё значение; None — используется стандартное.
    value: str | None = None


class TextsOut(BaseModel):
    messages: list[CopyOut]
    buttons: list[CopyOut]
    icons: dict[str, str]


def _out(item: Copy, custom: dict[str, str]) -> CopyOut:
    return CopyOut(
        key=item.key,
        default=item.default,
        title=item.title,
        where=item.where,
        variables=[VarOut(name=v.name, hint=v.hint, example=v.example) for v in item.variables],
        value=custom.get(item.key),
    )


def _all(row) -> TextsOut:
    texts = dict(row.bot_texts or {})
    labels = dict(row.menu_labels or {})
    return TextsOut(
        messages=[_out(m, texts) for m in MESSAGES],
        buttons=[_out(b, labels) for b in BUTTONS],
        icons=PLAIN_ICONS,
    )


@router.get("", response_model=TextsOut)
async def get_texts(admin: CurrentAdmin, db: DbSession) -> TextsOut:
    return _all(await _config(db))


class TextsIn(BaseModel):
    # Все свои тексты целиком: чего нет в словаре или пусто — стандартный.
    values: dict[str, str] = Field(default_factory=dict, max_length=64)


@router.put("", response_model=TextsOut)
async def save_texts(
    data: TextsIn, admin: CurrentAdmin, db: DbSession, request: Request
) -> TextsOut:
    clean: dict[str, str] = {}
    for key, text in data.values.items():
        try:
            checked = validate_message(key, text or "")
        except CopyError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        if checked is not None:
            clean[key] = checked
    row = await _config(db)
    row.bot_texts = clean
    _audit(db, admin, "bot.texts", request, changed=sorted(clean))
    await db.flush()
    await db.commit()  # видно боту ДО pub/sub-уведомления
    await bus.publish(bus.CMD_RELOAD)
    return _all(row)

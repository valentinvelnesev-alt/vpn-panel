"""Отрисовка экранов бота.

Экран — это одно сообщение: текст (или подпись к картинке), клавиатура и,
если в панели загружена картинка меню, само фото. Переходы между экранами
редактируют это сообщение, а не плодят новые.

Две особенности Telegram, из-за которых здесь не обойтись одним edit_text:

* подпись к фото ограничена 1024 символами против 4096 у обычного
  сообщения. Длинные экраны (инструкции по подключению) в подпись не
  влезают, поэтому показываются текстом — иначе отправка просто не пройдёт;
* тип сообщения на лету не меняется: текстовое нельзя превратить в фото и
  наоборот. При смене типа старое сообщение удаляется и шлётся новое.

Картинка отправляется файлом с диска (общий том с панелью), а не ссылкой:
в режиме по IP порт панели обычно закрыт файрволом, и Telegram не смог бы
её скачать. Первая отправка возвращает file_id, дальше используется он —
это быстрее и не грузит диск.
"""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

log = logging.getLogger("bot.ui")

# Лимит Telegram на подпись к медиа.
MAX_CAPTION = 1024

# Имя файла → file_id. Загруженная картинка живёт у Telegram, поэтому
# повторно отправлять сам файл не нужно. Кэш в памяти процесса: после
# перезапуска бота первая отправка просто загрузит файл заново.
_file_ids: dict[str, str] = {}


def reset_photo_cache(path: str | None = None) -> None:
    """Картинку заменили — забываем её file_id."""
    if path is None:
        _file_ids.clear()
    else:
        _file_ids.pop(path, None)


def _photo_input(path: str):
    return _file_ids.get(path) or FSInputFile(path)


def _remember(path: str, message: Message) -> None:
    if message.photo:
        _file_ids[path] = message.photo[-1].file_id


def fits_caption(text: str) -> bool:
    return len(text) <= MAX_CAPTION


async def safe_edit(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    photo: str | None = None,
    **kwargs: object,
) -> None:
    """Показывает экран, по возможности редактируя текущее сообщение."""
    with_photo = bool(photo) and fits_caption(text)

    try:
        if with_photo:
            await _edit_as_photo(message, text, reply_markup, photo, **kwargs)
        else:
            await _edit_as_text(message, text, reply_markup, **kwargs)
    except TelegramBadRequest as exc:
        detail = str(exc).lower()
        if "message is not modified" in detail:
            return
        if (
            "message can't be edited" in detail
            or "there is no text in the message" in detail
            or "message to edit not found" in detail
        ):
            await send_screen(message, text, reply_markup, photo=photo, **kwargs)
            return
        raise


async def _edit_as_photo(message, text, reply_markup, photo, **kwargs) -> None:
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=reply_markup, **kwargs)
        return
    # Текстовое сообщение картинкой не станет — заменяем его новым.
    await _delete(message)
    await send_screen(message, text, reply_markup, photo=photo, **kwargs)


async def _edit_as_text(message, text, reply_markup, **kwargs) -> None:
    if message.photo:
        # Экран не влезает в подпись: показываем текстом вместо фото.
        await _delete(message)
        await send_screen(message, text, reply_markup, photo=None, **kwargs)
        return
    await message.edit_text(text, reply_markup=reply_markup, **kwargs)


async def send_screen(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    photo: str | None = None,
    **kwargs: object,
) -> Message:
    """Отправляет экран новым сообщением."""
    if photo and fits_caption(text):
        try:
            sent = await message.answer_photo(
                _photo_input(photo), caption=text, reply_markup=reply_markup, **kwargs
            )
            _remember(photo, sent)
            return sent
        except TelegramBadRequest as exc:
            # Картинка битая или недоступна — экран важнее оформления.
            log.warning("Не удалось отправить картинку меню %s: %s", photo, exc)
            reset_photo_cache(photo)
        except OSError as exc:
            log.warning("Картинка меню %s не читается: %s", photo, exc)
    return await message.answer(text, reply_markup=reply_markup, **kwargs)


async def replace_media(message: Message, photo: str) -> None:
    """Меняет саму картинку у существующего экрана."""
    await message.edit_media(InputMediaPhoto(media=_photo_input(photo)))


async def _delete(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

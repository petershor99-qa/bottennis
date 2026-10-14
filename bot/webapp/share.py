"""
«Поделиться» картинкой (v2.176.0). Страница рисует карточку (итоги дня или свой
профиль) и присылает PNG; бот отправляет его игроку в личный чат — оттуда
картинку можно переслать куда угодно. Так работает на любом клиенте Telegram,
без отдельных разрешений. Это ответ на нажатие игрока, а не уведомление.
"""
import base64
import binascii
import logging
import time

from aiogram.types import BufferedInputFile

logger = logging.getLogger(__name__)

SHARE_MAX_BYTES = 2 * 1024 * 1024
SHARE_COOLDOWN_S = 10
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SHARE_CAPTIONS = {
    "day": "Итоги дня из приложения клуба. Перешли картинку, куда хочешь.",
    "profile": "Твой профиль из приложения клуба. Перешли картинку, куда хочешь.",
    "match": "Матч из приложения клуба. Перешли картинку, куда хочешь.",  # v2.177.0
}

_last_share: dict[int, float] = {}


def decode_png(data_url: str) -> bytes | None:
    """«data:image/png;base64,…» -> байты PNG; None — не PNG или слишком большой."""
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        return None
    if len(data_url) > SHARE_MAX_BYTES * 4 // 3 + len(prefix) + 4:
        return None
    try:
        data = base64.b64decode(data_url[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data.startswith(PNG_SIGNATURE) or len(data) > SHARE_MAX_BYTES:
        return None
    return data


async def send_share(bot, telegram_id: int, kind: str, data_url: str) -> str:
    """Отправить картинку игроку. Возвращает "ok", "bad" (не та картинка или
    вид), "wait" (чаще раза в SHARE_COOLDOWN_S) или "fail" (Telegram не принял)."""
    if kind not in SHARE_CAPTIONS:
        return "bad"
    data = decode_png(data_url)
    if data is None:
        return "bad"
    now = time.monotonic()
    if now - _last_share.get(telegram_id, -SHARE_COOLDOWN_S) < SHARE_COOLDOWN_S:
        return "wait"
    _last_share[telegram_id] = now
    try:
        await bot.send_photo(telegram_id, BufferedInputFile(data, filename=f"club-{kind}.png"),
                             caption=SHARE_CAPTIONS[kind])
    except Exception:
        logger.warning("Mini App: не удалось отправить картинку «Поделиться»", exc_info=True)
        return "fail"
    return "ok"

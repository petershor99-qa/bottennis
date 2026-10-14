"""
Фото игроков для Mini App (v2.162.0). Бот берёт аватар игрока из Telegram
(`getUserProfilePhotos`) и отдаёт его странице через свой API — у самой
страницы нет доступа к чужим фото. Результат кэшируется в памяти на
`PHOTO_TTL_SECONDS`, в том числе «фото нет» (скрыто настройками приватности
или не загружено) — чтобы не дёргать Telegram на каждый экран.

С v2.174.0 игрок может поставить своё фото через бота (`/photo`): его
`file_id` лежит в `Player.photo_file_id` и важнее аватара Telegram. Ключ кэша
включает `file_id`, поэтому новое фото видно сразу, без ожидания срока кэша.
"""
import hashlib
import logging
import time
from io import BytesIO

from aiogram import Bot

logger = logging.getLogger(__name__)

PHOTO_TTL_SECONDS = 12 * 60 * 60
PHOTO_MIN_SIDE = 160        # самый маленький размер не меньше этого — для аватара хватит
PHOTO_MAX_BYTES = 512 * 1024


class PhotoCache:
    def __init__(self) -> None:
        self._items: dict[tuple[int, str | None], tuple[float, bytes | None]] = {}

    async def get(self, bot: Bot, telegram_id: int, custom_file_id: str | None = None) -> bytes | None:
        """Своё фото игрока (если поставлено и скачивается), иначе аватар Telegram."""
        now = time.monotonic()
        key = (telegram_id, custom_file_id)
        cached = self._items.get(key)
        if cached is not None and now - cached[0] < PHOTO_TTL_SECONDS:
            return cached[1]
        try:
            data = await self._download(bot, custom_file_id) if custom_file_id else None
            if data is None:
                data = await self._fetch(bot, telegram_id)
        except Exception:
            # Сбой Telegram не кэшируем: иначе фото пропало бы на 12 часов
            logger.warning("Mini App: не удалось получить фото игрока", exc_info=True)
            return None
        self._items[key] = (now, data)
        return data

    @staticmethod
    async def _download(bot: Bot, file_id: str) -> bytes | None:
        try:
            buf = await bot.download(file_id, destination=BytesIO())
        except Exception:
            # file_id мог устареть — тогда показываем аватар Telegram
            logger.info("Mini App: своё фото игрока не скачалось, беру аватар Telegram", exc_info=True)
            return None
        data = buf.getvalue() if buf is not None else b""
        return data if 0 < len(data) <= PHOTO_MAX_BYTES else None

    @staticmethod
    async def _fetch(bot: Bot, telegram_id: int) -> bytes | None:
        photos = await bot.get_user_profile_photos(user_id=telegram_id, limit=1)
        if not photos.photos:
            # Telegram не отдаёт боту фото, если у игрока оно скрыто настройкой
            # приватности («Фото профиля» — «Мои контакты») или не загружено.
            logger.info("Mini App: фото игрока %s недоступно боту (скрыто приватностью или не загружено)", telegram_id)
            return None
        sizes = sorted(photos.photos[0], key=lambda s: s.width)
        size = next((s for s in sizes if s.width >= PHOTO_MIN_SIDE), sizes[-1])
        buf = await bot.download(size.file_id, destination=BytesIO())
        if buf is None:
            logger.info("Mini App: фото игрока %s не скачалось", telegram_id)
            return None
        data = buf.getvalue()
        if not 0 < len(data) <= PHOTO_MAX_BYTES:
            logger.info("Mini App: фото игрока %s отброшено: %s байт", telegram_id, len(data))
            return None
        return data


def pick_photo_size(sizes: list) -> str:
    """file_id размера фото для аватара: самый маленький не меньше PHOTO_MIN_SIDE
    (у присланного фото это обычно 320 px), иначе самый большой."""
    ordered = sorted(sizes, key=lambda s: s.width)
    return next((s for s in ordered if s.width >= PHOTO_MIN_SIDE), ordered[-1]).file_id


def photo_etag(data: bytes) -> str:
    return '"' + hashlib.md5(data).hexdigest()[:16] + '"'  # не для защиты, только версия

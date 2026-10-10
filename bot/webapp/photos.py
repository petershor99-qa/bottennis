"""
Фото игроков для Mini App (v2.162.0). Бот берёт аватар игрока из Telegram
(`getUserProfilePhotos`) и отдаёт его странице через свой API — у самой
страницы нет доступа к чужим фото. Результат кэшируется в памяти на
`PHOTO_TTL_SECONDS`, в том числе «фото нет» (скрыто настройками приватности
или не загружено) — чтобы не дёргать Telegram на каждый экран.
"""
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
        self._items: dict[int, tuple[float, bytes | None]] = {}

    async def get(self, bot: Bot, telegram_id: int) -> bytes | None:
        now = time.monotonic()
        cached = self._items.get(telegram_id)
        if cached is not None and now - cached[0] < PHOTO_TTL_SECONDS:
            return cached[1]
        try:
            data = await self._fetch(bot, telegram_id)
        except Exception:
            # Сбой Telegram не кэшируем: иначе фото пропало бы на 12 часов
            logger.warning("Mini App: не удалось получить фото игрока", exc_info=True)
            return None
        self._items[telegram_id] = (now, data)
        return data

    @staticmethod
    async def _fetch(bot: Bot, telegram_id: int) -> bytes | None:
        photos = await bot.get_user_profile_photos(user_id=telegram_id, limit=1)
        if not photos.photos:
            return None
        sizes = sorted(photos.photos[0], key=lambda s: s.width)
        size = next((s for s in sizes if s.width >= PHOTO_MIN_SIDE), sizes[-1])
        buf = await bot.download(size.file_id, destination=BytesIO())
        if buf is None:
            return None
        data = buf.getvalue()
        return data if 0 < len(data) <= PHOTO_MAX_BYTES else None

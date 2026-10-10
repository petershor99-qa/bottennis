"""
Веб-часть Mini App (v2.160.0): aiohttp в том же процессе, что и бот, только
чтение. Писать в БД продолжает только бот — иначе второй писатель снова
упрётся в блокировку SQLite (см. v2.157.2).

Маршруты:
  GET /                                     — приложение (одна страница, переходы внутри)
  GET /static/...                           — стили, скрипт, шрифт
  GET /api/leaderboard                      — рейтинг клуба
  GET /api/player/{id}                      — профиль (свой или чужой)
  GET /api/player/{id}/stats/{key}          — раздел статистики (как в боте)
  GET /api/player/{id}/achievements[/{n}]   — достижения: категории / одна категория
  GET /api/player/{id}/radar                — радар стиля
  GET /api/player/{id}/activity             — карта активности игрока
  GET /api/player/{id}/history              — история матчей
  GET /api/h2h/{id}                         — личные встречи зрителя с игроком
  GET /api/activity/club                    — карта активности клуба
  GET /api/records                          — рекорды клуба
  GET /api/matches?offset=N                 — все матчи клуба, порциями
  GET /api/throne                           — зал славы
Вход во все /api — `Authorization: tma <initData>`.

Снаружи сервер закрыт: слушает 127.0.0.1, HTTPS даёт прокси (Caddy), см.
docs/MINIAPP.md.
"""
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Player
from bot.utils import get_player
from bot.webapp import api
from bot.webapp.auth import WebAppAuthError, init_data_from_header, telegram_id_from_init_data
from bot.webapp.config import is_webapp_allowed, webapp_listen

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "webapp" / "static"

BOT_TOKEN_KEY: web.AppKey[str] = web.AppKey("bot_token", str)
SESSION_FACTORY_KEY: web.AppKey = web.AppKey("session_factory")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

Handler = Callable[[web.Request, AsyncSession, Player], Awaitable[dict | None]]


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status, headers=_SECURITY_HEADERS)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html", headers=_SECURITY_HEADERS)


def endpoint(handler: Handler) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Общая обёртка: вход по initData, доступ по флагу, игрок по telegram_id,
    сессия только для чтения (без commit). None от обработчика — 404."""
    async def wrapped(request: web.Request) -> web.Response:
        init_data = init_data_from_header(request.headers.get("Authorization"))
        try:
            telegram_id = telegram_id_from_init_data(request.app[BOT_TOKEN_KEY], init_data)
        except WebAppAuthError:
            return _json_error(401, "Откройте приложение из бота.")
        if not is_webapp_allowed(telegram_id):
            return _json_error(403, "Приложение пока недоступно.")
        async with request.app[SESSION_FACTORY_KEY]() as session:
            viewer = await get_player(session, telegram_id)
            if viewer is None:
                return _json_error(403, "Сначала зарегистрируйтесь в боте.")
            payload = await handler(request, session, viewer)
        if payload is None:
            return _json_error(404, "Не найдено.")
        return web.json_response(payload, headers=_SECURITY_HEADERS)
    return wrapped


async def _target(request: web.Request, session: AsyncSession) -> Player | None:
    try:
        player_id = int(request.match_info["player_id"])
    except (KeyError, ValueError):
        return None
    return (await session.execute(select(Player).where(Player.id == player_id))).scalar_one_or_none()


async def leaderboard(request, session, viewer):
    return await api.leaderboard_payload(session, viewer.id)


async def player(request, session, viewer):
    target = await _target(request, session)
    return await api.player_payload(session, target, viewer) if target else None


async def player_stats(request, session, viewer):
    target = await _target(request, session)
    if target is None:
        return None
    return await api.player_stats_section_payload(session, target, viewer, request.match_info["key"])


async def achievements(request, session, viewer):
    target = await _target(request, session)
    return api.achievements_payload(target) if target else None


async def achievement_category(request, session, viewer):
    target = await _target(request, session)
    try:
        index = int(request.match_info["index"])
    except ValueError:
        return None
    return api.achievement_category_payload(target, index) if target else None


async def radar(request, session, viewer):
    target = await _target(request, session)
    return await api.radar_payload(session, target) if target else None


async def player_activity(request, session, viewer):
    target = await _target(request, session)
    return await api.activity_payload(session, target) if target else None


async def club_activity(request, session, viewer):
    return await api.activity_payload(session, None)


async def history(request, session, viewer):
    target = await _target(request, session)
    return await api.history_payload(session, target) if target else None


async def h2h(request, session, viewer):
    target = await _target(request, session)
    if target is None or target.id == viewer.id:
        return None
    return await api.h2h_payload(session, viewer, target)


async def records(request, session, viewer):
    return await api.records_payload(session)


async def matches(request, session, viewer):
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        offset = 0
    return await api.club_matches_payload(session, offset)


async def throne(request, session, viewer):
    return await api.hall_of_fame_payload(session)


def create_app(bot_token: str, session_factory) -> web.Application:
    app = web.Application()
    app[BOT_TOKEN_KEY] = bot_token
    app[SESSION_FACTORY_KEY] = session_factory
    app.router.add_get("/", index)
    routes = [
        ("/api/leaderboard", leaderboard),
        ("/api/player/{player_id}", player),
        ("/api/player/{player_id}/stats/{key}", player_stats),
        ("/api/player/{player_id}/achievements", achievements),
        ("/api/player/{player_id}/achievements/{index}", achievement_category),
        ("/api/player/{player_id}/radar", radar),
        ("/api/player/{player_id}/activity", player_activity),
        ("/api/player/{player_id}/history", history),
        ("/api/h2h/{player_id}", h2h),
        ("/api/activity/club", club_activity),
        ("/api/records", records),
        ("/api/matches", matches),
        ("/api/throne", throne),
    ]
    for path, handler in routes:
        app.router.add_get(path, endpoint(handler))
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def start_webapp(bot_token: str, session_factory) -> web.AppRunner:
    """Запускает сервер рядом с поллингом бота; вернуть runner, чтобы закрыть."""
    host, port = webapp_listen()
    runner = web.AppRunner(create_app(bot_token, session_factory), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info("Mini App: веб-сервер слушает %s:%s", host, port)
    return runner

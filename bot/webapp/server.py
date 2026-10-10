"""
Веб-часть Mini App (v2.160.0): aiohttp в том же процессе, что и бот, только
чтение. Писать в БД продолжает только бот — иначе второй писатель снова
упрётся в блокировку SQLite (см. v2.157.2).

Маршруты:
  GET /                 — страница «Рейтинг клуба» (статичный HTML/CSS/JS)
  GET /static/...       — стили, скрипт, шрифты
  GET /api/leaderboard  — данные таблицы; вход по `Authorization: tma <initData>`

Снаружи сервер закрыт: слушает 127.0.0.1, HTTPS даёт прокси (Caddy), см.
docs/MINIAPP.md.
"""
import logging
from pathlib import Path

from aiohttp import web

from bot.utils import get_player
from bot.webapp.api import leaderboard_payload
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


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status, headers=_SECURITY_HEADERS)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html", headers=_SECURITY_HEADERS)


async def api_leaderboard(request: web.Request) -> web.Response:
    init_data = init_data_from_header(request.headers.get("Authorization"))
    try:
        telegram_id = telegram_id_from_init_data(request.app[BOT_TOKEN_KEY], init_data)
    except WebAppAuthError:
        return _json_error(401, "Откройте приложение из бота.")
    if not is_webapp_allowed(telegram_id):
        return _json_error(403, "Приложение пока недоступно.")

    async with request.app[SESSION_FACTORY_KEY]() as session:
        player = await get_player(session, telegram_id)
        if player is None:
            return _json_error(403, "Сначала зарегистрируйтесь в боте.")
        payload = await leaderboard_payload(session, player.id)
    return web.json_response(payload, headers=_SECURITY_HEADERS)


def create_app(bot_token: str, session_factory) -> web.Application:
    app = web.Application()
    app[BOT_TOKEN_KEY] = bot_token
    app[SESSION_FACTORY_KEY] = session_factory
    app.router.add_get("/", index)
    app.router.add_get("/api/leaderboard", api_leaderboard)
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

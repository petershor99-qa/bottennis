"""
Веб-часть Mini App (v2.160.0): aiohttp в том же процессе, что и бот, только
чтение. Писать в БД продолжает только бот — иначе второй писатель снова
упрётся в блокировку SQLite (см. v2.157.2). Исключения — короткие записи
своей транзакцией: счётчик экранов (фоном) и реакции на матчи (v2.169.0).

Маршруты:
  GET /                                     — приложение (одна страница, переходы внутри)
  GET /static/...                           — стили, скрипт, шрифт
  GET /api/home                             — главная «Клуб сегодня»
  GET /api/leaderboard                      — рейтинг клуба
  GET /api/photo/{id}                       — фото игрока из Telegram (или 404)
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
  GET /api/summary/{kind}                   — итоги дня/недели/месяца/квартала/года (v2.168.0)
  GET /api/match/{id}                       — экран матча (v2.168.0), с реакциями (v2.169.0)
  GET /api/player/{id}/chart                — график рейтинга по датам (v2.168.0)
  POST /api/match/{id}/react                — поставить/снять реакцию (v2.169.0)
  GET /api/feed?offset=N                    — лента клуба порциями (v2.171.0)
  GET /api/when?who=club|me                 — «Когда играют»: день недели × час (v2.170.0)
  GET /api/usage                            — «Использование», только администратор (v2.170.0)
  POST /api/track                           — счётчик показанных экранов (v2.166.0)
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
from bot.webapp import api, api_club, api_insights, reactions
from bot.webapp.assets import ASSET_TYPES, Assets, accepts_gzip
from bot.webapp.auth import WebAppAuthError, init_data_from_header, telegram_id_from_init_data
from bot.webapp.config import is_admin, is_webapp_allowed, webapp_listen
from bot.webapp.photos import PhotoCache
from bot.webapp.usage import SCREEN_RE, UsageWriter, log_boot_time

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "webapp" / "static"

BOT_TOKEN_KEY: web.AppKey[str] = web.AppKey("bot_token", str)
SESSION_FACTORY_KEY: web.AppKey = web.AppKey("session_factory")
BOT_KEY: web.AppKey = web.AppKey("bot")
PHOTOS_KEY: web.AppKey[PhotoCache] = web.AppKey("photos", PhotoCache)
USAGE_KEY: web.AppKey[UsageWriter] = web.AppKey("usage", UsageWriter)
TRACK_REQUEST_KEY = "bottennis_track"  # что записать в счётчик после ответа
ASSETS_KEY: web.AppKey[Assets] = web.AppKey("assets", Assets)
# Ответы API крупнее этого сжимаются gzip (v2.172.0); мелкие — дороже сжимать
COMPRESS_MIN_BYTES = 1024

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

Handler = Callable[[web.Request, AsyncSession, Player], Awaitable[dict | web.StreamResponse | None]]


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status, headers=_SECURITY_HEADERS)


async def index(request: web.Request) -> web.Response:
    """Страница со ссылками на текущие версии app.js и app.css (v2.172.0)."""
    return web.Response(body=request.app[ASSETS_KEY].index_html(), content_type="text/html",
                        charset="utf-8", headers=_SECURITY_HEADERS)


async def static_asset(request: web.Request) -> web.Response:
    return request.app[ASSETS_KEY].response(request, request.match_info["name"])


def endpoint(handler: Handler) -> Callable[[web.Request], Awaitable[web.Response]]:
    """Общая обёртка: вход по initData, доступ по флагу, игрок по telegram_id,
    сессия без общего commit (пишет только `react` — сам). None от обработчика — 404."""
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
        # Запись счётчика — только после закрытия сессии запроса: иначе при общем
        # соединении (SQLite в памяти в тестах) закрытие сессии откатило бы её.
        pending = request.get(TRACK_REQUEST_KEY)
        if pending is not None:
            telegram_id_, screen_, boot_ms_ = pending
            if request.app[USAGE_KEY].record(telegram_id_, screen_):
                log_boot_time(screen_, boot_ms_)
        if payload is None:
            return _json_error(404, "Не найдено.")
        if isinstance(payload, web.StreamResponse):
            return payload
        return web.json_response(payload, headers=_SECURITY_HEADERS)
    return wrapped


async def _target(request: web.Request, session: AsyncSession) -> Player | None:
    try:
        player_id = int(request.match_info["player_id"])
    except (KeyError, ValueError):
        return None
    return (await session.execute(select(Player).where(Player.id == player_id))).scalar_one_or_none()


async def home(request, session, viewer):
    payload = await api.home_payload(session, viewer)
    if payload.get("empty"):
        return payload
    # v2.168.0: лента клуба вместо отдельных «свежих рекордов» и «ещё сегодня»,
    # «Ты сегодня» в карточке игрока, вход в «Итоги»
    # v2.171.0: первая порция ленты; остальное — /api/feed?offset=N
    page = await _feed_page(session, viewer, 0)
    payload["feed"], payload["feed_more"] = page["items"], page["more"]
    # v2.173.0: реакции и на карточке «Матч дня» — счётчики, как в ленте
    featured = payload.get("featured")
    if featured and featured.get("id"):
        by_match = await reactions.reactions_for(session, [featured["id"]], viewer.id)
        featured["reactions"] = reactions.feed_counts(by_match[featured["id"]])
    payload["me"]["today"] = await api_club.today_line(session, viewer)
    payload["links"].insert(0, {"route": "summary/day", "title": "Итоги", "value": "", "icon": "calendar"})
    # v2.170.0: «Когда играют» — всем, «Использование» — только администратору
    payload["links"].append({"route": "when", "title": "Когда играют", "value": "", "icon": "clock"})
    if is_admin(viewer.telegram_id):
        payload["links"].append({"route": "usage", "title": "Использование", "value": "", "icon": "chart-pie"})
    return payload


async def _feed_page(session, viewer, offset: int) -> dict:
    """Порция ленты клуба с реакциями (v2.169.0 — только счётчики)."""
    events = await api_club.feed_events(session)
    items = events[offset:offset + api_club.FEED_PAGE]
    feed_matches = [e["id"] for e in items if e.get("type") == "match"]
    by_match = await reactions.reactions_for(session, feed_matches, viewer.id)
    for e in items:
        if e.get("type") == "match":
            e["reactions"] = reactions.feed_counts(by_match.get(e["id"], []))
    return {"items": items, "more": len(events) > offset + len(items)}


async def feed(request, session, viewer):
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        return None
    if not 0 <= offset <= api_club.FEED_LIMIT:
        return None
    return await _feed_page(session, viewer, offset)


async def summary(request, session, viewer):
    payload = await api_club.summary_payload(session, request.match_info["kind"])
    if payload is not None and payload["kind"] == "year":
        # v2.170.0: «Твой год» и неформальные звания (голосование — в боте)
        payload.update(await api_insights.year_extras(session, viewer))
    return payload


async def when(request, session, viewer):
    return await api_insights.when_payload(session, viewer, request.query.get("who", "club"))


async def usage(request, session, viewer):
    """Не администратору — 404: экран не должен даже обнаруживаться."""
    if not is_admin(viewer.telegram_id):
        return None
    return await api_insights.usage_payload(session)


async def match_screen(request, session, viewer):
    try:
        match_id = int(request.match_info["match_id"])
    except ValueError:
        return None
    payload = await api_club.match_payload(session, match_id)
    if payload is not None:
        payload["reactions"] = (await reactions.reactions_for(
            session, [match_id], viewer.id, with_names=True))[match_id]
    return payload


async def react(request, session, viewer):
    """Поставить или снять реакцию на матч (v2.169.0). Никаких уведомлений."""
    try:
        match_id = int(request.match_info["match_id"])
        body = await request.json()
    except Exception:
        return None
    kind = str(body.get("kind", "")) if isinstance(body, dict) else ""
    result = await reactions.toggle_reaction(session, match_id, viewer, kind)
    return None if result is None else {"reactions": result}


async def chart(request, session, viewer):
    target = await _target(request, session)
    return await api_club.chart_payload(session, target) if target else None


async def me(request, session, viewer):
    """id зрителя — для вкладки «Я» (страница сама его не знает)."""
    return {"id": viewer.id, "name": viewer.display_name}


async def photo(request, session, viewer):
    target = await _target(request, session)
    bot = request.app.get(BOT_KEY)
    if target is None or bot is None:
        return None
    data = await request.app[PHOTOS_KEY].get(bot, target.telegram_id)
    if data is None:
        return None
    return web.Response(body=data, content_type="image/jpeg", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
    })


async def track(request, session, viewer):
    """Показан экран приложения — записать в счётчик (v2.166.0). Ответ сразу,
    запись фоном; неподходящее имя экрана не пишется."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False}
    screen = str(body.get("screen", "")) if isinstance(body, dict) else ""
    if not SCREEN_RE.match(screen):
        return {"ok": False}
    request[TRACK_REQUEST_KEY] = (viewer.telegram_id, screen, body.get("boot_ms"))
    return {"ok": True}


async def leaderboard(request, session, viewer):
    period = request.query.get("period", "")
    if period:
        return await api_club.period_leaderboard_payload(session, viewer, period)
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
    if target is None:
        return None
    payload = api.achievements_payload(target)
    # v2.168.0: редкость «есть у N из M», дата получения, три ближайшие с прогрессом
    extras = await api_club.achievements_extras(session, target)
    for cat in payload["categories"]:
        for item in cat["items"]:
            if "id" in item:
                item.update(extras["per_ach"].get(item["id"], {}))
                if not item["earned"]:
                    item.pop("date", None)
    payload["nearest"] = extras["nearest"]
    return payload


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


@web.middleware
async def revalidate_static(request: web.Request, handler):
    """Статика всегда с проверкой по ETag (v2.164.0). Без заголовка встроенный
    браузер Telegram кэширует app.js «по эвристике» и после выкладки показывает
    новый API со старой страницей. С `no-cache` запрос уходит на сервер, а
    неизменённый файл отдаётся ответом 304 без тела."""
    response = await handler(request)
    if request.path.startswith("/static/fonts/"):
        # Шрифт не меняется от релиза к релизу — месяц без перепроверки (v2.172.0)
        response.headers["Cache-Control"] = "public, max-age=2592000"
    elif request.path.startswith("/static/"):
        # app.js/app.css/icons.svg ставят заголовок сами (с версией — immutable)
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@web.middleware
async def compress_json(request: web.Request, handler):
    """Сжатие крупных ответов API gzip (v2.172.0): ачивки — 23 КБ, главная — 10 КБ."""
    response = await handler(request)
    if (isinstance(response, web.Response) and response.content_type == "application/json"
            and response.body is not None and len(response.body) >= COMPRESS_MIN_BYTES
            and accepts_gzip(request)):
        response.enable_compression(web.ContentCoding.gzip)
    return response


def create_app(bot_token: str, session_factory, bot=None) -> web.Application:
    """bot — экземпляр aiogram.Bot для фото игроков; без него /api/photo отвечает 404."""
    app = web.Application(middlewares=[revalidate_static, compress_json])
    app[BOT_TOKEN_KEY] = bot_token
    app[SESSION_FACTORY_KEY] = session_factory
    app[BOT_KEY] = bot
    app[PHOTOS_KEY] = PhotoCache()
    app[USAGE_KEY] = UsageWriter(session_factory)
    app[ASSETS_KEY] = Assets(STATIC_DIR)
    app.router.add_get("/", index)
    names = "|".join(n.replace(".", r"\.") for n in ASSET_TYPES)
    app.router.add_get("/static/{name:" + names + "}", static_asset)
    routes = [
        ("/api/home", home),
        ("/api/me", me),
        ("/api/leaderboard", leaderboard),
        ("/api/photo/{player_id}", photo),
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
        ("/api/summary/{kind}", summary),
        ("/api/match/{match_id}", match_screen),
        ("/api/player/{player_id}/chart", chart),
        ("/api/matches", matches),
        ("/api/throne", throne),
        ("/api/when", when),
        ("/api/feed", feed),
        ("/api/usage", usage),
    ]
    for path, handler in routes:
        app.router.add_get(path, endpoint(handler))
    app.router.add_post("/api/track", endpoint(track))
    app.router.add_post("/api/match/{match_id}/react", endpoint(react))
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def start_webapp(bot_token: str, session_factory, bot=None) -> web.AppRunner:
    """Запускает сервер рядом с поллингом бота; вернуть runner, чтобы закрыть."""
    host, port = webapp_listen()
    runner = web.AppRunner(create_app(bot_token, session_factory, bot), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info("Mini App: веб-сервер слушает %s:%s", host, port)
    return runner

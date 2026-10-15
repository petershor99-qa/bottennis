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
  GET /api/photo/{id}                       — своё фото игрока или аватар из Telegram (или 404)
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
  GET /api/month?ym=ГГГГММ                  — «Твой месяц» карточками (v2.177.0)
  GET /api/chronicle                        — летопись клуба (v2.177.0)
  GET /api/scores                           — счета клуба: частые счета, дьюсы (v2.177.0)
  GET /api/day?date=ГГГГ-ММ-ДД              — итоги дня на лету: табло, игрок дня, движение, матч дня… (v2.176.0)
  GET /api/match/{id}                       — экран матча (v2.168.0), с реакциями (v2.169.0)
  GET /api/player/{id}/chart                — график рейтинга по датам (v2.168.0)
  POST /api/match/{id}/react                — поставить/снять реакцию (v2.169.0)
  GET /api/feed?offset=N                    — лента клуба порциями (v2.171.0)
  GET /api/when?who=club|me                 — «Когда играют»: день недели × час (v2.170.0)
  GET /api/usage                            — «Использование», только администратор (v2.170.0)
  POST /api/track                           — счётчик показанных экранов (v2.166.0)
  POST /api/share                           — «Поделиться»: картинка игроку в чат с ботом (v2.176.0)
  POST /api/feed/react                      — реакция на ачивку или рекорд в ленте (v2.176.0)
  POST /api/client_error                    — ошибка JS на странице: в журнал (v2.177.0)
  POST /api/boot_fail                       — страница не запустилась: причина в лог (v2.175.0)
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
from bot.handlers.photo import ask_photo_from_app
from bot.utils import get_player
from bot.webapp import (
    api,
    api_chronicle,
    api_club,
    api_day,
    api_insights,
    api_month,
    api_personal,
    api_return,
    reactions,
)
from bot.webapp.assets import ASSET_TYPES, Assets, accepts_gzip
from bot.webapp.auth import WebAppAuthError, init_data_from_header, telegram_id_from_init_data
from bot.webapp.challenge_open import open_challenge
from bot.webapp.config import is_admin, is_webapp_allowed, webapp_listen
from bot.webapp.photos import PhotoCache, photo_etag
from bot.webapp.share import send_share
from bot.webapp.usage import SCREEN_RE, UsageWriter, client_name, log_boot_failure, log_boot_time, log_client_error

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "webapp" / "static"

BOT_TOKEN_KEY: web.AppKey[str] = web.AppKey("bot_token", str)
SESSION_FACTORY_KEY: web.AppKey = web.AppKey("session_factory")
BOT_KEY: web.AppKey = web.AppKey("bot")
# Хранилище FSM бота (v2.185.0): «Поставить фото» из приложения ставит ожидание фото
STORAGE_KEY: web.AppKey = web.AppKey("fsm_storage")
PHOTOS_KEY: web.AppKey[PhotoCache] = web.AppKey("photos", PhotoCache)
USAGE_KEY: web.AppKey[UsageWriter] = web.AppKey("usage", UsageWriter)
TRACK_REQUEST_KEY = "bottennis_track"  # что записать в счётчик после ответа
BOOT_FAIL_REQUEST_KEY = "bottennis_boot_fail"  # неудачный запуск — в app_boots после ответа (v2.180.0)
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
            telegram_id_, screen_, boot_ms_, info_ = pending
            if request.app[USAGE_KEY].record(telegram_id_, screen_):
                boot = log_boot_time(screen_, boot_ms_, info_)
                if boot:
                    request.app[USAGE_KEY].record_boot(*boot)
        failed_platform = request.get(BOOT_FAIL_REQUEST_KEY)
        if failed_platform is not None:
            request.app[USAGE_KEY].record_boot(0, failed_platform, failed=True)
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
    # v2.181.0: «Цели недели» — только в приложении, без уведомлений
    payload["goals"] = await api_return.goals_payload(session, viewer)
    payload["feed"], payload["feed_more"] = page["items"], page["more"]
    # v2.173.0: реакции и на карточке «Матч дня» — счётчики, как в ленте
    featured = payload.get("featured")
    if featured and featured.get("id"):
        by_match = await reactions.reactions_for(session, [featured["id"]], viewer.id)
        featured["reactions"] = reactions.feed_counts(by_match[featured["id"]])
    payload["me"]["today"] = await api_club.today_line(session, viewer)
    payload["links"].insert(0, {"route": "summary/day", "title": "Итоги", "value": "", "icon": "calendar"})
    # v2.177.0: «Твой месяц», летопись и счета клуба
    payload["links"].insert(1, {"route": "month", "title": "Твой месяц", "value": "", "icon": "sparkles"})
    payload["links"].append({"route": "chronicle", "title": "Летопись", "value": "", "icon": "book"})
    payload["links"].append({"route": "scores", "title": "Счета клуба", "value": "", "icon": "list-numbers"})
    # v2.170.0: «Когда играют» — всем, «Использование» — только администратору
    payload["links"].append({"route": "when", "title": "Когда играют", "value": "", "icon": "clock"})
    payload["links"].append({"route": "pairs", "title": "Кто с кем", "value": "", "icon": "users"})  # v2.183.0
    payload["links"].append({"route": "race", "title": "Гонка рейтинга", "value": "", "icon": "flag"})  # v2.184.0
    if is_admin(viewer.telegram_id):
        payload["links"].append({"route": "usage", "title": "Использование", "value": "", "icon": "chart-pie"})
    return payload


async def _feed_page(session, viewer, offset: int) -> dict:
    """Порция ленты клуба с реакциями (v2.169.0 — только счётчики)."""
    events = await api_club.feed_events(session)
    items = events[offset:offset + api_club.FEED_PAGE]
    feed_matches = [e["id"] for e in items if e.get("type") == "match"]
    by_match = await reactions.reactions_for(session, feed_matches, viewer.id)
    # v2.176.0: ачивки и рекорды — свои кнопки реакций прямо в ленте
    by_target = await reactions.feed_reactions_for(session, [e["target"] for e in items if e.get("target")], viewer.id)
    for e in items:
        if e.get("type") == "match":
            e["reactions"] = reactions.feed_counts(by_match.get(e["id"], []))
        elif e.get("target"):
            e["reactions"] = by_target.get(e["target"], [])
    return {"items": items, "more": len(events) > offset + len(items)}


async def feed(request, session, viewer):
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        return None
    if not 0 <= offset <= api_club.FEED_LIMIT:
        return None
    return await _feed_page(session, viewer, offset)


async def day(request, session, viewer):
    """Итоги дня на лету (v2.176.0): ?date=ГГГГ-ММ-ДД, без даты — последний игровой день."""
    return await api_day.day_payload(session, viewer, request.query.get("date") or None)


async def month(request, session, viewer):
    """«Твой месяц» (v2.177.0): ?ym=ГГГГММ, без него — текущий (или прошлый)."""
    return await api_month.month_payload(session, viewer, request.query.get("ym") or None)


async def chronicle(request, session, viewer):
    return await api_chronicle.chronicle_payload(session)


async def scores(request, session, viewer):
    return await api_chronicle.scores_payload(session)


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


async def share(request, session, viewer):
    """«Поделиться» (v2.176.0): картинка со страницы — игроку в чат с ботом."""
    bot = request.app.get(BOT_KEY)
    if bot is None:
        return None
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return _json_error(400, "Не удалось прочитать картинку.")
    result = await send_share(bot, viewer.telegram_id, str(body.get("kind", "")), body.get("image", ""))
    if result == "ok":
        return {"ok": True}
    messages = {"bad": (400, "Не удалось прочитать картинку."),
                "wait": (429, "Чуть позже — картинка только что отправлена."),
                "fail": (502, "Telegram не принял картинку. Попробуйте ещё раз.")}
    status, text = messages[result]
    return _json_error(status, text)


async def race(request, session, viewer):
    return await api_insights.race_payload(session)


async def pairs(request, session, viewer):
    return await api_insights.pairs_payload(session)


async def since(request, session, viewer):
    """«Пока тебя не было» (v2.181.0): ?t=<секунды UTC прошлого захода>."""
    try:
        ts = int(request.query.get("t", ""))
    except ValueError:
        return None
    return await api_return.since_payload(session, viewer, ts) if ts > 0 else None


async def challenge_open(request, session, viewer):
    """«Вызвать на матч» из приложения (v2.179.0): бот присылает экран вызова в чат."""
    bot = request.app.get(BOT_KEY)
    if bot is None:
        return None
    try:
        body = await request.json()
    except Exception:
        body = None
    raw = body.get("opponent_id") if isinstance(body, dict) else None
    if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int)):
        return _json_error(400, "Не удалось открыть вызов.")
    result = await open_challenge(bot, session, viewer, raw)
    if result == "ok":
        request[TRACK_REQUEST_KEY] = (viewer.telegram_id, "challenge_open", None, None)
        return {"ok": True}
    messages = {"bad": (400, "Не удалось открыть вызов."),
                "wait": (429, "Экран вызова уже в чате с ботом."),
                "fail": (502, "Бот не смог ответить. Откройте чат с ботом.")}
    status, text = messages[result]
    return _json_error(status, text)


async def photo_open(request, session, viewer):
    """«Поставить фото» из приложения (v2.185.0): бот присылает просьбу в чат."""
    bot = request.app.get(BOT_KEY)
    if bot is None:
        return None
    result = await ask_photo_from_app(bot, request.app.get(STORAGE_KEY), viewer)
    if result == "ok":
        request[TRACK_REQUEST_KEY] = (viewer.telegram_id, "photo_open", None, None)
        return {"ok": True}
    if result == "wait":
        return _json_error(429, "Просьба о фото уже в чате с ботом.")
    return _json_error(502, "Бот не смог ответить. Откройте чат с ботом.")


async def feed_react(request, session, viewer):
    """Реакция на ачивку или рекорд в ленте (v2.176.0). Без уведомлений."""
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    result = await reactions.toggle_feed_reaction(session, str(body.get("target", "")), viewer, str(body.get("kind", "")))
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
    data = await request.app[PHOTOS_KEY].get(bot, target.telegram_id, target.photo_file_id)
    if data is None:
        return None
    # v2.174.0: фото можно сменить через бота — браузер каждый раз сверяет версию
    # (ETag), ответ 304 без тела; раньше он час держал старое фото
    etag = photo_etag(data)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers=headers)
    return web.Response(body=data, content_type="image/jpeg", headers=headers)


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
    request[TRACK_REQUEST_KEY] = (viewer.telegram_id, screen, body.get("boot_ms"), body)
    return {"ok": True}


async def client_error(request, session, viewer):
    """Ошибка JS на странице после запуска (v2.177.0): в журнал сервиса."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return {"ok": log_client_error(viewer.telegram_id, body)}


async def boot_fail(request, session, viewer):
    """Страница не дошла до первого экрана (v2.175.0): причина — в лог сервиса."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    logged = log_boot_failure(viewer.telegram_id, body)
    if logged:
        # v2.180.0: и в app_boots — «Использование» показывает число неудачных запусков
        platform = body.get("platform") if isinstance(body, dict) else None
        request[BOOT_FAIL_REQUEST_KEY] = "" if platform is None else client_name(platform)
    return {"ok": logged}


async def leaderboard(request, session, viewer):
    period = request.query.get("period", "")
    if period:
        return await api_club.period_leaderboard_payload(session, viewer, period)
    return await api.leaderboard_payload(session, viewer.id)


async def player(request, session, viewer):
    target = await _target(request, session)
    if target is None:
        return None
    payload = await api.player_payload(session, target, viewer)
    # v2.174.0: в своём профиле — ссылка в бота поставить или сменить фото
    bot = request.app.get(BOT_KEY)
    if target.id == viewer.id and bot is not None:
        try:
            username = (await bot.me()).username
        except Exception:
            logger.warning("Mini App: не удалось узнать имя бота для ссылки на фото", exc_info=True)
            username = None
        if username:
            payload["head"]["photo_link"] = f"https://t.me/{username}?start=photo"
    # v2.177.0: личные рекорды, коронный счёт, главный соперник; в чужом
    # профиле — сразу шанс против него (раньше только на «Ты против …»)
    payload["personal"] = await api_personal.personal_payload(session, target)
    if target.id != viewer.id and not payload.get("empty"):
        payload["forecast"] = await api._forecast(session, viewer, target)
    return payload


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


def create_app(bot_token: str, session_factory, bot=None, storage=None) -> web.Application:
    """bot — экземпляр aiogram.Bot для фото игроков; без него /api/photo отвечает 404.
    storage — хранилище FSM диспетчера (ожидание фото из приложения)."""
    # client_max_size: картинка «Поделиться» (v2.176.0) приходит base64 до ~2,7 МБ
    app = web.Application(middlewares=[revalidate_static, compress_json], client_max_size=3 * 1024 * 1024)
    app[BOT_TOKEN_KEY] = bot_token
    app[SESSION_FACTORY_KEY] = session_factory
    app[BOT_KEY] = bot
    app[STORAGE_KEY] = storage
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
        ("/api/day", day),
        ("/api/month", month),
        ("/api/chronicle", chronicle),
        ("/api/scores", scores),
        ("/api/match/{match_id}", match_screen),
        ("/api/player/{player_id}/chart", chart),
        ("/api/matches", matches),
        ("/api/throne", throne),
        ("/api/when", when),
        ("/api/feed", feed),
        ("/api/usage", usage),
        ("/api/since", since),
        ("/api/pairs", pairs),
        ("/api/race", race),
    ]
    for path, handler in routes:
        app.router.add_get(path, endpoint(handler))
    app.router.add_post("/api/track", endpoint(track))
    app.router.add_post("/api/boot_fail", endpoint(boot_fail))
    app.router.add_post("/api/client_error", endpoint(client_error))
    app.router.add_post("/api/feed/react", endpoint(feed_react))
    app.router.add_post("/api/share", endpoint(share))
    app.router.add_post("/api/challenge/open", endpoint(challenge_open))
    app.router.add_post("/api/photo/open", endpoint(photo_open))
    app.router.add_post("/api/match/{match_id}/react", endpoint(react))
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def start_webapp(bot_token: str, session_factory, bot=None, storage=None) -> web.AppRunner:
    """Запускает сервер рядом с поллингом бота; вернуть runner, чтобы закрыть."""
    host, port = webapp_listen()
    runner = web.AppRunner(create_app(bot_token, session_factory, bot, storage), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info("Mini App: веб-сервер слушает %s:%s", host, port)
    return runner

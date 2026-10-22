"""Превью Mini App на настоящем aiohttp-сервере с тестовой БД (не прод).

Поднимает веб-часть приложения (bot/webapp) на временной SQLite с придуманными
данными: 6 игроков, около 100 матчей за 85 дней, два матча сегодня, трон и
прошлое правление, свежий рекорд клуба. Telegram заменён заглушкой со
сгенерированной подписью initData — страница ведёт себя как внутри Telegram.
Ни репозиторий, ни прод не трогает. Фото игроков в превью нет (нет бота) —
показываются буквы.

Запуск:
  PYTHONIOENCODING=utf-8 py -3.13 scripts/preview_miniapp.py [порт]
Открыть: http://127.0.0.1:8770/preview         — светлая тема
         http://127.0.0.1:8770/preview?dark=1  — тёмная
Экран — после «#»: #/, #/rating, #/player/1, #/player/1/achievements, #/h2h/2 …
Зритель (вкладка «Я») — игрок 1, «Алиса».
"""
import asyncio
import hashlib
import hmac
import json
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
TMP = tempfile.mkdtemp(prefix="miniapp_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP}/preview.db"
os.environ["WEBAPP_URL"] = "https://preview.example/"
os.environ["ADMIN_ID"] = "1001"
os.environ.setdefault("WEBAPP_AUDIENCE", "all")   # как на проде: приложение открыто всем игрокам (v2.208.0)
sys.path.insert(0, REPO)
os.chdir(TMP)

from aiohttp import web  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bot.db.database import async_session, engine  # noqa: E402
from bot.db.models import Base, ChampionReign, ClubRecordPing, Match, MatchStatus, Player, UsageEvent  # noqa: E402
from bot.services.rating import calculate_rating_change  # noqa: E402
from bot.webapp.server import create_app  # noqa: E402

TOKEN = "123456:PREVIEW"
NAMES = ["Алиса", "Боб", "Вика", "Гоша", "Дима", "Ева"]
# У первого игрока («Алиса», зритель) много ачивок разных категорий и ступеней
# лестниц — чтобы в превью было видно иконки и цвета уровней.
RICH_ACHIEVEMENTS = [
    "press_start", "first_blood", "beginners_luck", "hat_trick", "im_on_fire", "fatality", "night_owl",
    "comeback", "deuce_maker", "marathon", "titans", "fifty", "veteran", "legend", "point_saver",
    "sturdy_grinder", "set_sniper", "diplomat", "collector", "highlander", "fk_tyumen", "weekend_warrior",
]


def init_data(user_id: int) -> str:
    fields = {"auth_date": str(int(time.time())), "query_id": "AAE",
              "user": json.dumps({"id": user_id, "first_name": "Превью"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def set_score(rng):
    loser = rng.randint(3, 13)
    if loser >= 10:
        return {"w": loser + 2, "l": loser}
    return {"w": 11, "l": loser}


VARIANT = os.environ.get("PREVIEW_VARIANT", "default")   # default | tiny | empty (v2.208.0)


async def seed_variant():
    """Крайние случаи клуба: tiny — трое, один матч, чемпиона нет; empty — трое без матчей.
    Везде «Новичок» (1099) зарегистрирован и не играл."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as s:
        alice = Player(telegram_id=1001, display_name="Алиса", rating=1000.0, peak_rating=1000.0,
                       achievements=json.dumps(["press_start"]), backfill_version=0)
        bob = Player(telegram_id=1002, display_name="Боб", rating=1000.0, peak_rating=1000.0,
                     achievements="[]", backfill_version=0)
        newbie = Player(telegram_id=1099, display_name="Новичок", rating=1000.0, peak_rating=1000.0,
                        achievements="[]", backfill_version=0)
        s.add_all([alice, bob, newbie])
        await s.flush()
        if VARIANT == "tiny":
            when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
            data = [{"w": 11, "l": 6}, {"w": 11, "l": 8}]
            delta = calculate_rating_change(alice.rating, bob.rating, data)
            alice.rating, bob.rating = round(alice.rating + delta, 1), round(max(1000.0, bob.rating - delta), 1)
            alice.peak_rating = alice.rating
            s.add(Match(challenger_id=alice.id, challenged_id=bob.id, status=MatchStatus.completed,
                        winner_id=alice.id, sets_data=data, rating_change=delta, created_at=when,
                        accepted_at=when, completed_at=when))
        await s.commit()


async def seed():
    if VARIANT in ("tiny", "empty"):
        await seed_variant()
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    rng = random.Random(7)
    async with async_session() as s:
        players = [Player(telegram_id=1001 + i, display_name=n, rating=1000.0, peak_rating=1000.0,
                          achievements=json.dumps(RICH_ACHIEVEMENTS if i == 0 else ["press_start", "first_blood", "hat_trick"][: 1 + i % 3]),
                          backfill_version=0) for i, n in enumerate(NAMES)]
        s.add_all(players)
        await s.flush()
        skill = {p.id: rng.uniform(0.3, 0.7) for p in players}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start = now - timedelta(days=85)
        for k in range(95):
            a, b = rng.sample(players, 2)
            when = start + timedelta(days=k * 0.9, hours=rng.randint(9, 17))
            p_a = skill[a.id] / (skill[a.id] + skill[b.id])
            n_sets = rng.choice([1, 2, 3, 3, 3])
            sets = []
            wins_a = 0
            for _ in range(n_sets):
                sc = set_score(rng)
                if rng.random() < p_a:
                    wins_a += 1
                    sets.append(("a", sc))
                else:
                    sets.append(("b", sc))
            wins_b = n_sets - wins_a
            if wins_a == wins_b:
                winner = None
                data = [{"w": sc["w"], "l": sc["l"]} if who == "a" else {"w": sc["l"], "l": sc["w"]} for who, sc in sets]
                delta = 0.0
            else:
                winner, loser = (a, b) if wins_a > wins_b else (b, a)
                wk = "a" if winner is a else "b"
                data = [{"w": sc["w"], "l": sc["l"]} if who == wk else {"w": sc["l"], "l": sc["w"]} for who, sc in sets]
                delta = calculate_rating_change(winner.rating, loser.rating, data)
                winner.rating = round(winner.rating + delta, 1)
                loser.rating = round(max(1000.0, loser.rating - delta), 1)
                winner.peak_rating = max(winner.peak_rating, winner.rating)
            s.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed,
                        winner_id=winner.id if winner else None, sets_data=data, rating_change=delta,
                        created_at=when, accepted_at=when, completed_at=when + timedelta(minutes=20)))
        for a, b, sets, win_a in ((players[0], players[1], [{"w": 11, "l": 13}, {"w": 12, "l": 10}, {"w": 11, "l": 4}], True),
                                  (players[2], players[3], [{"w": 11, "l": 7}, {"w": 11, "l": 9}], False)):
            when = now - timedelta(minutes=40)
            winner, loser = (a, b) if win_a else (b, a)
            data = sets if win_a else [{"w": x["w"], "l": x["l"]} for x in sets]
            delta = calculate_rating_change(winner.rating, loser.rating, data)
            winner.rating = round(winner.rating + delta, 1)
            loser.rating = round(max(1000.0, loser.rating - delta), 1)
            s.add(Match(challenger_id=a.id, challenged_id=b.id, status=MatchStatus.completed, winner_id=winner.id,
                        sets_data=data, rating_change=delta, created_at=when, accepted_at=when, completed_at=when))
        s.add(ClubRecordPing(kind="streak", player_id=players[2].id, value=7, pinged_at=now - timedelta(days=1)))
        champ = max(players, key=lambda p: p.rating)
        champ.is_champion = True
        s.add(ChampionReign(player_id=players[3].id, started_at=start + timedelta(days=10),
                            ended_at=start + timedelta(days=30)))
        s.add(ChampionReign(player_id=champ.id, started_at=start + timedelta(days=30)))
        # Счётчик экранов за 30 дней — для экрана «Использование» (v2.170.0)
        actions = ["menu_leaderboard", "menu_stats", "challenge", "result_confirm", "app_home",
                   "app_rating", "app_me", "app_player", "app_summary_day", "app_match", "app_when"]
        for _ in range(400):
            s.add(UsageEvent(user_id=rng.choice([1001, 2002, 3003, 4004, 5005]), action=rng.choice(actions),
                             created_at=now - timedelta(minutes=rng.randint(0, 30 * 24 * 60))))
        await s.commit()
        ids = (await s.execute(select(Player.id, Player.display_name))).all()
        print("players:", ids)


PREVIEW_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Превью</title>
<script>
// ?tg=late — скрипт Telegram приходит через 3 с, ?tg=none — не приходит вовсе
// (как у игрока, у которого не открывается telegram.org; v2.175.0). В обоих
// случаях подпись для входа лежит в адресе запуска, как в настоящем Telegram.
var dark = location.search.indexOf("dark") >= 0;
var mode = (location.search.match(/tg=(late|none)/) || [])[1] || "now";
var INIT = %s;
function makeTelegram() {
  return { WebApp: { colorScheme: dark ? "dark" : "light", initData: INIT, themeParams: {},
    platform: "tdesktop", version: "7.0",
    ready(){}, expand(){}, onEvent(){}, setHeaderColor(){}, setBackgroundColor(){},
    openTelegramLink(u){ window.__openedLink = u; },
    HapticFeedback: { selectionChanged(){}, impactOccurred(){} },
    // v2.208.0: «Назад» записывает состояние — проверка, что видимая кнопка что-то делает
    BackButton: { visible: false, show(){ this.visible = true; }, hide(){ this.visible = false; }, onClick(f){ this.cb = f; } } } };
}
if (mode === "now") {
  window.Telegram = makeTelegram();
} else if (location.hash.indexOf("tgWebAppData") < 0 && location.hash.indexOf("#/") !== 0) {
  var theme = JSON.stringify({ bg_color: dark ? "#17212b" : "#ffffff" });
  location.hash = "tgWebAppData=" + encodeURIComponent(INIT) + "&tgWebAppVersion=7.0&tgWebAppPlatform=tdesktop"
    + "&tgWebAppThemeParams=" + encodeURIComponent(theme);
}
if (mode === "late") setTimeout(function () {
  window.Telegram = makeTelegram();
  if (window.__clubOnTelegram) window.__clubOnTelegram();
}, 3000);
</script>
<link rel="stylesheet" href="static/app.css"></head>
<body><main class="screen" id="app"></main><nav class="tabbar" id="tabbar"></nav><script src="static/app.js"></script></body></html>"""


async def preview(request):
    # ?u=<telegram_id> — открыть приложение от лица другого игрока (v2.208.0): новичок,
    # незарегистрированный (7777) и т.п. По умолчанию — «Алиса», зритель превью
    try:
        viewer = int(request.query.get("u", 1001))
    except ValueError:
        viewer = 1001
    return web.Response(text=PREVIEW_HTML % json.dumps(init_data(viewer)), content_type="text/html")


class _SilentBot:
    """Подставной бот: рассылки сводок проходят целиком, но никуда не уходят;
    фото у игроков нет (буквы), имя бота — для ссылки «Поставить фото» (v2.174.0)."""

    async def send_message(self, *args, **kwargs):
        return None

    async def get_user_profile_photos(self, *args, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(photos=[])

    async def me(self):
        from types import SimpleNamespace
        return SimpleNamespace(username="preview_bot")

    async def send_photo(self, chat_id, photo, caption=None, **kwargs):
        """«Поделиться» (v2.176.0): картинка сохраняется рядом с временной базой."""
        path = os.path.join(tempfile.gettempdir(), f"preview_share_{photo.filename}")
        with open(path, "wb") as f:
            f.write(photo.data)
        print("share:", path, caption, flush=True)


async def seed_digests():
    """Настоящие сводки дня, недели и месяца — для экрана «Итоги» (снимки)."""
    from bot import scheduler

    for job in (scheduler.send_daily_summary, scheduler.send_weekly_digest, scheduler.send_monthly_summary):
        try:
            await job(_SilentBot())
        except Exception as exc:  # превью не должно падать из-за одной сводки
            print("сводка не собралась:", job.__name__, exc)


async def main():
    await seed()
    await seed_digests()
    app = create_app(TOKEN, async_session, _SilentBot())
    app.router.add_get("/preview", preview)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    print(f"ready http://127.0.0.1:{PORT}/preview", flush=True)
    await asyncio.Event().wait()


asyncio.run(main())

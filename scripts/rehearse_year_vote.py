"""Репетиция голосования «Итоги года» — сквозной прогон на временной БД.

Не трогает ни репозиторий, ни прод: БД во временной папке, Telegram заменён
записывающей сессией, время — подменённые часы. Запуск:
  PYTHONIOENCODING=utf-8 py -3.13 scripts/rehearse_year_vote.py
  (ONLY=latency — только замер задержек; NO_USAGE=1 — без UsageMiddleware)
"""
import asyncio
import itertools
import os
import re
import sys
import tempfile
from datetime import datetime as RealDT, timedelta, timezone
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="rehearsal_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP}/rehearsal.db"
os.environ["ADMIN_ID"] = "999001"
os.environ.pop("BOT_TOKEN", None)
os.chdir(TMP)                    # чтобы случайно не подхватить чужой ./bottennis.db или .env
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import logging  # noqa: E402

logging.disable(logging.CRITICAL)  # тишина от логов бота; результаты печатаем сами

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update, User  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import bot.scheduler as sched  # noqa: E402
from bot.db.database import async_session, engine, init_db  # noqa: E402
from bot.db.models import Match, MatchStatus, Player, YearVote  # noqa: E402
from bot.handlers.admin import router as admin_router  # noqa: E402
from bot.handlers.challenge import router as challenge_router  # noqa: E402
from bot.handlers.history import router as history_router  # noqa: E402
from bot.handlers.leaderboard import router as leaderboard_router  # noqa: E402
from bot.handlers.match_result import router as match_result_router  # noqa: E402
from bot.handlers.notifications import router as notifications_router  # noqa: E402
from bot.handlers.profile import router as profile_router  # noqa: E402
from bot.handlers.start import router as start_router  # noqa: E402
from bot.handlers.year_vote import router as year_vote_router  # noqa: E402
from bot.middleware import DatabaseMiddleware, UsageMiddleware  # noqa: E402
from bot.services.year_vote import YEAR_VOTE_NOMINATIONS  # noqa: E402

UTC = timezone.utc
MSK = timezone(timedelta(hours=3))

# ── Часы ──────────────────────────────────────────────────────────────────────

class Clock:
    now = RealDT(2026, 12, 1, tzinfo=UTC)


class FakeDT(RealDT):
    @classmethod
    def now(cls, tz=None):
        return Clock.now.astimezone(tz) if tz else Clock.now.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return Clock.now.replace(tzinfo=None)


for _name, _mod in list(sys.modules.items()):
    if _name.startswith("bot") and getattr(_mod, "datetime", None) is RealDT:
        _mod.datetime = FakeDT


def at(day, hour, minute=0, second=0):
    """Поставить часы на МСК-время декабря 2026."""
    Clock.now = RealDT(2026, 12, day, hour, minute, second, tzinfo=MSK).astimezone(UTC)


# ── Telegram-заглушка ─────────────────────────────────────────────────────────

class RecSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []        # (имя метода, метод)
        self.blocked = set()   # chat_id, заблокировавшие бота

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method))
        chat_id = getattr(method, "chat_id", None)
        if name == "SendMessage":
            if chat_id in self.blocked:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            return Message(message_id=len(self.calls), date=Clock.now, chat=Chat(id=chat_id, type="private"), text=method.text)
        if name == "EditMessageText":
            return Message(message_id=method.message_id or 1, date=Clock.now, chat=Chat(id=chat_id, type="private"), text=method.text)
        return True


session = RecSession()
bot = Bot(token="123456:REHEARSAL", session=session)
dp = Dispatcher(storage=MemoryStorage())
dp.update.middleware(DatabaseMiddleware(async_session))
if not os.environ.get("NO_USAGE"):
    dp.callback_query.middleware(UsageMiddleware("callback"))
    dp.message.middleware(UsageMiddleware("command"))
dp.message.filter(F.chat.type == "private")
dp.callback_query.filter(F.message.chat.type == "private")
for r in (admin_router, start_router, leaderboard_router, profile_router, history_router,
          challenge_router, match_result_router, year_vote_router, notifications_router):
    dp.include_router(r)

_ids = itertools.count(1)


async def tap(tg_id, data, msg_id=100):
    n = next(_ids)
    cb = CallbackQuery(
        id=str(n), from_user=User(id=tg_id, is_bot=False, first_name="x"), chat_instance="ci", data=data,
        message=Message(message_id=msg_id, date=Clock.now, chat=Chat(id=tg_id, type="private"), text="screen"),
    )
    before = len(session.calls)
    err = None
    t0 = asyncio.get_event_loop().time()
    try:
        await asyncio.wait_for(dp.feed_update(bot, Update(update_id=n, callback_query=cb)), timeout=20)
    except asyncio.TimeoutError as e:
        err = e
    except Exception as e:  # noqa: BLE001
        err = e
    elapsed = asyncio.get_event_loop().time() - t0
    new = [m for _, m in session.calls[before:]]
    answers = [m for m in new if type(m).__name__ == "AnswerCallbackQuery"]
    edits = [m for m in new if type(m).__name__ == "EditMessageText"]
    return SimpleNamespace(
        answer=answers[-1] if answers else None, edit=edits[-1] if edits else None, err=err, elapsed=elapsed,
    )


# ── Проверки ──────────────────────────────────────────────────────────────────

RESULTS = []
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote"}
TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")


def html_problems(text):
    probs, stack = [], []
    for m in TAG_RE.finditer(text):
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag not in ALLOWED_TAGS:
            probs.append(f"неподдерживаемый тег <{tag}>")
        elif closing:
            if not stack or stack.pop() != tag:
                probs.append(f"несбалансированный </{tag}>")
        else:
            stack.append(tag)
    if stack:
        probs.append(f"не закрыты теги: {stack}")
    rest = TAG_RE.sub("", text)
    if "<" in rest:
        probs.append("голый '<' (Telegram не распарсит)")
    if re.search(r"&(?!(amp|lt|gt|quot|#\d+);)", rest):
        probs.append("голый '&' (Telegram не распарсит)")
    if len(text) > 4096:
        probs.append(f"длина {len(text)} > 4096")
    return probs


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("  PASS " if ok else "  FAIL ") + name + (f"  — {detail}" if detail and not ok else ""))


def sent_texts(since):
    return [(m.chat_id, m.text, m.reply_markup) for n, m in session.calls[since:] if n == "SendMessage"]


# ── Сидинг ────────────────────────────────────────────────────────────────────

PLAYERS = {  # ключ: (telegram_id, имя)
    "anna": (1001, "Анна"), "bob": (1002, "Боб & Ко"), "carol": (1003, "Кэрол <3"),
    "dima": (1004, "Дима"), "eva": (1005, "Ева"), "fedor": (1006, "Фёдор"),
    "galya": (1007, "Галя"), "block": (1008, "Блок"),
}
P = {}  # ключ -> Player.id (в БД)


def mk_match(a, b, winner, when_utc):
    return Match(
        challenger_id=P[a], challenged_id=P[b], status=MatchStatus.completed, winner_id=P[winner],
        sets_data=[{"w": 11, "l": 5}, {"w": 11, "l": 7}], rating_change=8.0, completed_at=when_utc,
    )


async def seed():
    async with async_session() as s:
        for key, (tg, name) in PLAYERS.items():
            p = Player(telegram_id=tg, display_name=name, rating=1000.0, achievements="[]", backfill_version=0)
            s.add(p)
            await s.flush()
            P[key] = p.id
        d = lambda *a: RealDT(*a)  # noqa: E731
        s.add_all([
            mk_match("anna", "bob", "anna", d(2026, 2, 3, 10)),
            mk_match("bob", "carol", "carol", d(2026, 4, 9, 11)),
            mk_match("carol", "dima", "dima", d(2026, 6, 1, 12)),
            mk_match("dima", "anna", "anna", d(2026, 8, 20, 9)),
            mk_match("anna", "block", "anna", d(2026, 9, 5, 9)),
            # Ева: единственный матч — 1 января 2026 00:30 МСК = 31.12.2025 21:30 UTC (годовая граница, внутри)
            mk_match("eva", "anna", "eva", d(2025, 12, 31, 21, 30)),
            # Фёдор: единственный матч — 31.12.2025 23:30 МСК = 20:30 UTC (прошлый год, снаружи)
            mk_match("fedor", "dima", "dima", d(2025, 12, 31, 20, 30)),
        ])
        await s.commit()


async def votes_count(voter=None):
    async with async_session() as s:
        q = select(func.count()).select_from(YearVote).where(YearVote.year == 2026)
        if voter:
            q = q.where(YearVote.voter_id == P[voter])
        return (await s.execute(q)).scalar()


def nom_ids():
    return [n.id for n in YEAR_VOTE_NOMINATIONS]


# ── Сценарий ──────────────────────────────────────────────────────────────────

async def main():
    await init_db()
    await seed()
    eligible = {"anna", "bob", "carol", "dima", "eva", "block"}

    if os.environ.get("ONLY") == "latency":
        from bot.db.models import UsageEvent
        at(22, 12, 0)

        async def usage_count(action):
            async with async_session() as sx:
                return (await sx.execute(select(func.count()).select_from(UsageEvent).where(UsageEvent.action == action))).scalar()

        print("\n[LATENCY] одиночные нажатия, без конкуренции")
        for label, data in [
            ("чтение: открыть бюллетень (yv_open)", "yv_open"),
            ("чтение: экран номинации (yv_nom_gentleman)", "yv_nom_gentleman"),
            ("ЗАПИСЬ: голос (yv_pick_gentleman_X)", f"yv_pick_gentleman_{P['bob']}"),
            ("ЗАПИСЬ: смена голоса", f"yv_pick_gentleman_{P['carol']}"),
        ]:
            action = "yv_open" if data == "yv_open" else ("yv_nom_" if data.startswith("yv_nom") else "yv_pick_")
            from bot.services.usage import normalize_action
            norm = normalize_action(data)
            before = await usage_count(norm)
            r = await tap(1001, data)
            after = await usage_count(norm)
            print(f"     {label:48s} {r.elapsed:5.2f} с | событие в /usage записано: {'да' if after == before + 1 else 'НЕТ'} | ошибка: {type(r.err).__name__ if r.err else '—'}")
        await engine.dispose()
        return

    print("\n[1] Расписание")
    s = sched.setup_scheduler(bot)
    ref = RealDT(2026, 10, 7, tzinfo=UTC)
    dec = {}
    for job in s.get_jobs():
        nxt = job.trigger.get_next_fire_time(None, ref)
        if nxt and nxt.astimezone(MSK).month == 12 and nxt.astimezone(MSK).day >= 21:
            dec[job.id] = (nxt.astimezone(MSK), job.func.__name__)
    for jid, (t, fn) in sorted(dec.items(), key=lambda kv: kv[1][0]):
        print(f"     {jid:20s} {fn:28s} {t:%d.%m %H:%M} МСК")
    check("приглашение 21.12 10:00", dec.get("year_vote_invite", (None,))[0] and dec["year_vote_invite"][0].strftime("%d.%m %H:%M") == "21.12 10:00")
    check("напоминание 29.12 10:00", dec.get("year_vote_reminder", (None,))[0] and dec["year_vote_reminder"][0].strftime("%d.%m %H:%M") == "29.12 10:00")
    check("итоги года + результаты одной джобой 30.12 12:00",
          "yearly_summary" in dec and dec["yearly_summary"][0].strftime("%d.%m %H:%M") == "30.12 12:00"
          and dec["yearly_summary"][1] == "send_year_end_combo")
    check("в конце декабря нет лишних джоб", set(dec) == {"year_vote_invite", "year_vote_reminder", "yearly_summary"}, str(set(dec)))

    print("\n[2] До открытия")
    at(20, 23, 59)
    r = await tap(1001, "yv_open")
    check("20.12 23:59 — голосование закрыто", r.answer is not None and "закрыто" in (r.answer.text or "") and r.err is None)
    at(21, 9, 59, 59)
    r = await tap(1001, "yv_open")
    check("21.12 09:59:59 — ещё закрыто", r.answer is not None and "закрыто" in (r.answer.text or ""))

    print("\n[3] Приглашение 21.12 10:00 (Блок заблокировал бота)")
    at(21, 10, 0, 0)
    session.blocked.add(PLAYERS["block"][0])
    mark = len(session.calls)
    try:
        await sched.send_year_vote_invitations(bot)
        crashed = None
    except Exception as e:  # noqa: BLE001
        crashed = e
    msgs = sent_texts(mark)
    got = {chat for chat, _, _ in msgs}
    check("рассылка не упала из-за заблокировавшего", crashed is None, repr(crashed))
    want = {PLAYERS[k][0] for k in eligible}
    check("приглашения ушли ровно допущенным", got == want, f"получили {sorted(got)}, ожидали {sorted(want)}")
    check("Фёдору (матч в 2025) и Гале (0 матчей) приглашения нет", not ({1006, 1007} & got))
    check("кнопка «Голосовать» ведёт на yv_open", all(
        k is not None and k.inline_keyboard[0][0].callback_data == "yv_open" for _, _, k in msgs))
    probs = [p for _, t, _ in msgs for p in html_problems(t)]
    check("HTML приглашения корректен", not probs, str(probs))
    print("     текст:", msgs[0][1].replace("\n", " | ")[:200])

    print("\n[3a] Допуск через реальный диспетчер")
    r = await tap(1006, "yv_open")
    check("Фёдор (только матч 2025) — отказ", r.answer is not None and "не сыграл" in (r.answer.text or ""))
    r = await tap(1007, "yv_open")
    check("Галя (0 матчей) — отказ", r.answer is not None and "не сыграл" in (r.answer.text or ""))
    r = await tap(1005, "yv_open")
    check("Ева (матч 01.01 00:30 МСК) — допущена", r.edit is not None and r.err is None)
    r = await tap(5555, "yv_open")
    check("незнакомый пользователь — просьба /start", r.answer is not None and "/start" in (r.answer.text or ""))

    print("\n[4] Голосование")
    r = await tap(1001, "yv_open")
    check("бюллетень открылся, без ошибок", r.edit is not None and r.err is None and not html_problems(r.edit.text), str(r.err))
    r = await tap(1001, f"yv_nom_{nom_ids()[0]}")
    labels = [b.text.replace("✅ ", "") for row in r.edit.reply_markup.inline_keyboard for b in row]
    check("кандидаты: нет себя, нет Фёдора и Гали, есть Ева",
          "Анна" not in labels and "Фёдор" not in labels and "Галя" not in labels and "Ева" in labels, str(labels))
    r = await tap(1001, f"yv_pick_{nom_ids()[0]}_{P['anna']}")
    check("голос за себя отклонён", r.answer is not None and "Некорректные" in (r.answer.text or "") and await votes_count("anna") == 0)
    r = await tap(1001, f"yv_pick_{nom_ids()[0]}_{P['galya']}")
    check("голос за недопущенного отклонён", await votes_count("anna") == 0)
    for bad in ("yv_pick_gentleman", "yv_pick_unknown_1", "yv_nom_unknown", "yv_pick_gentleman_abc"):
        r = await tap(1001, bad)
        check(f"мусорный callback '{bad}' — алерт, без падения", r.err is None and r.answer is not None, repr(r.err))

    # раздаём голоса: каждый допущенный голосует «по кругу» за остальных
    order = ["anna", "bob", "carol", "dima", "eva"]
    plan = {}
    for i, voter in enumerate(order):
        others = [k for k in order if k != voter]
        for j, nid in enumerate(nom_ids()):
            if nid == "toughest":
                continue                      # в этой номинации намеренно никто не голосует
            if nid == "magnet":               # ничья: Анна и Боб получают по 2
                nominee = "anna" if voter in ("bob", "carol", "dima", "eva")[:2] else "bob"
                nominee = nominee if nominee != voter else "carol"
            else:
                nominee = others[(i + j) % len(others)]
            plan[(voter, nid)] = nominee
    errs = []
    for (voter, nid), nominee in plan.items():
        tg = PLAYERS[voter][0]
        await tap(tg, f"yv_nom_{nid}")
        r = await tap(tg, f"yv_pick_{nid}_{P[nominee]}")
        if r.err or r.answer is None or "учтён" not in (r.answer.text or ""):
            errs.append((voter, nid, repr(r.err)))
    check("все голоса приняты", not errs, str(errs[:3]))
    total = len([1 for _ in plan])
    check("в БД ровно столько голосов, сколько отдано", await votes_count() == total, f"{await votes_count()} != {total}")

    # смена голоса не плодит строки
    before = await votes_count("anna")
    nid = nom_ids()[0]
    await tap(1001, f"yv_pick_{nid}_{P['dima']}")
    await tap(1001, f"yv_pick_{nid}_{P['eva']}")
    check("смена голоса — строка обновляется, не добавляется", await votes_count("anna") == before)
    r = await tap(1001, "yv_open")
    check("бюллетень показывает обновлённый выбор и экранирует имена",
          "Ева" in r.edit.text and not html_problems(r.edit.text))
    r = await tap(1002, "yv_open")
    check("имя «Боб & Ко» в бюллетене Анны/Кэрол экранировано",
          not html_problems((await tap(1003, "yv_open")).edit.text))

    print("\n[4a] Двойной тап (два одновременных нажатия одной кнопки)")
    # у Блока (заблокировал бота, но может голосовать через старое сообщение) голосов нет — берём его
    double_errs = []
    for nid in nom_ids()[:2]:
        a, b = await asyncio.gather(
            tap(1008, f"yv_pick_{nid}_{P['anna']}"), tap(1008, f"yv_pick_{nid}_{P['anna']}"),
        )
        print(f"     {nid:12s} тап1: {'ОШИБКА ' + type(a.err).__name__ if a.err else 'ok'} {a.elapsed:5.2f}с | "
              f"тап2: {'ОШИБКА ' + type(b.err).__name__ if b.err else 'ok'} {b.elapsed:5.2f}с")
        for r in (a, b):
            if r.err:
                double_errs.append((nid, type(r.err).__name__, str(r.err)[:80]))
    rows = await votes_count("block")
    check("двойной тап: без исключений", not double_errs, f"{len(double_errs)} из {len(nom_ids()[:2])*2}: {double_errs[:2]}")
    check("двойной тап: ровно один голос на номинацию", rows == 2, f"строк {rows}, ожидали {2}")

    print("\n[4b] Двойной тап с интервалом (как у живого человека)")

    async def delayed(delay, *a):
        await asyncio.sleep(delay)
        return await tap(*a)

    from sqlalchemy import delete as _d
    for delay in (0.02, 0.1, 0.3):
        failures = 0
        for nid in nom_ids()[:2]:
            async with async_session() as sx:      # сбрасываем голос, чтобы каждый раз шёл INSERT (худший случай)
                await sx.execute(_d(YearVote).where(YearVote.voter_id == P["block"], YearVote.nomination == nid))
                await sx.commit()
            a, b = await asyncio.gather(
                tap(1008, f"yv_pick_{nid}_{P['anna']}"), delayed(delay, 1008, f"yv_pick_{nid}_{P['anna']}"),
            )
            failures += sum(1 for r in (a, b) if r.err)
        print(f"     интервал {int(delay * 1000):>3} мс: сбоев {failures} из {2} пар")
    async with async_session() as sx:              # вернуть Блоку полный набор голосов
        for nid in nom_ids():
            have = (await sx.execute(select(YearVote).where(YearVote.voter_id == P["block"], YearVote.nomination == nid))).scalar_one_or_none()
            if have is None:
                sx.add(YearVote(year=2026, nomination=nid, voter_id=P["block"], nominee_id=P["anna"], updated_at=RealDT(2026, 12, 22)))
        await sx.commit()

    print("\n[5] Напоминание 29.12 10:00")
    at(29, 10, 0, 0)
    async with async_session() as s:
        for nid in nom_ids():   # Ева дозаполняет всё (toughest тоже), чтобы быть «закончившей»
            if (await s.execute(select(YearVote).where(YearVote.voter_id == P["anna"], YearVote.nomination == nid))).scalar_one_or_none() is None:
                s.add(YearVote(year=2026, nomination=nid, voter_id=P["anna"], nominee_id=P["bob"], updated_at=RealDT(2026, 12, 22)))
        await s.commit()
    mark = len(session.calls)
    await sched.send_year_vote_reminders(bot)
    got = {chat for chat, _, _ in sent_texts(mark)}
    # Анна закончила все 6; Блок тоже закончил все 6 (двойной тап); остальные — нет (в toughest никто не голосовал)
    check("напоминание не получила Анна (закончила)", 1001 not in got)
    check("напоминание не получил Блок (закончил, хоть и заблокировал бота)", 1008 not in got)
    check("напоминание получили незакончившие", {1002, 1003, 1004, 1005} <= got, str(sorted(got)))
    check("напоминание не ушло неучаствующим", not ({1006, 1007} & got))

    print("\n[6] Закрытие")
    at(30, 11, 59, 59)
    r = await tap(1004, f"yv_pick_{nom_ids()[1]}_{P['carol']}")
    check("30.12 11:59:59 — голос ещё принимается", r.answer is not None and "учтён" in (r.answer.text or ""))
    before = await votes_count()
    at(30, 12, 0, 0)
    r = await tap(1004, f"yv_pick_{nom_ids()[1]}_{P['bob']}")
    check("30.12 12:00:00 — голос отклонён «закрыто»", r.answer is not None and "закрыто" in (r.answer.text or ""))
    check("после закрытия голоса не меняются", await votes_count() == before)

    print("\n[7] 30.12 12:00 — итоги года + результаты")
    async with async_session() as s3:      # чистим одну номинацию, чтобы проверить ветку «никто не проголосовал»
        await s3.execute(_d(YearVote).where(YearVote.nomination == "toughest"))
        await s3.commit()
    session.calls.clear()
    try:
        await sched.send_year_end_combo(bot)
        crashed = None
    except Exception as e:  # noqa: BLE001
        crashed = e
    msgs = sent_texts(0)
    check("джоба отработала без исключений", crashed is None, repr(crashed))
    by_chat = {}
    for idx, (chat, text, _) in enumerate(msgs):
        by_chat.setdefault(chat, []).append((idx, text))
    is_results = lambda t: "Результаты голосования" in t  # noqa: E731
    order_ok = all(
        [is_results(t) for _, t in v].index(True) > 0 if any(is_results(t) for _, t in v) and len(v) > 1 else True
        for v in by_chat.values()
    )
    check("у каждого получателя результаты идут ПОСЛЕ итогов года", order_ok)
    res_msgs = [(c, t) for c, t, _ in msgs if is_results(t)]
    check("результаты отправлены всем допущенным (попытка на заблокировавшего проглочена safe_send)",
          {c for c, _ in res_msgs} == {PLAYERS[k][0] for k in eligible}, str(sorted({c for c, _ in res_msgs})))
    check("Фёдор и Галя результатов не получили", not ({1006, 1007} & {c for c, _ in res_msgs}))
    first_res = max(i for i, (c, t, _) in enumerate(msgs) if not is_results(t)) < min(i for i, (c, t, _) in enumerate(msgs) if is_results(t)) if res_msgs else False
    check("все «Итоги года» ушли раньше всех результатов", first_res)
    probs = [p for _, t, _ in msgs for p in html_problems(t)]
    check("HTML и длина всех сообщений корректны", not probs, str(sorted(set(probs))))
    text = res_msgs[0][1]
    print("\n     ── текст результатов ──")
    for line in text.split("\n"):
        print("     " + line)
    check("номинация без голосов подписана «никто не проголосовал»", "никто не проголосовал" in text)
    check("ничья оформлена «делят звание»", "делят звание" in text)
    check("имена с & и < экранированы", "Боб &amp; Ко" in text and "Кэрол &lt;3" in text)
    summary = next(t for c, t, _ in msgs if not is_results(t))
    print("\n     первая строка «Итогов года»:", summary.split("\n")[0][:120])

    print("\n[8] Пустое голосование")
    async with async_session() as s2:
        from sqlalchemy import delete
        await s2.execute(delete(YearVote))
        await s2.commit()
    mark = len(session.calls)
    await sched.send_year_vote_results(bot)
    check("0 голосов — результаты не рассылаются", not sent_texts(mark))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== Итого: {len(RESULTS) - len(failed)} из {len(RESULTS)} проверок пройдено ===")
    for name, _, detail in failed:
        print(f"  ПРОВАЛ: {name} — {detail}")
    await engine.dispose()


asyncio.run(main())

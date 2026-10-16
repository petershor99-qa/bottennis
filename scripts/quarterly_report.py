"""Квартальный «техосмотр» клуба по прод-базе — только чтение, игроки обезличены.

v2.197.0. Раз в квартал (15 января, апреля, июля, октября; бот напоминает
администратору) владелец запускает на сервере одну команду и присылает вывод
ассистенту (скилл `checkup`). По нему пересматриваются пороги и формулы:

1. Радар стиля — показатели карточки, цвета, архетипы (`style_report.py`).
2. Звания и рейтинг — сколько игроков в каждом звании, медиана, «инфляция»
   (сумма рейтингов выше стартовых 1000), кто стоит на полу 900/1000.
3. Начисления за матч по месяцам — средняя и крупнейшая дельта, доля боссфайтов.
4. «Цели недели» — сколько раз каждая цель выпадала и как часто её закрывали.
5. Ачивки — какие не получил никто и какие есть почти у всех.
6. Пинги рекордов клуба за период.
7. Запуск приложения — медиана, медленные (от 5 с) и неудачные запуски.

Запуск (на сервере, из папки бота):
  /opt/bottennis/.venv/bin/python scripts/quarterly_report.py [--days 90]
База — из DATABASE_URL (.env рядом со скриптом) или --db путь/к/файлу. Открывается
только на чтение (mode=ro); имён игроков в выводе нет — u1, u2, … по числу матчей
(та же нумерация, что в разделе радара).
"""
import argparse
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from export_usage import db_path_from_url, load_env  # noqa: E402

SLOW_BOOT_MS = 5000
ALMOST_ALL = 0.8          # ачивка «почти у всех» — у 80% активных игроков и больше


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pct(part: int, whole: int) -> str:
    return f"{round(part / whole * 100)}%" if whole else "—"


GOALS_SINCE = datetime(2026, 10, 11, 21, 0)   # понедельник 12.10 по МСК — неделя появления «Целей недели»


def goals_lines(active, by_player, names, first_week: datetime, this_week: datetime) -> list[str]:
    """Сколько раз каждая цель недели выпадала и закрывалась — по ЗАВЕРШЁННЫМ неделям
    [first_week, this_week), только у игроков, которые в эту неделю играли."""
    from bot.webapp import api_return

    offered: Counter = Counter()
    done: Counter = Counter()
    all_done = seen = 0
    ws = first_week
    while ws < this_week:
        we = ws + timedelta(weeks=1)
        for p in active:
            mine = by_player[p.id]
            week = [m for m in mine if ws <= m.completed_at < we]
            if not week:
                continue
            goals = api_return._goals_for_week([m for m in mine if m.completed_at < ws], week, p.id, ws, names)
            seen += 1
            all_done += all(g["done"] for g in goals)
            for g in goals:
                offered[g["key"]] += 1
                done[g["key"]] += g["done"]
        ws = we
    if not seen:
        return ["Полных недель с играми ещё нет"]
    lines = [f"Недель-игроков: {seen}; закрыто целиком: {all_done} ({_pct(all_done, seen)})"]
    lines += [f"{key}: выпадала {n}, закрыта {done[key]} ({_pct(done[key], n)})" for key, n in offered.most_common()]
    return lines


async def build(db_path: Path, days: int) -> str:
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///file:{db_path.as_posix()}?mode=ro&uri=true"
    import style_report
    from sqlalchemy import select

    from bot.db.database import async_session
    from bot.db.models import AppBoot, ClubRecordPing, Match, MatchStatus, Player
    from bot.services.achievements import ACHIEVEMENTS_LIST, get_achievements
    from bot.utils import MSK_OFFSET, RANK_TITLE_BANDS, RANK_TITLE_TOP, rank_title
    from bot.webapp import api_return

    now = _now()
    since = now - timedelta(days=days)
    out = [f"Техосмотр клуба: {(now + MSK_OFFSET):%d.%m.%Y}, период — {days} дней", ""]

    out.append("== 1. Радар стиля ==")
    out.append(await style_report.report(db_path))

    async with async_session() as session:
        players = list((await session.execute(select(Player))).scalars().all())
        matches = list((await session.execute(
            select(Match).where(Match.status == MatchStatus.completed, Match.completed_at.is_not(None))
            .order_by(Match.completed_at)
        )).scalars().all())
        by_player: dict[int, list] = defaultdict(list)
        for m in matches:
            by_player[m.challenger_id].append(m)
            by_player[m.challenged_id].append(m)
        active = sorted((p for p in players if by_player[p.id]), key=lambda p: (-len(by_player[p.id]), p.id))
        anon = {p.id: f"u{i}" for i, p in enumerate(active, 1)}

        # ── 2. Звания и рейтинг ──────────────────────────────────────────────
        out += ["", "== 2. Звания и рейтинг (игроки с матчами) =="]
        ratings = [p.rating for p in active]
        if ratings:
            out.append(f"Игроков: {len(active)}; рейтинг мин / медиана / макс: {min(ratings):.1f} / "
                       f"{median(ratings):.1f} / {max(ratings):.1f}")
            out.append(f"Инфляция: сумма рейтингов выше стартовых 1000 — {sum(r - 1000 for r in ratings):+.1f}")
            titles = Counter(rank_title(r) for r in ratings)
            order = [t for _, t in RANK_TITLE_BANDS] + [RANK_TITLE_TOP]
            bands = ", ".join(f"{t} {titles.get(t, 0)}" for t in order)
            out.append(f"Звания: {bands} (границы: " + ", ".join(f"{t} < {b}" for b, t in RANK_TITLE_BANDS)
                       + f", {RANK_TITLE_TOP} от {RANK_TITLE_BANDS[-1][0]})")
            floor = [anon[p.id] for p in active if p.rating in (900.0, 1000.0)]
            out.append("На полу 900/1000 сейчас: " + (", ".join(floor) if floor else "никого"))
            out.append("По игрокам: " + ", ".join(f"{anon[p.id]} {p.rating:.0f} ({rank_title(p.rating)})" for p in active))

        # ── 3. Начисления за матч по месяцам ────────────────────────────────
        out += ["", "== 3. Начисления за матч по месяцам (дельта победителя) =="]
        months: dict[str, list] = defaultdict(list)
        for m in matches:
            if m.completed_at >= now - timedelta(days=max(days, 180)):
                months[(m.completed_at + MSK_OFFSET).strftime("%Y-%m")].append(m)
        for key in sorted(months):
            ms = months[key]
            deltas = [m.rating_change for m in ms if m.rating_change is not None and m.winner_id is not None]
            boss = sum(1 for m in ms if m.is_boss_fight)
            line = f"{key}: матчей {len(ms)}"
            if deltas:
                line += f", дельта средняя {sum(deltas) / len(deltas):.1f}, медиана {median(deltas):.1f}, макс {max(deltas):.1f}"
            out.append(line + f", боссфайтов {boss}, ничьих {sum(1 for m in ms if m.winner_id is None)}")

        # ── 4. Цели недели ──────────────────────────────────────────────────
        out += ["", "== 4. «Цели недели» (только недели, когда игрок играл) =="]
        names = {p.id: p.display_name for p in players}
        out += goals_lines(active, by_player, names, api_return.week_start(max(since, GOALS_SINCE)),
                           api_return.week_start(now))

        # ── 5. Ачивки ───────────────────────────────────────────────────────
        out += ["", "== 5. Ачивки (у игроков с матчами) =="]
        holders = Counter(a for p in active for a in set(get_achievements(p)))
        never = [a.name for a in ACHIEVEMENTS_LIST if holders[a.id] == 0]
        common = [f"{a.name} ({holders[a.id]})" for a in ACHIEVEMENTS_LIST
                  if active and holders[a.id] >= ALMOST_ALL * len(active)]
        out.append(f"Всего ачивок {len(ACHIEVEMENTS_LIST)}; получена хоть кем-то: {len(ACHIEVEMENTS_LIST) - len(never)}")
        out.append("Не получил никто: " + (", ".join(never) if never else "—"))
        out.append(f"Почти у всех (от {round(ALMOST_ALL * 100)}%): " + (", ".join(common) if common else "—"))

        # ── 6. Пинги рекордов клуба ─────────────────────────────────────────
        out += ["", "== 6. Пинги рекордов клуба за период =="]
        pings = list((await session.execute(
            select(ClubRecordPing).where(ClubRecordPing.pinged_at >= since))).scalars().all())
        events = Counter((pg.kind, round(pg.value, 1)) for pg in pings)
        kinds = Counter(kind for kind, _ in events)
        out.append(f"Событий: {len(events)}, сообщений: {len(pings)}"
                   + ("; по видам: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()) if kinds else ""))

        # ── 7. Запуск приложения ────────────────────────────────────────────
        out += ["", "== 7. Запуск приложения =="]
        boots = list((await session.execute(select(AppBoot).where(AppBoot.created_at >= since))).scalars().all())
        ok = [b.ms for b in boots if not b.failed and b.ms > 0]
        failed = sum(1 for b in boots if b.failed)
        if ok:
            slow = sum(1 for ms in ok if ms >= SLOW_BOOT_MS)
            out.append(f"Запусков: {len(boots)}; медиана {median(ok) / 1000:.1f} с; от {SLOW_BOOT_MS // 1000} с: "
                       f"{slow} ({_pct(slow, len(ok))}); не запустилось: {failed}")
            per = defaultdict(list)
            for b in boots:
                if not b.failed and b.ms > 0:
                    per[b.platform or "?"].append(b.ms)
            out.append("По клиентам: " + ", ".join(f"{k} {median(v) / 1000:.1f} с ({len(v)})"
                                                   for k, v in sorted(per.items(), key=lambda x: -len(x[1]))))
        else:
            out.append(f"Запусков с замером нет; не запустилось: {failed}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    load_env()
    db = Path(args.db) if args.db else db_path_from_url(os.environ.get("DATABASE_URL", "sqlite:///bottennis.db"))
    if not db.exists():
        raise SystemExit(f"Нет файла базы: {db}")
    print(asyncio.run(build(db, args.days)))


if __name__ == "__main__":
    main()

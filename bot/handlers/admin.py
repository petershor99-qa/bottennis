"""
Админ-команды. Доступны только владельцу (ADMIN_ID в .env).

/dbstats  — анализ начислений рейтинга по всей БД
/myid     — показать свой Telegram ID (для настройки ADMIN_ID)
/backup   — снять бэкап БД по запросу, без ожидания ежемесячной джобы
/usage    — какие экраны реально открывают (счётчик, этап 1 дорожной карты)
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player, UsageEvent
from bot.scheduler import send_backup_file
from bot.services.usage import action_label
from bot.utils import MSK_OFFSET, env_int, pluralize_opens, pluralize_players

router = Router()

ADMIN_ID: int = env_int("ADMIN_ID")


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_admin(message: Message) -> bool:
    return ADMIN_ID != 0 and message.from_user.id == ADMIN_ID


_SEND_CHUNK = 4000  # с запасом от лимита Telegram в 4096 символов


async def _send(message: Message, text: str) -> None:
    """Отправить длинный текст, разбив на части по границам строк.

    Резать произвольно по символам нельзя — можно разорвать HTML-тег
    (<b>...</b>) пополам, и Telegram отклонит сообщение с ошибкой парсинга.
    Если одна строка сама длиннее лимита (крайний случай) — режем её по
    символам без учёта HTML, это осознанный компромисс.
    """
    chunk = ""
    for line in text.split("\n"):
        if len(line) > _SEND_CHUNK:
            if chunk:
                await message.answer(chunk)
                chunk = ""
            for i in range(0, len(line), _SEND_CHUNK):
                await message.answer(line[i:i + _SEND_CHUNK])
            continue

        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > _SEND_CHUNK:
            await message.answer(chunk)
            chunk = line
        else:
            chunk = candidate

    if chunk:
        await message.answer(chunk)


# ── /myid ──────────────────────────────────────────────────────────────────────

@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Показывает Telegram ID текущего пользователя."""
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


# ── /backup ────────────────────────────────────────────────────────────────────

@router.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot) -> None:
    """Снимает бэкап БД по запросу — не дожидаясь ежемесячной джобы шедулера."""
    if not _is_admin(message):
        return
    date_str = (datetime.now(timezone.utc) + MSK_OFFSET).strftime("%Y-%m-%d")
    ok = await send_backup_file(bot, message.chat.id, f"💾 Бэкап по запросу — {date_str}")
    if not ok:
        await message.answer("⚠️ Файл базы данных не найден.")


# ── /usage ─────────────────────────────────────────────────────────────────────

_USAGE_WINDOW_DAYS = 30


@router.message(Command("usage"))
async def cmd_usage(message: Message, session: AsyncSession) -> None:
    """Отчёт по счётчику открытий экранов (этап 1 дорожной карты) — какие
    экраны реально используют, за последние 30 дней. Данные копятся только
    с момента выкатки этой команды — задним числом их нет."""
    if not _is_admin(message):
        return

    period_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_USAGE_WINDOW_DAYS)
    r = await session.execute(
        select(
            UsageEvent.action,
            func.count().label("opens"),
            func.count(func.distinct(UsageEvent.user_id)).label("users"),
            func.max(UsageEvent.created_at).label("last_at"),
        )
        .where(UsageEvent.created_at >= period_start)
        .group_by(UsageEvent.action)
        .order_by(func.count().desc())
    )
    rows = r.all()

    header = f"📊 <b>Использование экранов</b> — за последние {_USAGE_WINDOW_DAYS} дней"
    if not rows:
        await message.answer(
            f"{header}\n"
            "Данных пока нет — счётчик считает только с момента выкатки этой команды."
        )
        return

    total = sum(row.opens for row in rows)
    lines = [header, f"Всего событий: <b>{total}</b>\n"]
    for i, row in enumerate(rows, 1):
        last_str = (row.last_at + MSK_OFFSET).strftime("%d.%m")
        lines.append(
            f"{i}. {h(action_label(row.action))} — "
            f"{pluralize_opens(row.opens)}, {pluralize_players(row.users)}, "
            f"последнее {last_str}"
        )
    await _send(message, "\n".join(lines))


# ── /dbstats ──────────────────────────────────────────────────────────────────

@router.message(Command("dbstats"))
async def cmd_dbstats(message: Message, session: AsyncSession) -> None:
    if not _is_admin(message):
        if ADMIN_ID == 0:
            await message.answer(
                "⚙️ <b>ADMIN_ID не настроен.</b>\n\n"
                f"Твой ID: <code>{message.from_user.id}</code>\n\n"
                "Добавь переменную <code>ADMIN_ID</code> в <code>.env</code> с этим значением, "
                "затем перезапусти бота и повтори команду.",
            )
        return

    await message.answer("⏳ Анализирую базу данных...")

    # ── Загружаем данные ───────────────────────────────────────────────────────
    players_r = await session.execute(select(Player).order_by(Player.rating.desc()))
    players = players_r.scalars().all()
    player_map = {p.id: p.display_name for p in players}

    matches_r = await session.execute(
        select(Match)
        .where(Match.status == MatchStatus.completed, Match.winner_id.isnot(None))
        .order_by(Match.completed_at)
    )
    matches = matches_r.scalars().all()

    if not matches:
        await message.answer("Завершённых матчей пока нет.")
        return

    deltas = [m.rating_change for m in matches if m.rating_change is not None]
    if not deltas:
        await message.answer("У завершённых матчей не заполнено поле rating_change.")
        return

    # ── 1. Обзор ──────────────────────────────────────────────────────────────
    avg = sum(deltas) / len(deltas)
    median = sorted(deltas)[len(deltas) // 2]

    lines = ["<b>📊 Анализ рейтинговых начислений</b>\n"]
    lines.append(f"Всего матчей (с победителем): <b>{len(matches)}</b>")
    lines.append(f"Диапазон Δ: <b>{min(deltas):.1f} — {max(deltas):.1f}</b>")
    lines.append(f"Среднее Δ: <b>{avg:.1f}</b>   Медиана: <b>{median:.1f}</b>")

    # Распределение по диапазонам
    lines.append("\n<b>Распределение Δ:</b>")
    buckets = defaultdict(int)
    for d in deltas:
        b = int(d // 5) * 5
        buckets[b] += 1
    for b in sorted(buckets):
        bar = "▓" * min(20, buckets[b])
        lines.append(f"  {b:>3}–{b+4} pts │ {buckets[b]:>3}  {bar}")

    await _send(message, "\n".join(lines))

    # ── 2. По формату матча ───────────────────────────────────────────────────
    fmt_data = defaultdict(list)
    for m in matches:
        if m.rating_change is None or not m.sets_data:
            continue
        sets = m.sets_data if isinstance(m.sets_data, list) else json.loads(m.sets_data)
        w = sum(1 for s in sets if s["w"] > s["l"])
        losses = len(sets) - w
        fmt_data[f"{w}-{losses}"].append(m.rating_change)

    lines = ["<b>🎯 Среднее Δ по формату матча:</b>\n"]
    lines.append(f"  {'Формат':<8} {'Матчей':>7}  {'Мин':>5}  {'Ср.':>5}  {'Макс':>5}")
    lines.append("  " + "─" * 38)
    for fmt in sorted(fmt_data, key=lambda x: (int(x.split("-")[1]), x)):
        v = fmt_data[fmt]
        lines.append(
            f"  <code>{fmt:<8}</code> {len(v):>7}  {min(v):>5.1f}  "
            f"{sum(v)/len(v):>5.1f}  {max(v):>5.1f}"
        )

    # ── 3. Текущие рейтинги ───────────────────────────────────────────────────
    lines.append("\n<b>🏆 Текущие рейтинги:</b>\n")
    win_map = defaultdict(int)
    loss_map = defaultdict(int)
    for m in matches:
        win_map[m.winner_id] += 1
        loser_id = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
        loss_map[loser_id] += 1

    for i, p in enumerate(players, 1):
        w = win_map.get(p.id, 0)
        losses = loss_map.get(p.id, 0)
        total = w + losses
        pct = f"{100*w//total}%" if total else "—"
        lines.append(
            f"  {i}. <b>{h(p.display_name)}</b>  {p.rating:.1f} pts  "
            f"({w}W/{losses}L  {pct})"
        )

    await _send(message, "\n".join(lines))

    # ── 4. Ретроспектива: влияние рейтинга соперника ──────────────────────────
    # Восстанавливаем рейтинги «до матча» в обратном порядке
    snap = {p.id: p.rating for p in players}
    gap_data = defaultdict(list)

    for m in reversed(matches):
        d = m.rating_change
        if d is None:
            continue
        wid = m.winner_id
        lid = m.challenged_id if wid == m.challenger_id else m.challenger_id
        wr_after = snap.get(wid, 1000.0)
        lr_after = snap.get(lid, 1000.0)
        wr_before = round(wr_after - d, 1)
        lr_before = round(lr_after + d, 1)
        snap[wid] = wr_before
        snap[lid] = lr_before
        gap = lr_before - wr_before   # положительный = победитель был слабее
        bucket = round(gap / 50) * 50
        gap_data[bucket].append(d)

    lines = ["<b>📈 Δ по разнице рейтингов (ретроспективно):</b>\n"]
    lines.append(f"  {'Разрыв (соперник − победитель)':>32}  {'N':>4}  {'Ср.Δ':>6}")
    lines.append("  " + "─" * 48)
    for gap in sorted(gap_data):
        v = gap_data[gap]
        if gap > 0:
            label = f"победитель слабее на ~{gap}"
        elif gap < 0:
            label = f"победитель сильнее на ~{abs(gap)}"
        else:
            label = "примерно равные"
        lines.append(f"  {label:>32}  {len(v):>4}  {sum(v)/len(v):>6.1f}")

    await _send(message, "\n".join(lines))

    # ── 5. Топ-5 и Боттом-5 начислений ───────────────────────────────────────
    ranked = sorted(
        [m for m in matches if m.rating_change is not None],
        key=lambda x: x.rating_change,
        reverse=True,
    )

    def _row(m: Match) -> str:
        wname = h(player_map.get(m.winner_id, "?"))
        lid = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
        lname = h(player_map.get(lid, "?"))
        sets = m.sets_data if isinstance(m.sets_data, list) else json.loads(m.sets_data or "[]")
        score = "  ".join(f"{s['w']}:{s['l']}" for s in sets)
        return f"  +{m.rating_change:.1f}  {wname} → {lname}  [{score}]"

    lines = ["<b>🔝 Топ-5 начислений:</b>"]
    for m in ranked[:5]:
        lines.append(_row(m))

    lines.append("\n<b>📉 Наименьшие начисления:</b>")
    for m in ranked[-5:]:
        lines.append(_row(m))

    # ── 6. Repeat-penalty ─────────────────────────────────────────────────────
    lines.append("\n<b>🔄 Repeat-penalty (подряд vs одного соперника):</b>")
    lines.append("  Подряд│ ×mult │ Пример дельты (равные, 3-0)")
    lines.append("  ──────┼───────┼──────────────────────────")
    base_ex = 20.5  # примерная дельта без penalty
    for streak in range(10):
        rm = max(0.5, 1.0 - 0.05 * streak)
        ex = round(base_ex * rm, 1)
        bar = "▓" * int(rm * 10)
        lines.append(f"  {streak+1:>5}x │ ×{rm:.2f} │ ~{ex:>5.1f}  {bar}")

    await _send(message, "\n".join(lines))

    await message.answer("✅ Готово.")

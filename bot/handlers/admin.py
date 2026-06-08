"""
Админ-команды. Доступны только владельцу (ADMIN_ID в .env).

/dbstats  — анализ начислений рейтинга по всей БД
/myid     — показать свой Telegram ID (для настройки ADMIN_ID)
"""
import json
import os
from collections import defaultdict

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, MatchStatus, Player

router = Router()

ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_admin(message: Message) -> bool:
    return ADMIN_ID != 0 and message.from_user.id == ADMIN_ID


async def _send(message: Message, text: str) -> None:
    """Отправить длинный текст, при необходимости разбив на части."""
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000], parse_mode="HTML")


# ── /myid ──────────────────────────────────────────────────────────────────────

@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Показывает Telegram ID текущего пользователя."""
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


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
                parse_mode="HTML",
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
            f"  {i}. <b>{p.display_name}</b>  {p.rating:.1f} pts  "
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
        wname = player_map.get(m.winner_id, "?")
        lid = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
        lname = player_map.get(lid, "?")
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

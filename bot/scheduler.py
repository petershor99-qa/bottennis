import logging
from datetime import datetime, timedelta, timezone
from html import escape as h

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, or_, and_, desc, func
from sqlalchemy.orm import selectinload

from bot.db.database import async_session
from bot.db.models import Match, MatchStatus, Player
from bot.keyboards.inline import active_match_kb
from bot.utils import (
    match_rating_delta, pluralize_matches, _match_line,
    pick_match_of_day, match_drama_reason, match_score_challenger_first,
)

MSK_OFFSET = timedelta(hours=3)

logger = logging.getLogger(__name__)


# ── Напоминание о незавершённых матчах ────────────────────────────────────────

async def send_match_reminders(bot: Bot) -> None:
    """Раз в час ищет принятые матчи старше 24 часов и напоминает игрокам."""
    async with async_session() as session:
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

        result = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.accepted,
                Match.reminder_sent == False,  # noqa: E712
                # accepted_at для новых записей, created_at как fallback для старых
                or_(
                    and_(Match.accepted_at.isnot(None), Match.accepted_at <= threshold),
                    and_(Match.accepted_at.is_(None), Match.created_at <= threshold),
                ),
            )
            .options(selectinload(Match.challenger), selectinload(Match.challenged))
        )
        matches = result.scalars().all()

        for match in matches:
            for player, opponent in [
                (match.challenger, match.challenged),
                (match.challenged, match.challenger),
            ]:
                try:
                    await bot.send_message(
                        player.telegram_id,
                        f"⏰ <b>Напоминание о матче</b>\n\n"
                        f"У тебя с <b>{h(opponent.display_name)}</b> есть незавершённый матч "
                        f"уже больше 24 часов.\n"
                        f"Сыграйте и внесите результат! 🏓\n\n"
                        f"<i>Напиши счёт прямо сюда — например: <code>11:7 9:11 11:5</code></i>",
                        reply_markup=active_match_kb(match.id),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

            match.reminder_sent = True

        if matches:
            await session.commit()
            logger.info("Отправлено напоминаний: %d", len(matches))


# ── Еженедельный дайджест ─────────────────────────────────────────────────────


async def send_weekly_digest(bot: Bot) -> None:
    """Каждый понедельник в 9:00 МСК отправляет игрокам итоги недели."""
    async with async_session() as session:
        week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        two_weeks_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)

        players_result = await session.execute(select(Player))
        players = players_result.scalars().all()

        # Ранжирование по рейтингу
        sorted_players = sorted(players, key=lambda p: p.rating, reverse=True)
        rank_map = {p.id: i + 1 for i, p in enumerate(sorted_players)}
        player_name_map = {p.id: p.display_name for p in players}

        # ── Герои недели — агрегируем все матчи за неделю одним запросом ─────
        all_week_r = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= week_ago,
            )
        )
        all_week_matches = all_week_r.scalars().all()

        if not all_week_matches:
            logger.info("Еженедельный дайджест: за неделю матчей не было, пропускаем")
            return

        # Матчи за позапрошлую неделю — для сравнения активности
        prev_week_r = await session.execute(
            select(func.count()).select_from(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= two_weeks_ago,
                Match.completed_at < week_ago,
            )
        )
        prev_week_count = prev_week_r.scalar()

        heroes_block = ""
        if all_week_matches:
            match_count: dict[int, int] = {}
            wins_count: dict[int, int] = {}
            delta_sum: dict[int, float] = {}

            for m in all_week_matches:
                for pid in (m.challenger_id, m.challenged_id):
                    match_count[pid] = match_count.get(pid, 0) + 1
                    delta_sum[pid] = delta_sum.get(pid, 0.0) + match_rating_delta(m, pid)
                if m.winner_id:
                    wins_count[m.winner_id] = wins_count.get(m.winner_id, 0) + 1

            total_sets = sum(len(m.sets_data) if m.sets_data else 0 for m in all_week_matches)

            cur_count = len(all_week_matches)
            if prev_week_count > 0:
                diff = cur_count - prev_week_count
                diff_str = f"+{diff}" if diff >= 0 else str(diff)
                activity_line = (
                    f"⚡ Сыграно за неделю: <b>{cur_count}</b> матчей, <b>{total_sets}</b> партий"
                    f"  <i>({diff_str} к прошлой)</i>"
                )
            else:
                activity_line = (
                    f"⚡ Сыграно за неделю: <b>{cur_count}</b> матчей, <b>{total_sets}</b> партий"
                )

            hero_lines = [
                "\n🏆 <b>Герои недели:</b>",
                activity_line,
            ]

            # Главный теннисист недели — самый активный
            most_active_id = max(match_count, key=match_count.get)
            hero_lines.append(
                f"🏅 Главный теннисист недели — <b>{h(player_name_map[most_active_id])}</b> "
                f"({pluralize_matches(match_count[most_active_id])})"
            )

            # Больше всех побед (только если были победы)
            if wins_count:
                most_wins_id = max(wins_count, key=wins_count.get)
                hero_lines.append(
                    f"🥇 Больше всех побед — <b>{h(player_name_map[most_wins_id])}</b> "
                    f"({wins_count[most_wins_id]})"
                )

            # Лучший рост рейтинга (только если положительный)
            best_gain_id = max(delta_sum, key=delta_sum.get)
            if delta_sum[best_gain_id] > 0:
                hero_lines.append(
                    f"📈 Лучший рост — <b>{h(player_name_map[best_gain_id])}</b> "
                    f"(+{round(delta_sum[best_gain_id], 1)} pts)"
                )

            # Наибольшее падение (только если отрицательное)
            worst_id = min(delta_sum, key=delta_sum.get)
            if delta_sum[worst_id] < 0:
                hero_lines.append(
                    f"📉 Тяжелее всех — <b>{h(player_name_map[worst_id])}</b> "
                    f"({round(delta_sum[worst_id], 1)} pts)"
                )

            # Блок показываем только если есть хотя бы 2 строки (заголовок + хоть одна)
            if len(hero_lines) > 1:
                heroes_block = "\n".join(hero_lines)

        # ── Персональные сообщения ────────────────────────────────────────────
        for player in players:
            matches_result = await session.execute(
                select(Match)
                .where(
                    or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                    Match.status == MatchStatus.completed,
                    Match.completed_at >= week_ago,
                )
                .options(selectinload(Match.challenger), selectinload(Match.challenged))
            )
            matches = matches_result.scalars().all()

            rank = rank_map.get(player.id, 0)
            wins = sum(1 for m in matches if m.winner_id == player.id)
            draws = sum(1 for m in matches if m.winner_id is None)
            losses = len(matches) - wins - draws

            rating_delta = sum(match_rating_delta(m, player.id) for m in matches)
            sign = "+" if rating_delta >= 0 else ""

            if not matches:
                last_r = await session.execute(
                    select(Match)
                    .where(
                        or_(Match.challenger_id == player.id, Match.challenged_id == player.id),
                        Match.status == MatchStatus.completed,
                    )
                    .order_by(desc(Match.completed_at))
                    .limit(1)
                )
                last_m = last_r.scalar_one_or_none()
                vanished = (
                    last_m is not None
                    and last_m.completed_at is not None
                    and last_m.completed_at < two_weeks_ago
                )
                vanished_line = "\n👻 Куда пропал? Тебя давно не видели за столом!\n" if vanished else "\n"

                text = (
                    f"📊 <b>Итоги недели</b>\n\n"
                    f"На этой неделе матчей не было.\n"
                    f"Твой рейтинг: <b>{round(player.rating, 1)}</b> pts — #{rank}"
                    f"{vanished_line}\n"
                    f"«Ты либо занят жизнью, либо занят умиранием.»"
                    f"{heroes_block}"
                )
            else:
                draws_str = f"  |  🤝 Ничьих: <b>{draws}</b>" if draws > 0 else ""
                lines = [
                    f"📊 <b>Итоги недели</b>\n",
                    f"🏆 Побед: <b>{wins}</b>{draws_str}  |  💔 Поражений: <b>{losses}</b>",
                    f"📈 Рейтинг: <b>{round(player.rating, 1)}</b> pts "
                    f"({sign}{round(rating_delta, 1)}) — #{rank}\n",
                    "<b>Матчи:</b>",
                ]
                for m in matches:
                    lines.append(_match_line(m, player.id))
                if heroes_block:
                    lines.append(heroes_block)
                text = "\n".join(lines)

            try:
                await bot.send_message(player.telegram_id, text, parse_mode="HTML")
            except Exception:
                pass

    logger.info("Еженедельный дайджест отправлен")


# ── Итоги дня (20:00 МСК) ─────────────────────────────────────────────────────


async def send_daily_summary(bot: Bot) -> None:
    """Каждый день в 20:00 МСК отправляет игрокам сводку за день + «матч дня»."""
    async with async_session() as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        msk_now = now + MSK_OFFSET
        msk_midnight = msk_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = msk_midnight - MSK_OFFSET   # граница дня по МСК в UTC-naive

        r = await session.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.completed,
                Match.completed_at >= day_start,
            )
            .options(selectinload(Match.challenger), selectinload(Match.challenged))
            .order_by(Match.completed_at)
        )
        matches = r.scalars().all()

        if not matches:
            logger.info("Итоги дня: матчей не было, пропускаем")
            return

        players_r = await session.execute(select(Player))
        players = players_r.scalars().all()
        name_map = {p.id: p.display_name for p in players}

        # ── Агрегаты по игрокам ────────────────────────────────────────────────
        stats: dict[int, dict] = {}
        delta_sum: dict[int, float] = {}
        for m in matches:
            for pid in (m.challenger_id, m.challenged_id):
                st = stats.setdefault(pid, {"w": 0, "l": 0, "d": 0, "total": 0})
                st["total"] += 1
                delta_sum[pid] = delta_sum.get(pid, 0.0) + match_rating_delta(m, pid)
            if m.winner_id is None:
                stats[m.challenger_id]["d"] += 1
                stats[m.challenged_id]["d"] += 1
            else:
                stats[m.winner_id]["w"] += 1
                lid = m.challenged_id if m.winner_id == m.challenger_id else m.challenger_id
                stats[lid]["l"] += 1

        total_sets = sum(len(m.sets_data) if m.sets_data else 0 for m in matches)
        date_str = msk_now.strftime("%d.%m")

        lines = [
            f"📅 <b>Итоги дня — {date_str}</b>\n",
            f"⚡ Сыграно: <b>{len(matches)}</b> матчей, <b>{total_sets}</b> партий\n",
            "🏆 <b>Топ дня:</b>",
        ]

        medals = ["🥇", "🥈", "🥉"]
        sorted_players = sorted(
            stats.items(), key=lambda x: (x[1]["w"], x[1]["total"]), reverse=True
        )
        for i, (pid, st) in enumerate(sorted_players):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            draws_str = f"–{st['d']}🤝" if st["d"] else ""
            lines.append(f"{prefix} <b>{h(name_map.get(pid, '?'))}</b> — {st['w']}–{st['l']}{draws_str}")

        # Лучший рост рейтинга за день (если положительный)
        if delta_sum:
            best_id = max(delta_sum, key=delta_sum.get)
            if delta_sum[best_id] > 0:
                lines.append(
                    f"\n📈 Лучший рост: <b>{h(name_map.get(best_id, '?'))}</b> "
                    f"(+{round(delta_sum[best_id], 1)} pts)"
                )

        # Матч дня
        mod = pick_match_of_day(matches)
        if mod:
            ch = name_map.get(mod.challenger_id, "?")
            cd = name_map.get(mod.challenged_id, "?")
            score_str = match_score_challenger_first(mod)
            reason = match_drama_reason(mod)
            lines.append(
                f"\n🌟 <b>Матч дня</b>\n"
                f"<b>{h(ch)}</b> vs <b>{h(cd)}</b> — {score_str}\n"
                f"<i>{reason}</i>"
            )

        text = "\n".join(lines)
        for p in players:
            try:
                await bot.send_message(p.telegram_id, text, parse_mode="HTML")
            except Exception:
                pass

    logger.info("Итоги дня отправлены")


# ── Инициализация планировщика ────────────────────────────────────────────────

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Проверка незавершённых матчей — каждый час
    scheduler.add_job(
        send_match_reminders,
        IntervalTrigger(hours=1),
        args=[bot],
        id="match_reminders",
    )

    # Еженедельный дайджест — каждый понедельник в 9:00 МСК (06:00 UTC)
    scheduler.add_job(
        send_weekly_digest,
        CronTrigger(day_of_week="mon", hour=6, minute=0),
        args=[bot],
        id="weekly_digest",
    )

    # Итоги дня — каждый день в 20:00 МСК (17:00 UTC)
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=17, minute=0),
        args=[bot],
        id="daily_summary",
    )

    return scheduler

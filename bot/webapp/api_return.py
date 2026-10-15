"""
Mini App, пакет 4, релиз 3 (v2.181.0): поводы заглядывать в приложение — без
единого уведомления.

- «Пока тебя не было» (`since_payload`): что изменилось с прошлого захода
  игрока — место, рейтинг, матчи клуба, ачивки, новый чемпион. Время прошлого
  захода хранит страница (CloudStorage Telegram), сервер только считает.
- «Цели недели» (`goals_payload`): три личные цели на неделю (понедельник
  00:00 МСК). Владелец разрешил их только в приложении (2026-10-15; в боте
  «недельные мини-челленджи» по-прежнему в «Решено НЕ делать»). Цели строятся
  из истории игрока ДО начала недели — посреди недели не меняются; прогресс —
  по матчам этой недели. «Недель подряд» — сколько недель до этой закрыты
  целиком.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AchievementEarned, ChampionReign, Match, MatchStatus, Player
from bot.utils import MSK_OFFSET, _ru_plural, match_rating_delta
from bot.webapp.api_day import _standings_at

SINCE_MAX_DAYS = 30          # дальше «пока тебя не было» не смотрим
STREAK_MAX_WEEKS = 26
MATCH_GOAL_MIN, MATCH_GOAL_MAX = 3, 15
REVENGE_WEEKS = 8            # реванш — только за поражения последних 8 недель


def revenge_rival(history: list[Match], pid: int, start: datetime) -> int | None:
    """Кому реванш (v2.185.0): самое позднее поражение за REVENGE_WEEKS, за
    которое игрок ещё не отыгрался — не обыграл этого соперника после него.
    Раньше бралось просто последнее поражение, и «Реванш: X» выпадал, даже
    если игрок уже выиграл у X следом."""
    since = start - timedelta(weeks=REVENGE_WEEKS)
    avenged: set[int] = set()
    for m in sorted(history, key=lambda m: m.completed_at, reverse=True):
        if m.completed_at < since:
            break
        if m.winner_id is None:
            continue
        rival = m.challenged_id if m.challenger_id == pid else m.challenger_id
        if m.winner_id == pid:
            avenged.add(rival)
        elif rival not in avenged:
            return rival
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}{abs(x):.1f}"


# ── Пока тебя не было ─────────────────────────────────────────────────────────

async def since_payload(session: AsyncSession, viewer: Player, since_ts: int) -> dict | None:
    """Что изменилось после `since_ts` (секунды UTC). None — неверное время."""
    now = _now()
    since = datetime.fromtimestamp(since_ts, tz=timezone.utc).replace(tzinfo=None)
    if since > now + timedelta(minutes=5):
        return None
    since = max(since, now - timedelta(days=SINCE_MAX_DAYS))
    later = list((await session.execute(
        select(Match).where(Match.status == MatchStatus.completed, Match.completed_at >= since)
    )).scalars().all())
    players = list((await session.execute(select(Player))).scalars().all())
    names = {p.id: p.display_name for p in players}
    label = (since + MSK_OFFSET).strftime("с %d.%m, %H:%M")
    items: list[dict] = []

    mine = [m for m in later if viewer.id in (m.challenger_id, m.challenged_id)]
    if later:
        before = (await _standings_at(session, players, since, later)).get(viewer.id)
        after = (await _standings_at(session, players, now + timedelta(seconds=1), [])).get(viewer.id)
        if before and after and before != after:
            arrow = "▲" if after < before else "▼"
            items.append({"label": "Твоё место", "value": f"№{before} → №{after} {arrow}{abs(before - after)}"})
        elif after and not before:
            items.append({"label": "Твоё место", "value": f"№{after}"})
    if mine:
        delta = sum(match_rating_delta(m, viewer.id) for m in mine if m.rating_change is not None)
        wins = sum(1 for m in mine if m.winner_id == viewer.id)
        losses = sum(1 for m in mine if m.winner_id not in (None, viewer.id))
        parts = [_ru_plural(wins, "победа", "победы", "побед")]
        if losses:
            parts.append(_ru_plural(losses, "поражение", "поражения", "поражений"))
        items.append({"label": "Твой рейтинг", "value": _signed(delta), "note": ", ".join(parts)})
    if later:
        items.append({"label": "Матчей в клубе", "value": str(len(later))})
    earned = (await session.execute(
        select(AchievementEarned.player_id).where(AchievementEarned.earned_at >= since)
    )).scalars().all()
    if earned:
        who = sorted({names.get(pid, "?") for pid in earned})
        items.append({"label": "Новые ачивки", "value": str(len(earned)), "note": ", ".join(who)})
    champion = (await session.execute(
        select(ChampionReign.player_id).where(ChampionReign.started_at >= since)
        .order_by(ChampionReign.started_at.desc()).limit(1)
    )).scalar_one_or_none()
    if champion is not None:
        items.append({"label": "Новый чемпион", "value": names.get(champion, "?")})
    return {"title": "Пока тебя не было", "since": label, "items": items}


# ── Цели недели ───────────────────────────────────────────────────────────────

def week_start(moment: datetime) -> datetime:
    """Понедельник 00:00 по МСК недели `moment`, в UTC-naive."""
    msk = moment + MSK_OFFSET
    monday = (msk - timedelta(days=msk.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday - MSK_OFFSET


def _sets(m: Match, pid: int) -> list[tuple[int, int]]:
    """Счёт партий в перспективе игрока pid (sets_data — в перспективе победителя,
    при ничьей — challenger)."""
    persp = m.winner_id if m.winner_id is not None else m.challenger_id
    out = []
    for s in m.sets_data or []:
        w, lo = int(s.get("w", 0)), int(s.get("l", 0))
        out.append((w, lo) if persp == pid else (lo, w))
    return out


def _deuce_set_wins(m: Match, pid: int) -> int:
    return sum(1 for a, b in _sets(m, pid) if a > b and a >= 12)


def _converted(m: Match, pid: int) -> bool:
    sets = _sets(m, pid)
    return len(sets) >= 2 and m.winner_id == pid and sets[0][0] > sets[0][1]


def _comeback(m: Match, pid: int) -> bool:
    sets = _sets(m, pid)
    return len(sets) >= 2 and m.winner_id == pid and sets[0][0] < sets[0][1]


def _dry_set_wins(m: Match, pid: int) -> int:
    return sum(1 for a, b in _sets(m, pid) if a > b and b <= 5)


def _clean_win(m: Match, pid: int) -> bool:
    sets = _sets(m, pid)
    return len(sets) >= 2 and m.winner_id == pid and all(a > b for a, b in sets)


def _decider(m: Match, pid: int) -> bool:
    """Матч дошёл до решающей партии: перед последней партией счёт по партиям равный."""
    sets = _sets(m, pid)
    if len(sets) < 3:
        return False
    before = sets[:-1]
    return sum(1 for a, b in before if a > b) == sum(1 for a, b in before if a < b)


def _decider_win(m: Match, pid: int) -> bool:
    return _decider(m, pid) and m.winner_id == pid


def _opponent(m: Match, pid: int) -> int:
    return m.challenged_id if m.challenger_id == pid else m.challenger_id


def _rivals_count(week: list[Match], pid: int) -> int:
    return len({_opponent(m, pid) for m in week})


def _best_streak(week: list[Match], pid: int) -> int:
    best = run = 0
    for m in sorted(week, key=lambda m: m.completed_at):
        run = run + 1 if m.winner_id == pid else 0
        best = max(best, run)
    return best


# Цель на мастерство: (ключ, заголовок, цель, прогресс по матчам недели)
SKILLS = [
    ("deuce", "Выиграй 2 партии на дьюсе", 2, lambda w, pid: sum(_deuce_set_wins(m, pid) for m in w)),
    ("convert", "Дожми 2 матча после выигранной первой партии", 2,
     lambda w, pid: sum(_converted(m, pid) for m in w)),
    ("comeback", "Отыграйся после проигранной первой партии", 1, lambda w, pid: sum(_comeback(m, pid) for m in w)),
    # v2.186.0: пул шире, у каждого игрока своя цель
    ("dry", "Выиграй партию, отдав не больше 5 очков", 1, lambda w, pid: sum(_dry_set_wins(m, pid) for m in w)),
    ("clean", "Выиграй матч, не отдав ни одной партии", 1, lambda w, pid: sum(_clean_win(m, pid) for m in w)),
    ("decider", "Выиграй матч в решающей партии", 1, lambda w, pid: sum(_decider_win(m, pid) for m in w)),
    ("rivals", "Сыграй с тремя разными соперниками", 3, _rivals_count),
    ("streak", "Выиграй 3 матча подряд", 3, _best_streak),
]
SKILL_BY_KEY = {s[0]: s for s in SKILLS}
OLD_SKILLS = ("deuce", "convert", "comeback")   # до v2.186.0: одна на весь клуб по номеру недели
# С понедельника 19.10.2026 (МСК) — своя цель каждому; прошлые недели считаются
# по-старому, чтобы «недель подряд» не пересчитались задним числом
PERSONAL_SKILLS_FROM = datetime(2026, 10, 18, 21, 0)

# Слабая сторона — по матчам за FOCUS_WEEKS до начала недели: доля удачных
# попыток, делённая на ориентир (типичную долю). Меньше всего — туда и цель.
# Ориентиры подобраны на глаз, как пороги архетипов, — пересматривать по данным.
FOCUS_WEEKS = 12
FOCUS_MIN_TRIES = 4
FOCUS_NOTE = "Здесь у тебя больше всего запаса — по последним матчам"
FOCUS_ANCHORS = {"deuce": 0.5, "convert": 0.75, "comeback": 0.3, "decider": 0.5, "dry": 0.3}


def _focus_rates(history: list[Match], pid: int) -> dict[str, float]:
    tries = {k: [0, 0] for k in FOCUS_ANCHORS}      # [удачно, попыток]
    for m in history:
        sets = _sets(m, pid)
        if not sets:
            continue
        for a, b in sets:
            if max(a, b) >= 12:
                tries["deuce"][1] += 1
                tries["deuce"][0] += a > b
            if a > b:
                tries["dry"][1] += 1
                tries["dry"][0] += b <= 5
        if len(sets) >= 2:
            first_won = sets[0][0] > sets[0][1]
            key = "convert" if first_won else "comeback"
            tries[key][1] += 1
            tries[key][0] += m.winner_id == pid
        if _decider(m, pid):
            tries["decider"][1] += 1
            tries["decider"][0] += m.winner_id == pid
    return {k: ok / n / FOCUS_ANCHORS[k] for k, (ok, n) in tries.items() if n >= FOCUS_MIN_TRIES}


def _weakest(history: list[Match], pid: int, start: datetime) -> str | None:
    since = start - timedelta(weeks=FOCUS_WEEKS)
    rates = _focus_rates([m for m in history if since <= m.completed_at < start], pid)
    return min(rates, key=lambda k: (rates[k], k)) if rates else None


def _shuffled(pid: int, week_no: int) -> list[str]:
    """Пул в своём порядке для игрока и недели — стабильно, без случайности."""
    return sorted(SKILL_BY_KEY, key=lambda k: hashlib.md5(f"{pid}:{week_no}:{k}".encode()).hexdigest())


def pick_skill(history: list[Match], pid: int, start: datetime) -> tuple[str, bool]:
    """Цель на мастерство недели: (ключ, по слабой стороне?). Слабая сторона — если
    она не та же, что на прошлой неделе (иначе одна и та же цель неделями);
    иначе — своя по порядку для игрока и недели, не повторяя прошлую."""
    week_no = int((start + MSK_OFFSET).strftime("%V"))
    if start < PERSONAL_SKILLS_FROM:
        return OLD_SKILLS[week_no % len(OLD_SKILLS)], False
    focus = _weakest(history, pid, start)
    prev = _weakest(history, pid, start - timedelta(weeks=1))
    if focus is not None and focus != prev:
        return focus, True
    avoid = {focus, prev}
    return next(k for k in _shuffled(pid, week_no) if k not in avoid), False


def _goals_for_week(history: list[Match], week: list[Match], pid: int, start: datetime,
                    names: dict[int, str]) -> list[dict]:
    """Три цели недели: по истории ДО начала недели, прогресс — по матчам недели."""
    recent = [m for m in history if m.completed_at >= start - timedelta(weeks=4)]
    per_week = len(recent) / 4
    target = max(MATCH_GOAL_MIN, min(MATCH_GOAL_MAX, round(per_week) + 1))
    goals = [{"key": "matches", "title": f"Сыграй {_ru_plural(target, 'матч', 'матча', 'матчей')}",
              "target": target, "progress": len(week)}]

    rival = revenge_rival(history, pid, start)
    if rival is not None:
        beat = sum(1 for m in week if m.winner_id == pid and rival in (m.challenger_id, m.challenged_id))
        goals.append({"key": "revenge", "title": f"Реванш: {names.get(rival, '?')}",
                      "note": "Выиграй у того, кому ещё не отыгрался", "target": 1, "progress": beat,
                      "player_id": rival})
    else:
        wins_target = max(2, min(10, round(sum(1 for m in recent if m.winner_id == pid) / 4) + 1))
        goals.append({"key": "wins", "title": f"Выиграй {_ru_plural(wins_target, 'матч', 'матча', 'матчей')}",
                      "target": wins_target, "progress": sum(1 for m in week if m.winner_id == pid)})

    skill, focused = pick_skill(history, pid, start)
    key, title, skill_target, progress = SKILL_BY_KEY[skill]
    goal = {"key": key, "title": title, "target": skill_target, "progress": int(progress(week, pid))}
    if focused:
        goal["note"] = FOCUS_NOTE
    goals.append(goal)
    for g in goals:
        g["progress"] = min(g["progress"], g["target"])
        g["done"] = g["progress"] >= g["target"]
    return goals


async def goals_payload(session: AsyncSession, viewer: Player) -> dict:
    now = _now()
    start = week_start(now)
    matches = list((await session.execute(
        select(Match).where(
            Match.status == MatchStatus.completed, Match.completed_at.is_not(None),
            or_(Match.challenger_id == viewer.id, Match.challenged_id == viewer.id),
        ).order_by(Match.completed_at)
    )).scalars().all())
    names = {p.id: p.display_name for p in (await session.execute(select(Player))).scalars().all()}

    def for_week(ws: datetime) -> list[dict]:
        we = ws + timedelta(weeks=1)
        history = [m for m in matches if m.completed_at < ws]
        week = [m for m in matches if ws <= m.completed_at < we]
        return _goals_for_week(history, week, viewer.id, ws, names)

    goals = for_week(start)
    streak = 0
    first = matches[0].completed_at if matches else now
    ws = start - timedelta(weeks=1)
    while streak < STREAK_MAX_WEEKS and ws + timedelta(weeks=1) > first:
        if not all(g["done"] for g in for_week(ws)):
            break
        streak += 1
        ws -= timedelta(weeks=1)

    days_left = (start + timedelta(weeks=1) - now).days
    left = "последний день" if days_left == 0 else f"осталось {_ru_plural(days_left, 'день', 'дня', 'дней')}"
    return {"title": "Цели недели", "left": left, "goals": goals,
            "all_done": all(g["done"] for g in goals), "streak": streak}

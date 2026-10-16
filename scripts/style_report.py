"""Разбор радара стиля по прод-базе — только чтение, игроки обезличены.

Для калибровки (v2.195.0): по каждому игроку от 5 матчей — все шесть
показателей карточки (ВИН, КЛЧ, ДОЖ, КМБ, ДОМ, СТБ) теми же функциями, что
приложение, выборка под каждым, цвет на карточке и архетип. v2.196.0: камбэки
уже по новой формуле (в КМБ); рядом — кандидат на замену ДОМ: «разгромные
партии» — доля выигранных партий, где соперник набрал 6 очков и меньше (у
нынешнего ДОМ весь клуб в 56–61). Внизу — сводка по осям
(мин / медиана / макс, сколько каких цветов) и сколько у кого какой архетип.
По выводу подбираются пороги архетипов и «коридоры» цветов так, чтобы
показатели различали игроков.

Запуск (на сервере, из папки бота):
  /opt/bottennis/.venv/bin/python scripts/style_report.py
База — из DATABASE_URL (.env рядом со скриптом) или --db путь/к/файлу.
Открывается только на чтение (mode=ro); имён в выводе нет — u1, u2, … по числу матчей.
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from export_usage import db_path_from_url, load_env  # noqa: E402

AXES = [("Винрейт", "ВИН"), ("Клатч", "КЛЧ"), ("Дожимание", "ДОЖ"), ("Камбэки", "КМБ"),
        ("Доминирование", "ДОМ"), ("Стабильность", "СТБ")]
COLOR = {"gray": "сер", "white": "бел", "green": "зел", "gold": "зол", "red": "крас"}


CRUSH_MAX = 6   # «разгромная» партия: соперник набрал не больше 6 очков


def crushing_sets(matches, pid: int) -> tuple[int, int]:
    """(разгромных, всего выигранных) партий игрока — кандидат на замену ДОМ."""
    crush = won = 0
    for m in matches:
        persp = m.winner_id if m.winner_id is not None else m.challenger_id
        for st in m.sets_data or []:
            mine, theirs = (st["w"], st["l"]) if persp == pid else (st["l"], st["w"])
            if mine > theirs:
                won += 1
                crush += theirs <= CRUSH_MAX
    return crush, won


def comeback_after_lost_first(matches, pid: int) -> tuple[int, int]:
    """(выиграно, всего) матчей от двух партий, где игрок проиграл первую партию.
    sets_data — в перспективе победителя, при ничьей — challenger."""
    won = total = 0
    for m in matches:
        sets = m.sets_data or []
        if len(sets) < 2:
            continue
        persp = m.winner_id if m.winner_id is not None else m.challenger_id
        first_mine = (sets[0]["w"], sets[0]["l"]) if persp == pid else (sets[0]["l"], sets[0]["w"])
        if first_mine[0] < first_mine[1]:
            total += 1
            won += m.winner_id == pid
    return won, total


async def report(db_path: Path) -> str:
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///file:{db_path.as_posix()}?mode=ro&uri=true"
    from sqlalchemy import select

    from bot.db.database import async_session
    from bot.db.models import Player
    from bot.services.stats import _build_style_radar, _style_archetype
    from bot.utils import get_career_matches
    from bot.webapp import style_tiers
    from bot.webapp.api import stats_cached

    rows = []
    async with async_session() as session:
        ref = await style_tiers.club_reference(session)
        # по id — чтобы нумерация u1, u2… совпадала с quarterly_report.py при равном числе матчей
        for p in (await session.execute(select(Player).order_by(Player.id))).scalars().all():
            matches = await get_career_matches(session, p.id, with_opponents=True)
            if len(matches) < style_tiers.MIN_MATCHES_FOR_RADAR:
                continue
            s = stats_cached(p, matches)
            radar = _build_style_radar(s)
            if not radar:
                continue
            tiers = {a: style_tiers.tier(a, radar[a], s, p.id, ref)[0] for a, _ in AXES}
            samples = {a: style_tiers._sample(a, s)[0] for a, _ in AXES}
            rows.append((len(matches), radar, tiers, samples, _style_archetype(radar, s),
                         crushing_sets(matches, p.id)))
    rows.sort(key=lambda r: -r[0])

    out = [f"Игроков от {style_tiers.MIN_MATCHES_FOR_RADAR} матчей: {len(rows)}", ""]
    out.append("Игрок матчей | " + " | ".join(f"{short} знач/выборка/цвет" for _, short in AXES)
               + " | ДОМ-кандидат: разгромные партии | архетип")
    for i, (n, radar, tiers, samples, arch, (cw, ct)) in enumerate(rows, 1):
        cells = [f"{short} {radar[a]:.0f}/{samples[a]}/{COLOR[tiers[a]]}" for a, short in AXES]
        new = f"{round(cw / ct * 100)}% ({cw} из {ct})" if ct else "—"
        out.append(f"u{i:<3} {n:>4} | " + " | ".join(cells) + f" | {new} | {arch}")
    out += ["", "Сводка по осям (только игроки с достаточной выборкой): мин / медиана / макс; цвета"]
    for a, short in AXES:
        vals = [r[1][a] for r in rows if r[2][a] != "gray"]
        colors = Counter(COLOR[r[2][a]] for r in rows)
        spread = f"{min(vals):.0f} / {median(vals):.0f} / {max(vals):.0f}" if vals else "нет данных"
        out.append(f"{short}: {spread}; " + ", ".join(f"{c} {k}" for c, k in colors.most_common()))
    new_vals = [round(cw / ct * 100) for *_, (cw, ct) in rows if ct >= 10]
    if new_vals:
        out.append(f"ДОМ-кандидат, разгромные партии (от 10 выигранных партий): {min(new_vals)} / "
                   f"{median(new_vals):.0f} / {max(new_vals)}")
    out += ["", "Архетипы: " + ", ".join(f"{a} {k}" for a, k in Counter(r[4] for r in rows).most_common())]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    args = ap.parse_args()
    load_env()
    db = Path(args.db) if args.db else db_path_from_url(os.environ.get("DATABASE_URL", "sqlite:///bottennis.db"))
    if not db.exists():
        raise SystemExit(f"Нет файла базы: {db}")
    print(asyncio.run(report(db)))


if __name__ == "__main__":
    main()

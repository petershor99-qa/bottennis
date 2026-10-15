"""
v2.186.0: цели недели — свой пул у каждого игрока, цель по слабой стороне;
всё приложение во весь экран; мячик при обновлении; партии по очереди на
экране матча; покрытие тестами в CI.
Запуск: pytest tests/test_v2186.py
"""
from datetime import timedelta
from pathlib import Path

import bot.webapp.api_return as ret
from tests.conftest import _player
from tests.test_come_back import _club, _m

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")
NEW_WEEK = ret.PERSONAL_SKILLS_FROM + timedelta(weeks=2)     # понедельник 02.11 00:00 МСК


def _pair():
    a, b = _player(1, "A"), _player(2, "B")
    a.id, b.id = 1, 2
    return a, b


# ── Счётчики новых целей ──────────────────────────────────────────────────────

def test_new_skill_counters():
    a, b = _pair()
    t = NEW_WEEK
    clean = _m(a, b, 1, [(11, 5), (11, 9)], t)
    decider = _m(a, b, 1, [(11, 9), (8, 11), (11, 7)], t + timedelta(hours=1))
    lost = _m(a, b, 2, [(11, 4), (11, 3)], t + timedelta(hours=2))
    assert ret._dry_set_wins(clean, 1) == 1 and ret._dry_set_wins(lost, 2) == 2 and ret._dry_set_wins(lost, 1) == 0
    assert ret._clean_win(clean, 1) and not ret._clean_win(decider, 1)
    assert ret._decider_win(decider, 1) and not ret._decider_win(clean, 1)
    assert not ret._decider(_m(a, b, 1, [(11, 9), (11, 8), (5, 11)], t), 1)   # 2:0 перед третьей — не решающая
    week = [clean, decider, lost]
    assert ret._best_streak(week, 1) == 2 and ret._rivals_count(week, 1) == 1
    keys = {s[0] for s in ret.SKILLS}
    assert keys == {"deuce", "convert", "comeback", "dry", "clean", "decider", "rivals", "streak"}


# ── Выбор цели ────────────────────────────────────────────────────────────────

def test_old_weeks_keep_club_wide_rotation():
    """До 19.10 — прежняя ротация на весь клуб: «недель подряд» не пересчитываются."""
    start = ret.PERSONAL_SKILLS_FROM - timedelta(weeks=1)
    week_no = int((start + ret.MSK_OFFSET).strftime("%V"))
    for pid in (1, 2, 3):
        assert ret.pick_skill([], pid, start) == (ret.OLD_SKILLS[week_no % 3], False)


def test_weak_side_becomes_the_goal_with_note():
    a, b = _pair()
    # 5 дьюсов проиграно, остальное в порядке: слабая сторона — дьюсы, возникла на этой неделе
    history = [_m(a, b, 2, [(12, 10)], NEW_WEEK - timedelta(days=d, hours=1)) for d in range(1, 6)]
    skill, focused = ret.pick_skill(history, 1, NEW_WEEK)
    assert (skill, focused) == ("deuce", True)
    goals = ret._goals_for_week(history, [], 1, NEW_WEEK, {2: "B"})
    assert goals[2]["key"] == "deuce" and goals[2]["note"] == ret.FOCUS_NOTE


def test_same_weak_side_two_weeks_in_a_row_gives_another_goal():
    a, b = _pair()
    history = [_m(a, b, 2, [(12, 10)], NEW_WEEK - timedelta(weeks=2, days=d)) for d in range(1, 6)]
    skill, focused = ret.pick_skill(history, 1, NEW_WEEK)
    assert skill != "deuce" and focused is False


def test_without_history_goals_differ_between_players():
    picks = {ret.pick_skill([], pid, NEW_WEEK)[0] for pid in range(1, 9)}
    assert len(picks) >= 3
    assert ret.pick_skill([], 5, NEW_WEEK) == ret.pick_skill([], 5, NEW_WEEK)    # стабильно


async def test_goals_payload_new_week_progress(db, monkeypatch):
    a, b, c = await _club(db)
    monkeypatch.setattr(ret, "_now", lambda: NEW_WEEK + timedelta(days=2))
    db.add_all([
        _m(a, b, a.id, [(11, 5), (11, 4)], NEW_WEEK + timedelta(hours=10)),
        _m(a, c, a.id, [(11, 9), (9, 11), (11, 7)], NEW_WEEK + timedelta(hours=11)),
    ])
    await db.commit()
    g = await ret.goals_payload(db, a)
    skill = g["goals"][2]
    expected = {"deuce": 0, "convert": 1, "comeback": 0, "dry": 1, "clean": 1, "decider": 1, "rivals": 2, "streak": 2}
    assert skill["progress"] == min(expected[skill["key"]], skill["target"])


# ── Страница ──────────────────────────────────────────────────────────────────

def test_fullscreen_everywhere_wiring():
    new = (SRC / "25-new.js").read_text(encoding="utf-8")
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert "function setupFullscreen" in new and 'FS_KEY = "fullscreen_desktop_v1"' in new
    assert "setupFullscreen();" in routes and "!wantFullscreen()) leaveFullscreen();" in routes
    assert ".fs-toggle {" in CSS and "--top-safe" in CSS and "html.is-fullscreen .tabbar" in CSS
    icons = (ROOT / "webapp" / "static" / "icons.svg").read_text(encoding="utf-8")
    assert 'id="ti-maximize"' in icons and 'id="ti-minimize"' in icons


def test_pull_ball_and_set_replay_wiring():
    gestures = (SRC / "22-gestures.js").read_text(encoding="utf-8")
    assert "function ballHeight" in gestures and "dropScene(mark, true)" in gestures
    assert "@keyframes ball-fly" in CSS and ".pull-mark" not in CSS
    match = (SRC / "20-match.js").read_text(encoding="utf-8")
    assert "function replaySets" in match and "replaySeen(d.id)" in match and "!reduceMotion" in match
    assert ".versus.replaying .mood-stamp { display: none; }" in CSS


def test_ci_prints_coverage():
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "pytest -v --cov=bot --cov-report=term" in wf and "GITHUB_STEP_SUMMARY" in wf
    assert "pytest-cov==" in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


# ── Первый запуск легче: без строк-комментариев в app.js ─────────────────────

def test_served_app_js_has_no_line_comments_and_still_parses(tmp_path):
    import shutil
    import subprocess

    from bot.webapp.assets import Assets, strip_line_comments
    from bot.webapp.server import STATIC_DIR

    src = (STATIC_DIR / "app.js").read_bytes()
    assert b"`" not in src                       # без шаблонных строк — вырезать безопасно
    assert strip_line_comments(b"  // x\nvar a = 1; // y\n//z\nb();\n") == b"var a = 1; // y\nb();\n"
    served = Assets(STATIC_DIR).get("app.js").body
    assert len(served) < len(src) * 0.9
    assert not any(line.lstrip().startswith(b"//") for line in served.splitlines())
    node = shutil.which("node")
    if node:
        f = tmp_path / "served.js"
        f.write_bytes(served)
        assert subprocess.run([node, "--check", str(f)], capture_output=True).returncode == 0

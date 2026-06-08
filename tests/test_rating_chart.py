"""
Тесты графика рейтинга (build_rating_series + rating_chart_url).
Запуск: pytest tests/test_rating_chart.py
"""
import json
import urllib.parse
from datetime import datetime
from types import SimpleNamespace

from bot.utils import build_rating_series, rating_chart_url


def mk(winner_id, challenger_id, challenged_id, rating_change, day):
    return SimpleNamespace(
        winner_id=winner_id,
        challenger_id=challenger_id,
        challenged_id=challenged_id,
        rating_change=rating_change,
        completed_at=datetime(2026, 5, day, 12, 0, 0),
    )


# ── build_rating_series ───────────────────────────────────────────────────────

def test_series_anchored_to_current():
    """Последняя точка ряда точно равна текущему рейтингу, ряд восстановлен назад."""
    matches = [
        mk(winner_id=1, challenger_id=1, challenged_id=2, rating_change=10.0, day=1),  # +10
        mk(winner_id=2, challenger_id=1, challenged_id=2, rating_change=8.0, day=2),   # -8
        mk(winner_id=1, challenger_id=1, challenged_id=2, rating_change=12.0, day=3),  # +12
    ]
    labels, values = build_rating_series(matches, player_id=1, current_rating=1020.0)
    assert values == [1016.0, 1008.0, 1020.0]
    assert values[-1] == 1020.0
    assert labels == ["01.05", "02.05", "03.05"]


def test_series_draw_delta():
    """Ничья: дельта challenger = +rating_change, challenged = -rating_change."""
    matches = [
        mk(winner_id=None, challenger_id=1, challenged_id=2, rating_change=3.0, day=1),
    ]
    # игрок 1 — challenger: после ничьей +3, значит до = current-3
    _, v_challenger = build_rating_series(matches, player_id=1, current_rating=1003.0)
    assert v_challenger == [1003.0]
    # игрок 2 — challenged: дельта -3
    _, v_challenged = build_rating_series(matches, player_id=2, current_rating=997.0)
    assert v_challenged == [997.0]


def test_series_limit_keeps_recent():
    """limit оставляет только последние N матчей, но последняя точка = current."""
    matches = [
        mk(winner_id=1, challenger_id=1, challenged_id=2, rating_change=10.0, day=1),
        mk(winner_id=2, challenger_id=1, challenged_id=2, rating_change=8.0, day=2),
        mk(winner_id=1, challenger_id=1, challenged_id=2, rating_change=12.0, day=3),
    ]
    labels, values = build_rating_series(matches, player_id=1, current_rating=1020.0, limit=2)
    assert values == [1008.0, 1020.0]
    assert labels == ["02.05", "03.05"]


def test_series_single_match():
    matches = [mk(winner_id=1, challenger_id=1, challenged_id=2, rating_change=15.0, day=5)]
    labels, values = build_rating_series(matches, player_id=1, current_rating=1015.0)
    assert values == [1015.0]
    assert labels == ["05.05"]


# ── rating_chart_url ──────────────────────────────────────────────────────────

def _decode_config(url: str) -> dict:
    query = urllib.parse.urlparse(url).query
    params = urllib.parse.parse_qs(query)
    return json.loads(params["c"][0])


def test_chart_url_basic():
    url = rating_chart_url("Игрок A", ["01.05", "02.05"], [1010.0, 1020.0])
    assert url.startswith("https://quickchart.io/chart?")
    cfg = _decode_config(url)
    assert cfg["type"] == "line"
    assert cfg["data"]["datasets"][0]["data"] == [1010.0, 1020.0]
    assert cfg["data"]["labels"] == ["01.05", "02.05"]


def test_chart_url_embeds_name():
    url = rating_chart_url("Вася", ["01.05"], [1000.0])
    cfg = _decode_config(url)
    assert "Вася" in cfg["options"]["title"]["text"]

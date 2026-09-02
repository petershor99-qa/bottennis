"""
Тесты тепловой карты активности (activity_counts_by_day + activity_heatmap_url).
Запуск: pytest tests/test_activity_heatmap.py
"""
import json
import urllib.parse
from datetime import datetime
from types import SimpleNamespace

from bot.utils import (
    HEATMAP_DAYS,
    MSK_OFFSET,
    _heatmap_tier,
    activity_counts_by_day,
    activity_heatmap_url,
)


def mk(day: int, hour: int = 12) -> SimpleNamespace:
    return SimpleNamespace(completed_at=datetime(2026, 6, day, hour, 0, 0))


# ── _heatmap_tier ────────────────────────────────────────────────────────────

def test_heatmap_tier_boundaries():
    assert _heatmap_tier(0) == 0
    assert _heatmap_tier(1) == 1
    assert _heatmap_tier(2) == 2
    assert _heatmap_tier(3) == 2
    assert _heatmap_tier(4) == 3
    assert _heatmap_tier(100) == 3


# ── activity_counts_by_day ───────────────────────────────────────────────────

def test_counts_by_day_sums_same_day_matches():
    matches = [mk(1), mk(1), mk(2)]
    counts = activity_counts_by_day(matches)
    assert len(counts) == 2
    assert sum(counts.values()) == 3


def test_counts_by_day_skips_uncompleted():
    """Матч без completed_at (не должен встречаться в реальных данных для
    завершённых матчей, но на всякий случай не должен падать)."""
    matches = [mk(1), SimpleNamespace(completed_at=None)]
    counts = activity_counts_by_day(matches)
    assert sum(counts.values()) == 1


def test_counts_by_day_late_night_msk_crosses_utc_date():
    """21:30 UTC = 00:30 МСК следующего дня — должен посчитаться в МСК-дне,
    а не в UTC-дне (та же граница «сегодня», что и везде в проекте)."""
    late_utc = SimpleNamespace(completed_at=datetime(2026, 6, 1, 21, 30, 0))
    counts = activity_counts_by_day([late_utc])
    from datetime import date
    assert date(2026, 6, 2) in counts  # ушло на МСК-день вперёд


# ── activity_heatmap_url ──────────────────────────────────────────────────────

def _decode_config(url: str) -> dict:
    query = urllib.parse.urlparse(url).query
    c_param = urllib.parse.parse_qs(query)["c"][0]
    return json.loads(c_param)


def test_heatmap_url_is_valid_quickchart_bubble_chart():
    counts = {}
    url = activity_heatmap_url("Тест", counts)
    assert url.startswith("https://quickchart.io/chart?")
    config = _decode_config(url)
    assert config["type"] == "bubble"


def test_heatmap_url_title_present():
    counts = {}
    url = activity_heatmap_url("Моя активность — 90 дней", counts)
    config = _decode_config(url)
    assert config["options"]["title"]["text"] == "Моя активность — 90 дней"


def test_heatmap_empty_counts_all_points_in_zero_tier():
    """Без матчей вообще — все точки в нулевом (серый) тир, один датасет."""
    url = activity_heatmap_url("Тест", {})
    config = _decode_config(url)
    datasets = config["data"]["datasets"]
    assert len(datasets) == 1
    assert datasets[0]["backgroundColor"] == "rgba(230,230,230,1)"


def test_heatmap_total_points_matches_window_size():
    """Суммарное число точек по всем датасетам равно окну в днях — ровно
    одна точка на календарный день, ни одна не потеряна и не задвоена."""
    counts = {}
    url = activity_heatmap_url("Тест", counts, days=HEATMAP_DAYS)
    config = _decode_config(url)
    total_points = sum(len(ds["data"]) for ds in config["data"]["datasets"])
    assert total_points == HEATMAP_DAYS


def test_heatmap_high_count_day_lands_in_top_tier():
    """days-окно считается от РЕАЛЬНОГО «сейчас» внутри функции — дата в
    фикстуре должна попадать в это окно, иначе точка не попадёт в сетку."""
    from datetime import timezone
    real_today = (datetime.now(timezone.utc) + MSK_OFFSET).date()
    counts = {real_today: 10}
    url = activity_heatmap_url("Тест", counts, days=30)
    config = _decode_config(url)
    # верхний тир (индекс совпадает с порядком добавления — только непустые тиры
    # присутствуют, но тир "4+" последний среди непустых) содержит ровно 1 точку
    top_tier_colors = [ds["backgroundColor"] for ds in config["data"]["datasets"]]
    assert "rgba(20,100,50,1)" in top_tier_colors
    top_ds = next(ds for ds in config["data"]["datasets"] if ds["backgroundColor"] == "rgba(20,100,50,1)")
    assert len(top_ds["data"]) == 1

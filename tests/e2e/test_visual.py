"""
Скриншот-тесты Mini App (v2.177.0): экран сравнивается с эталоном попиксельно,
даты и время замаскированы (данные превью привязаны к текущему моменту).
Эталоны свои для каждой системы (шрифты рисуются по-разному): лежат в
tests/e2e/baselines/<система>/. Нет эталона — он создаётся, тест пропускается.
Обновить после намеренной правки вида: UPDATE_BASELINES=1.

Запускаются локально перед релизом (скилл `app-check`); в CI — только проход
по экранам (test_app_e2e.py): эталоны с Windows на Linux не совпадут.
"""
import io
import os
import sys
from pathlib import Path

import pytest

from tests.e2e.conftest import open_page, wait_screen

Image = pytest.importorskip("PIL.Image")
ImageChops = pytest.importorskip("PIL.ImageChops")

if os.environ.get("CI"):
    pytest.skip("скриншот-тесты — только локально", allow_module_level=True)

BASELINES = Path(__file__).parent / "baselines" / sys.platform
# Экраны, которые не зависят от «сегодня»: всё время клуба, конкретный матч
SCREENS = {"scores": "scores", "records": "records", "match": "match/96", "h2h": "h2h/3", "throne": "throne"}
MASK = ".sub, .screen-sub, .feed-time, .eyebrow-meta, .chart-label, .sheet-meta, .day-label"
MAX_DIFF = 0.01   # доля различающихся пикселей
CHANNEL_TOLERANCE = 24


def _diff_ratio(a, b) -> float:
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    changed = sum(diff.histogram()[CHANNEL_TOLERANCE + 1:])
    return changed / (a.size[0] * a.size[1])


@pytest.mark.parametrize("name", list(SCREENS))
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_screen_matches_baseline(browser, base_url, name, dark):
    page = open_page(browser, f"{base_url}/preview{'?dark=1' if dark else ''}#/{SCREENS[name]}", dark=dark)
    wait_screen(page)
    page.evaluate("document.fonts.ready")
    shot = page.screenshot(full_page=True, mask=[page.locator(MASK)], animations="disabled")
    page.context.close()
    path = BASELINES / f"{name}-{'dark' if dark else 'light'}.png"
    if os.environ.get("UPDATE_BASELINES") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(shot)
        pytest.skip(f"эталон записан: {path.name}")
    ratio = _diff_ratio(Image.open(path), Image.open(io.BytesIO(shot)))
    if ratio > MAX_DIFF:
        (path.parent / f"{path.stem}.actual.png").write_bytes(shot)
    assert ratio <= MAX_DIFF, f"{name}: отличается {ratio:.1%} пикселей — см. {path.stem}.actual.png"

"""
v2.194.0: загрузчик Mini App первым в <head> (полоска Telegram не ждёт стили и
скрипт Telegram), замер сети в журнале запуска, прощальная карточка с картинкой
в «Твоём месяце».
Запуск: pytest tests/test_v2194.py
"""
import gzip
import logging
from datetime import timedelta
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

import bot.webapp.usage as usage
from bot.utils import msk_day_start
from bot.webapp.api_month import month_payload, outro_card
from bot.webapp.server import create_app
from bot.webapp.usage import log_boot_failure, log_boot_time, net_line
from tests.conftest import _completed, _player, freeze_clock
from tests.test_webapp import ADMIN

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webapp" / "static"
SRC = ROOT / "webapp" / "src" / "app"
TODAY = msk_day_start() + timedelta(hours=1)


@pytest.fixture(autouse=True)
def _clock_after_today(monkeypatch):
    """Часы кода под тестом стоят на TODAY + 2 ч — «текущий месяц» тот же, что у данных."""
    freeze_clock(monkeypatch, TODAY + timedelta(hours=2))


@pytest.fixture(autouse=True)
def _fresh_failures():
    usage._last_failure.clear()
    yield
    usage._last_failure.clear()


# ── Замер сети в журнале ──────────────────────────────────────────────────────

def test_net_line_lists_steps_and_missing_files():
    line = net_line({"dns": 12, "conn": 80, "tls": 40, "ttfb": 150, "html": 300, "css": 520.6})
    assert line == ("сеть: адрес 12 мс, соединение 80 мс, TLS 40 мс, первый байт 150 мс, "
                    "страница 300 мс, стили 520 мс; не пришли: скрипт Telegram, app.js")


def test_net_line_all_files_came():
    line = net_line({"html": 300, "css": 500, "tg": 900, "app": 700})
    assert line.endswith("скрипт Telegram 900 мс, app.js 700 мс") and "не пришли" not in line


@pytest.mark.parametrize("bad", [None, {}, "сеть", [1, 2]])
def test_net_line_without_measure_is_empty(bad):
    assert net_line(bad) == ""


def test_net_line_drops_garbage_values():
    line = net_line({"dns": "<script>", "conn": -5, "html": 10**9, "css": True, "ttfb": 40})
    assert line == "сеть: первый байт 40 мс; не пришли: стили, скрипт Telegram, app.js"


def test_boot_time_log_has_network(caplog):
    with caplog.at_level(logging.INFO, logger="bot.webapp.usage"):
        log_boot_time("home", 30231, {"platform": "android", "version": "9.6", "tg_ms": 29856,
                                      "net": {"dns": 5, "html": 29000, "css": 29500, "tg": 29800, "app": 29700}})
        log_boot_time("home", 900, {"platform": "android", "version": "9.6"})   # старая страница без замера
    first, second = [r.getMessage() for r in caplog.records]
    assert first.endswith("скрипт Telegram 29856 мс; сеть: адрес 5 мс, страница 29000 мс, стили 29500 мс, "
                          "скрипт Telegram 29800 мс, app.js 29700 мс")
    assert second.endswith("скрипт Telegram не пришёл")


def test_boot_failure_log_shows_where_it_stalled(caplog):
    body = {"stage": "page_pending", "tg": False, "platform": "android", "version": "9.6",
            "net": {"dns": 3, "conn": 60, "ttfb": 200, "html": 250}}
    with caplog.at_level(logging.WARNING, logger="bot.webapp.usage"):
        assert log_boot_failure(42, body) is True
    msg = caplog.records[0].getMessage()
    assert "этап «page_pending»" in msg
    assert msg.endswith("не пришли: стили, скрипт Telegram, app.js")


# ── Страница ──────────────────────────────────────────────────────────────────

def test_app_sends_network_measure_with_first_screen():
    api_js = (SRC / "02-api.js").read_text(encoding="utf-8")
    assert "body.net = window.__clubBoot.timing()" in api_js


def test_loader_measures_network_and_reports_page_stage():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    loader = html[html.index("(function () {"):html.index("</script>")]
    for key in ("domainLookupEnd", "secureConnectionStart", "responseStart", 'getEntriesByType("resource")'):
        assert key in loader
    assert "net: boot.timing()" in loader
    # сторож отличает «страница не дошла до app.js» от «app.js не пришёл»
    assert '"page_pending"' in loader and '"app_js_pending"' in loader


# ── «Твой месяц»: прощальная карточка ─────────────────────────────────────────

def test_outro_card_texts():
    done = outro_card(23, 14, 2026, 11, finished=True)
    assert done["kind"] == "outro" and done["art"] is True and done["title"] == "Месяц закрыт"
    assert done["lines"] == ["23 матча · 14 побед", "До встречи в ноябре!"]
    going = outro_card(1, 0, 2026, 11, finished=False)
    assert going["title"] == "Месяц ещё идёт" and going["lines"] == ["1 матч · 0 побед", "Увидимся за столом!"]


async def _pair(db):
    a, b = _player(ADMIN, "Алиса"), _player(2002, "Боб")
    db.add_all([a, b])
    await db.flush()
    return a, b


async def test_month_ends_with_outro_current_month(db):
    a, b = await _pair(db)
    db.add_all([_completed(a, b, a.id, 10.0, TODAY), _completed(b, a, b.id, 5.0, TODAY + timedelta(minutes=5))])
    await db.commit()
    cards = (await month_payload(db, a, None))["cards"]
    assert cards[-1]["kind"] == "outro" and cards[-1]["title"] == "Месяц ещё идёт"
    assert cards[-1]["lines"][0] == "2 матча · 1 победа"
    assert [c["kind"] for c in cards].count("outro") == 1


async def test_month_outro_for_finished_month_says_see_you_next(db):
    a, b = await _pair(db)
    old = msk_day_start() - timedelta(days=40)
    db.add(_completed(a, b, a.id, 10.0, old))
    await db.commit()
    d = await month_payload(db, a, None)       # в этом месяце не играл — прошлый (закрытый)
    outro = d["cards"][-1]
    assert outro["title"] == "Месяц закрыт" and outro["lines"][1].startswith("До встречи в ")


async def test_empty_month_has_no_outro(db):
    a, _ = await _pair(db)
    await db.commit()
    d = await month_payload(db, a, (TODAY + timedelta(hours=3)).strftime("%Y%m"))
    assert d["cards"] == [] and d["empty"]


def test_story_card_renders_splash_art():
    month_js = (SRC / "21-month.js").read_text(encoding="utf-8")
    card = month_js[month_js.index("function storyCard(card)"):month_js.index("function storySpark")]
    assert "if (card.art)" in card and "splashUrl()" in card and '"story-art"' in card
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".story-art {" in css and ".story-outro {" in css
    # «уменьшить движение» — без анимации картинки
    assert ".boot-pic, .story-card, .story-art," in css


# ── index.html сжата ──────────────────────────────────────────────────────────

@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    async with TestClient(TestServer(create_app("123456:TEST-token", db_factory))) as c:
        yield c


async def test_index_is_gzipped_when_accepted(client):
    """РЕГРЕССИЯ v2.194.0: index.html не кэшируется и приходит при каждом запуске,
    а отдавалась без сжатия (9,5 КБ после переноса загрузчика) — повторный запуск
    вышел за бюджет 8 КБ (e2e test_boot_budget)."""
    async with ClientSession(auto_decompress=False) as raw:
        resp = await raw.get(client.make_url("/"), headers={"Accept-Encoding": "gzip"})
        body = await resp.read()
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert resp.headers.get("Cache-Control") == "no-store"
    html = gzip.decompress(body).decode("utf-8")
    assert "window.__clubBoot" in html and len(body) < 6 * 1024


async def test_index_plain_without_gzip(client):
    async with ClientSession(auto_decompress=False) as raw:
        resp = await raw.get(client.make_url("/"), headers={"Accept-Encoding": "identity"})
        body = await resp.read()
    assert "Content-Encoding" not in resp.headers
    assert b"window.__clubBoot" in body

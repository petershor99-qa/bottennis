"""
Пакет 3, релиз 2 (v2.172.0): статика Mini App с версией в адресе и сжатием,
сжатие крупных ответов API, старт без ожидания /api/me, предзагрузка вкладок.
Запуск: pytest tests/test_app_speed.py
"""
import gzip
import os
import re
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from bot.webapp.assets import IMMUTABLE, Assets
from bot.webapp.server import STATIC_DIR, create_app
from tests.conftest import _player
from tests.test_webapp import ADMIN, TOKEN, _init_data

RAW = {"Accept-Encoding": "identity"}


@pytest.fixture
async def client(db_factory, monkeypatch):
    monkeypatch.setenv("WEBAPP_URL", "https://club.example/")
    monkeypatch.setenv("ADMIN_ID", str(ADMIN))
    async with db_factory() as s:
        s.add(_player(ADMIN, "Алиса"))
        await s.commit()
    async with TestClient(TestServer(create_app(TOKEN, db_factory))) as c:
        yield c


def _versions(html: str) -> dict[str, str]:
    return dict(re.findall(r"static/(app\.(?:css|js))\?v=([0-9a-f]{12})", html))


async def test_index_links_current_versions(client):
    html = await (await client.get("/")).text()
    versions = _versions(html)
    assert set(versions) == {"app.css", "app.js"}
    assets = Assets(STATIC_DIR)
    assert versions["app.js"] == assets.get("app.js").version
    assert versions["app.css"] == assets.get("app.css").version


async def test_versioned_asset_is_immutable_and_gzipped(client):
    html = await (await client.get("/")).text()
    v = _versions(html)["app.js"]
    # Сырые байты: собственная сессия без автоматической распаковки
    async with ClientSession(auto_decompress=False) as raw:
        resp = await raw.get(client.make_url(f"/static/app.js?v={v}"), headers={"Accept-Encoding": "gzip"})
        body = await resp.read()
    assert resp.headers["Cache-Control"] == IMMUTABLE
    assert resp.headers["Content-Encoding"] == "gzip"
    plain = gzip.decompress(body)
    assert len(body) < len(plain) / 2                    # сжатие ощутимое
    assert b'"static/icons.svg?v=' in plain                # значки тоже с версией


@pytest.mark.parametrize("query", ["", "?v=000000000000"])
async def test_unversioned_or_stale_asset_revalidates(client, query):
    resp = await client.get(f"/static/app.css{query}", headers=RAW)
    assert resp.headers["Cache-Control"] == "no-cache"
    assert "Content-Encoding" not in resp.headers
    etag = resp.headers["ETag"]
    again = await client.get(f"/static/app.css{query}", headers={"If-None-Match": etag})
    assert again.status == 304


async def test_icons_and_fonts_cache_headers(client):
    icons = await client.get("/static/icons.svg")
    assert icons.status == 200 and icons.headers["Content-Type"].startswith("image/svg+xml")
    font = await client.get("/static/fonts/Raleway-Subset.woff2")
    assert font.status == 200 and font.headers["Cache-Control"] == "public, max-age=2592000"


async def test_other_static_still_served(client):
    resp = await client.get("/static/icons-LICENSE.txt")
    assert resp.status == 200 and resp.headers["Cache-Control"] == "no-cache"


async def test_big_json_is_gzipped_small_is_not(client):
    auth = {"Authorization": "tma " + _init_data(ADMIN), "Accept-Encoding": "gzip"}
    big = await client.get("/api/player/1/achievements", headers=auth)
    assert big.status == 200 and big.headers.get("Content-Encoding") == "gzip"
    assert (await big.json())["categories"]               # клиент распаковал — данные целы
    small = await client.get("/api/me", headers=auth)
    assert "Content-Encoding" not in small.headers


def test_assets_reload_when_file_changes(tmp_path: Path):
    for name, text in {"app.js": 'var x = "static/icons.svg#ti-a";', "app.css": "a{}",
                       "icons.svg": "<svg/>", "telegram-web-app.js": "var t;",
                       "index.html": '<link href="static/app.css">'}.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    assets = Assets(tmp_path)
    first = assets.get("app.js").version
    (tmp_path / "icons.svg").write_text("<svg><g/></svg>", encoding="utf-8")
    st = (tmp_path / "icons.svg").stat()
    os.utime(tmp_path / "icons.svg", (st.st_atime, st.st_mtime + 10))
    assert assets.get("app.js").version != first           # новая версия значков -> новый app.js
    assert "?v=" in assets.index_html().decode()


def test_boot_requests_first_screen_in_parallel_and_prefetches_tabs():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    boot = js[js.rindex("routeJobs(currentRoute())"):]
    assert boot.index("routeJobs(currentRoute())") < boot.index('api("me")')
    assert "prefetchTabs()" in boot


def test_ci_checks_app_script_syntax():
    ci = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "node --check webapp/static/app.js" in ci

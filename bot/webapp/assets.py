"""
Статика Mini App с версией в адресе и сжатием (v2.172.0).

Раньше каждый запуск приложения заново спрашивал сервер про app.js, app.css и
icons.svg (`Cache-Control: no-cache` + ETag, v2.164.0) — по запросу на файл,
а файлы уходили несжатыми (78 + 28 + 15 КБ). Теперь:
- index.html ссылается на файлы с версией: `static/app.js?v=<хеш содержимого>`;
  такой адрес браузер кэширует навсегда (`immutable`), а после выкладки новый
  хеш даёт новый адрес — старая страница с новым API не встретится;
- без версии (или со старой) файл отдаётся по-прежнему с `no-cache` и ETag;
- если браузер принимает gzip, тело сжато заранее, один раз при загрузке.
Файлы держатся в памяти и перечитываются, если поменялись на диске (превью
подхватывает правки без перезапуска).

v2.186.0: из app.js при раздаче убираются строки, целиком состоящие из
комментария (`// …`), — около 9 КБ сжатого первого запуска. Файл на диске и
исходники в webapp/src остаются с комментариями. Безопасно, пока в app.js нет
шаблонных строк в обратных кавычках (строка внутри них могла бы начинаться с
`//`) — это проверяет тест.
"""
import gzip
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

# Файлы, которые отдаёт этот модуль; остальное (шрифты, лицензии) — add_static
ASSET_TYPES = {
    "app.js": "application/javascript",
    "app.css": "text/css",
    "icons.svg": "image/svg+xml",
    # Копия скрипта Telegram (v2.178.0, scripts/update_telegram_js.py): без
    # зависимости от telegram.org, которая держала заставку Telegram при запуске
    "telegram-web-app.js": "application/javascript",
    # Картинка заставки (v2.188.0): с версией — повторный запуск её не перепроверяет
    "splash.webp": "image/webp",
    # Трон в «Зале славы» (v2.191.0, картинка владельца); адрес с версией — в ответе /api/throne
    "throne.webp": "image/webp",
}
_NO_GZIP = {"splash.webp", "throne.webp"}   # уже сжато — gzip только тратит время
IMMUTABLE = "public, max-age=31536000, immutable"
_LINE_COMMENT = re.compile(rb"(?m)^[ \t]*//[^\n]*\n")


def strip_line_comments(js: bytes) -> bytes:
    """Убрать строки, где только комментарий `// …` (не трогая код с комментарием в конце)."""
    return _LINE_COMMENT.sub(b"", js)


@dataclass
class Asset:
    body: bytes
    gz: bytes
    version: str
    content_type: str
    mtime: float

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def accepts_gzip(request: web.Request) -> bool:
    return "gzip" in request.headers.get("Accept-Encoding", "").lower()


class Assets:
    def __init__(self, static_dir: Path):
        self.dir = static_dir
        self._cache: dict[str, Asset] = {}

    def _mtime(self, name: str) -> float:
        return (self.dir / name).stat().st_mtime

    def get(self, name: str) -> Asset:
        """Файл из памяти; перечитывается, если поменялся на диске."""
        mtime = self._mtime(name)
        if name == "app.js":
            # app.js ссылается на icons.svg — его версия входит в app.js
            mtime = max(mtime, self._mtime("icons.svg"))
        cached = self._cache.get(name)
        if cached is not None and cached.mtime == mtime:
            return cached
        body = (self.dir / name).read_bytes()
        if name == "app.js":
            body = strip_line_comments(body)
            icons = self.get("icons.svg").version
            body = body.replace(b'"static/icons.svg#', f'"static/icons.svg?v={icons}#'.encode())
        asset = Asset(body=body, gz=gzip.compress(body, 9, mtime=0), version=_version(body),
                      content_type=ASSET_TYPES[name], mtime=mtime)
        self._cache[name] = asset
        return asset

    def index_html(self) -> bytes:
        """index.html со ссылками на текущие версии app.css, app.js и скрипта Telegram."""
        html = (self.dir / "index.html").read_text(encoding="utf-8")
        css, js = self.get("app.css").version, self.get("app.js").version
        html = html.replace('href="static/app.css"', f'href="static/app.css?v={css}"')
        html = html.replace('s.src = "static/app.js"', f's.src = "static/app.js?v={js}"')
        html = html.replace('href="static/app.js"', f'href="static/app.js?v={js}"')  # preload — тот же адрес
        tg = self.get("telegram-web-app.js").version
        html = html.replace('src="static/telegram-web-app.js"', f'src="static/telegram-web-app.js?v={tg}"')
        if (self.dir / "splash.webp").exists():
            splash = self.get("splash.webp").version
            html = html.replace('"static/splash.webp"', f'"static/splash.webp?v={splash}"')   # preload и картинка
        return html.encode("utf-8")

    def url(self, name: str) -> str:
        """Адрес файла с версией — для картинок, на которые ссылается не index.html, а ответ API."""
        return f"static/{name}?v={self.get(name).version}"

    def response(self, request: web.Request, name: str) -> web.Response:
        asset = self.get(name)
        headers = {
            "ETag": asset.etag,
            "Vary": "Accept-Encoding",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": IMMUTABLE if request.query.get("v") == asset.version else "no-cache",
        }
        if request.headers.get("If-None-Match") == asset.etag:
            return web.Response(status=304, headers=headers)
        body = asset.body
        if accepts_gzip(request) and name not in _NO_GZIP:
            body = asset.gz
            headers["Content-Encoding"] = "gzip"
        return web.Response(body=body, content_type=asset.content_type, headers=headers)

"""
v2.176.0: webapp/static/app.js собирается из частей webapp/src/app/*.js
(scripts/build_app_js.py). Тест ловит, если часть поправили, а app.js не пересобрали.
Запуск: pytest tests/test_app_build.py
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _builder():
    spec = importlib.util.spec_from_file_location("build_app_js", ROOT / "scripts" / "build_app_js.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_app_js_is_built_from_parts():
    builder = _builder()
    assert (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8") == builder.build(), (
        "webapp/static/app.js устарел — запустите py -3.13 scripts/build_app_js.py"
    )


def test_parts_are_split_by_screen_and_wrap_one_function():
    parts = sorted((ROOT / "webapp" / "src" / "app").glob("*.js"))
    assert len(parts) >= 10
    first, last = parts[0].read_text(encoding="utf-8"), parts[-1].read_text(encoding="utf-8")
    assert "(function () {" in first and last.rstrip().endswith("})();")
    for p in parts[1:]:
        assert p.read_text(encoding="utf-8").strip(), p.name

"""
v2.212.0: сцена смены чемпиона в «Зале славы» — разметка и стили (вид проверяется в превью и
в браузерных тестах). Запуск: pytest tests/test_v2212_scene.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUB = (ROOT / "webapp" / "src" / "app" / "14-club.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "static" / "app.css").read_text(encoding="utf-8")


def test_scene_wiring():
    assert "function throneChange(seen, cur, timeline)" in CLUB
    assert 'cur.name + " занял трон. " + old.name + " это запомнит."' in CLUB       # подпись одобрена владельцем
    assert 'el("span", "", "БАМ!")' in CLUB
    assert "var scene = newKing && old !== null && !reduceMotion;" in CLUB
    # нажатие во время сцены пропускает её и не открывает профиль
    assert "e.stopPropagation();" in CLUB and 'card.classList.add("scene-done")' in CLUB
    # без сцены (нет старого чемпиона в летописи) — прежняя плашка
    assert '"Трон сменился: "' in CLUB


def test_scene_styles():
    for sel in (".is-scene .throne-sitter .throne-new", ".is-scene .throne-sitter .throne-old", ".throne-bang",
                ".throne-noir", ".scene-done .throne-old"):
        assert sel in CSS, sel
    for frames in ("new-charge", "old-launched", "crown-flip", "bang-pop", "scene-shake", "noir-in"):
        assert f"@keyframes {frames}" in CSS, frames
    assert "@media (prefers-reduced-motion: reduce) {\n  .throne-noir { animation: none; }" in CSS

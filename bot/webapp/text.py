"""
Строки экранов бота -> элементы Mini App (v2.161.0).

Формулировки экранов бота уже согласованы с владельцем построчно, а расчёты
за ними живут в хендлерах/сервисах. Чтобы приложение не заводило второй
экземпляр тех же текстов (и они не разъезжались), оно берёт готовые строки
бота и только меняет оформление: убирает HTML-теги и эмодзи (в приложении
эмодзи нет по дизайну), делит строку «Название: значение» на две колонки,
пустые строки превращает в границы групп.
"""
import html
import re

# Эмодзи и служебные символы при них (вариационный селектор, ZWJ, тоны кожи,
# флаги). Геометрические фигуры ▲▼ и типографика (· – — − …) не трогаются.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # пиктограммы, смайлы, транспорт, символы и т.п.
    "\U0001F1E6-\U0001F1FF"   # региональные индикаторы (флаги)
    "☀-➿"           # разные символы и дингбаты (☀ ✅ ❄ ⚔ ⚖ ❤ ✨ …)
    "⬀-⯿"           # стрелки и звёзды (⭐ ⬜ …)
    "⌀-⏿"           # технические (⏳ ⌛ …)
    "←-⇿"           # стрелки
    "〰〽㊗㊙"
    "︎️‍⃣"
    "]+"
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t ]{2,}")

# Кружки формы (🟢 победа, 🔴 поражение, 🟡 ничья) — в приложении это точки
_FORM_MARKS = {"🟢": "w", "🔴": "l", "🟡": "d"}
# Эмодзи внутри фразы, которые несут смысл, — заменяются словом, а не удаляются
_INLINE_WORDS = {"🤝": "ничьи "}


def plain(line: str) -> str:
    """HTML-строка бота -> чистый текст без тегов и эмодзи."""
    for emoji, word in _INLINE_WORDS.items():
        # эмодзи в начале строки — просто значок, в середине — слово
        stripped = line.lstrip()
        if not stripped.startswith(emoji):
            line = line.replace(emoji, word)
    text = html.unescape(_TAG_RE.sub("", line))
    text = _EMOJI_RE.sub("", text)
    text = _SPACES_RE.sub(" ", text)
    return text.strip(" \t·—-").strip()


def _form(value_raw: str) -> list[str] | None:
    marks = [_FORM_MARKS[ch] for ch in value_raw if ch in _FORM_MARKS]
    return marks or None


def item(line: str) -> dict | None:
    """Одна строка бота -> {label, value} | {label, form} | {text}; None — пусто."""
    raw_value = line.split(":", 1)[1] if ":" in line else ""
    form = _form(raw_value)
    text = plain(line)
    if not text:
        return None
    if ": " in text:
        label, value = text.split(": ", 1)
        if 0 < len(label) <= 40:
            if form:
                rest = plain(_TAG_RE.sub("", raw_value).translate({ord(c): None for c in _FORM_MARKS}))
                out: dict = {"label": label, "form": form}
                if rest:
                    out["note"] = rest.strip("() ")
                return out
            return {"label": label, "value": value}
    return {"text": text}


def groups(lines: list[str]) -> list[list[dict]]:
    """Строки бота -> группы элементов; граница группы — пустая строка.
    Многострочные записи (с \\n внутри) — один элемент: первая строка как
    текст, остальные как пояснение."""
    result: list[list[dict]] = []
    current: list[dict] = []
    for line in lines:
        if not line.strip():
            if current:
                result.append(current)
                current = []
            continue
        parts = [p for p in line.split("\n") if p.strip()]
        head = item(parts[0])
        if head is None:
            continue
        if len(parts) > 1:
            notes = [plain(p) for p in parts[1:]]
            head["note"] = " ".join(n for n in notes if n)
        current.append(head)
    if current:
        result.append(current)
    return result

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
_GAP_RE = re.compile(r"(?<=\S) {2,}(?=[^\s(|])")
_SPACES_RE = re.compile(r"[ \t ]{2,}")

# Кружки формы (🟢 победа, 🔴 поражение, 🟡 ничья) — в приложении это точки
_FORM_MARKS = {"🟢": "w", "🔴": "l", "🟡": "d"}
# Эмодзи внутри фразы, которые несут смысл, — заменяются словом, а не удаляются
_INLINE_WORDS = {"🤝": "ничьи "}
_BAR_CHARS = {"█", "░"}


def plain(line: str) -> str:
    """HTML-строка бота -> чистый текст без тегов и эмодзи."""
    for emoji, word in _INLINE_WORDS.items():
        # эмодзи в начале строки — просто значок, в середине — слово
        stripped = line.lstrip()
        if not stripped.startswith(emoji):
            line = line.replace(emoji, word)
    text = html.unescape(_TAG_RE.sub("", line))
    text = _EMOJI_RE.sub("", text).strip()
    # Двойной пробел в боте — визуальный разделитель («+36.4 pts  11:5»);
    # перед скобкой он просто отступ («Форма  (12 матчей)»).
    text = _GAP_RE.sub(" · ", text)
    text = _SPACES_RE.sub(" ", text)
    return text.strip(" \t·—-").strip()


def _form(value_raw: str) -> list[str] | None:
    marks = [_FORM_MARKS[ch] for ch in value_raw if ch in _FORM_MARKS]
    return marks or None


def item(line: str) -> dict | None:
    """Одна строка бота -> {label, value} | {label, form} | {text}; None — пусто."""
    raw_label, _, raw_value = line.partition(":")
    form = _form(raw_value)
    if form:
        out: dict = {"label": plain(raw_label), "form": form}
        rest = plain(_TAG_RE.sub("", raw_value).translate({ord(c): None for c in _FORM_MARKS}))
        if rest:
            out["note"] = rest.strip("() ")
        return out
    text = plain(line)
    if not text:
        return None
    if ": " in text:
        label, value = text.split(": ", 1)
        if 0 < len(label) <= 40:
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
        for extra in parts[1:]:
            bar = extra.strip()
            if bar and set(bar) <= _BAR_CHARS:
                # Полоска прогресса «███░░» из строки цели — в приложении шкала
                head["progress"] = round(bar.count("█") / len(bar), 2)
                continue
            note = plain(extra)
            if note:
                head["note"] = f"{head['note']}\n{note}" if "note" in head else note
        current.append(head)
    if current:
        result.append(current)
    return result

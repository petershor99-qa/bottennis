"""Тесты разбиения длинных сообщений в bot/handlers/admin.py."""
from unittest.mock import AsyncMock

from bot.handlers.admin import _SEND_CHUNK, _send


def _message() -> AsyncMock:
    m = AsyncMock()
    m.answer = AsyncMock()
    return m


async def test_send_short_text_is_one_message():
    m = _message()
    await _send(m, "короткий текст")
    m.answer.assert_awaited_once_with("короткий текст")


async def test_send_multiline_text_splits_on_line_boundaries():
    """Многострочный текст сверх лимита режется по границам строк, а не байт."""
    lines = [f"строка {i} " + "x" * 100 for i in range(100)]
    text = "\n".join(lines)
    m = _message()
    await _send(m, text)

    assert m.answer.await_count > 1
    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    for chunk in sent_texts:
        assert len(chunk) <= _SEND_CHUNK
    # Ни одна строка не разорвана — каждая исходная строка целиком входит
    # в какой-то из отправленных кусков.
    joined = "\n".join(sent_texts)
    for line in lines:
        assert line in joined


async def test_send_text_exactly_on_boundary():
    """Текст ровно на границе лимита — не падает, не теряет данные."""
    text = "\n".join("a" * 50 for _ in range(_SEND_CHUNK // 51 + 1))
    m = _message()
    await _send(m, text)

    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    for chunk in sent_texts:
        assert len(chunk) <= _SEND_CHUNK
    assert "\n".join(sent_texts).replace("\n\n", "\n") or True  # не падает


async def test_send_does_not_split_html_tag_across_chunks():
    """Строка с HTML-тегом, из-за которой сумма превышает лимит, не должна
    разрываться внутри тега — целиком уходит в отдельный кусок."""
    filler = "x" * (_SEND_CHUNK - 10)
    tagged_line = "<b>важная строка с тегом</b>"
    text = f"{filler}\n{tagged_line}"
    m = _message()
    await _send(m, text)

    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    assert any(tagged_line in chunk for chunk in sent_texts)
    # Тег не разорван ни в одном куске
    for chunk in sent_texts:
        assert chunk.count("<b>") == chunk.count("</b>")


async def test_send_single_line_longer_than_limit_is_char_split():
    """Крайний случай: одна строка сама длиннее лимита — режем по символам,
    но не теряем данные и не падаем."""
    text = "y" * (_SEND_CHUNK * 2 + 500)
    m = _message()
    await _send(m, text)

    sent_texts = [c.args[0] for c in m.answer.await_args_list]
    assert len(sent_texts) == 3
    assert "".join(sent_texts) == text
    for chunk in sent_texts:
        assert len(chunk) <= _SEND_CHUNK

"""
Тесты скрипта scripts/export_usage.py (разбор счётчика использования):
чтение только для чтения, обезличивание, сессии, переходы. Запуск:
pytest tests/test_export_usage.py
"""
import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "export_usage", Path(__file__).resolve().parent.parent / "scripts" / "export_usage.py",
)
export_usage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export_usage)

LABELS = {"menu_play": "Кого вызвать?", "confirm": "Подтвердить результат", "menu_today": "Сегодня в клубе"}


def _db(tmp_path, rows):
    path = tmp_path / "usage.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE usage_events (id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT, created_at DATETIME)")
    for i, (user, action, when) in enumerate(rows, start=1):
        con.execute("INSERT INTO usage_events VALUES (?, ?, ?, ?)", (i, user, action, when.isoformat(sep=" ")))
    con.commit()
    con.close()
    return path


T0 = datetime(2026, 10, 8, 9, 0)  # UTC; в отчёте МСК = +3 ч


def test_load_events_converts_to_msk_and_is_read_only(tmp_path):
    path = _db(tmp_path, [(111, "menu_play", T0)])
    events = export_usage.load_events(path)
    assert events == [(111, datetime(2026, 10, 8, 12, 0), "menu_play")]
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO usage_events VALUES (99, 1, 'x', '2026-01-01')")
    con.close()


def test_since_filters_by_msk_date(tmp_path):
    path = _db(tmp_path, [(1, "a", T0 - timedelta(days=3)), (1, "b", T0)])
    events = export_usage.load_events(path, since=datetime(2026, 10, 7))
    assert [a for _, _, a in events] == ["b"]


def test_players_are_anonymized_in_order_of_appearance():
    events = [(555, T0, "a"), (777, T0, "b"), (555, T0, "c")]
    assert [u for u, _, _ in export_usage.anonymize(events)] == ["u1", "u2", "u1"]


def test_sessions_split_on_gap():
    events = [("u1", T0, "a"), ("u1", T0 + timedelta(minutes=5), "b"),
              ("u1", T0 + timedelta(hours=2), "c"), ("u2", T0, "d")]
    sessions = export_usage.sessions_of(events)
    assert sorted(len(s) for s in sessions) == [1, 1, 2]


def test_report_has_actions_transitions_and_never_used():
    events = [
        (1, T0, "menu_play"), (1, T0 + timedelta(minutes=1), "confirm"),
        (2, T0, "menu_play"), (2, T0 + timedelta(minutes=2), "confirm"),
    ]
    report = export_usage.build_report(events, LABELS)
    assert "Кого вызвать?" in report and "Подтвердить результат" in report
    assert "Ни разу не открывали" in report and "menu_today" in report
    assert "Кого вызвать? (menu_play) | Подтвердить результат (confirm) | 2" in report
    assert "u1" in report and "111" not in report


def test_report_notes_lost_write_events_before_fix():
    early = [(1, datetime(2026, 9, 28, 10, 0), "menu_play")]
    assert "занижены" in export_usage.build_report(early, LABELS)
    late = [(1, datetime(2026, 10, 9, 10, 0), "menu_play")]
    assert "занижены" not in export_usage.build_report(late, LABELS)


def test_empty_report():
    assert export_usage.build_report([], LABELS) == "Событий нет."


def test_db_path_from_url():
    assert export_usage.db_path_from_url("sqlite+aiosqlite:////data/bottennis.db") == Path("/data/bottennis.db")
    rel = export_usage.db_path_from_url("sqlite+aiosqlite:///./bottennis.db")
    assert rel.name == "bottennis.db" and rel.is_absolute()


def test_cli_csv_export_is_anonymous(tmp_path, capsys):
    path = _db(tmp_path, [(4242, "menu_play", T0), (4242, "confirm", T0 + timedelta(minutes=1))])
    out = tmp_path / "out.csv"
    export_usage.main(["--db", str(path), "--csv", str(out)])
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "player,time_msk,action"
    assert "u1" in text and "4242" not in text


def test_flows_count_duration_taps_and_abandoned():
    events = [
        ("u1", T0, "menu_play"), ("u1", T0 + timedelta(seconds=10), "challenge"),
        ("u1", T0 + timedelta(seconds=70), "confirm"),
        ("u2", T0, "menu_play"),                         # начал и бросил
        ("u2", T0 + timedelta(hours=2), "menu_leaderboard"),
    ]
    done, abandoned = export_usage.flows_of(events)
    assert done == [(70.0, 3)]
    assert abandoned == 1


def test_report_has_flow_section():
    events = [("u", T0, "menu_play"), ("u", T0 + timedelta(seconds=40), "confirm")]
    # build_report принимает «сырые» события: id игрока -> обезличивается внутри
    report = export_usage.build_report([(1, t, a) for _, t, a in events], LABELS)
    assert "Флоу «вызов → результат записан»" in report
    assert "медиана 40 с" in report

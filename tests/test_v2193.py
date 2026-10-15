"""
v2.193.0: сравнение любой пары игроков — «A против B» глазами третьего
(`/api/pair/{a}/{b}`, `#/pair/A/B`): из профиля («Сравнить с другим игроком»),
из «Кто с кем» и из «Личных встреч» на экране матча.
Запуск: pytest tests/test_v2193.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.db.models import Match, MatchStatus
from bot.webapp import api, server
from tests.conftest import _player

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webapp" / "src" / "app"


async def _club(db):
    a, b, c = _player(1, "Алиса", 1050.0), _player(2, "Боб", 1010.0), _player(3, "Вика", 990.0)
    db.add_all([a, b, c])
    await db.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i, w in enumerate((b, b, c)):                    # Боб ведёт 2–1, последние две — его
        db.add(Match(challenger_id=b.id, challenged_id=c.id, status=MatchStatus.completed, winner_id=w.id,
                     sets_data=[{"w": 11, "l": 6}], rating_change=8.0,
                     completed_at=now - timedelta(days=10 - i * 3 if w is c else 3 - i)))
    await db.commit()
    return a, b, c


class _Req:
    def __init__(self, a, b):
        self.match_info = {"a": str(a), "b": str(b)}


async def test_pair_seen_by_third_player(db):
    a, b, c = await _club(db)
    d = await server.pair(_Req(b.id, c.id), db, a)
    assert d["pair"] is True and d["title"] == "Боб против Вика"
    assert d["me"]["name"] == "Боб" and d["them"]["name"] == "Вика"
    assert d["score"] == {"me": 2, "them": 1, "draws": 0}
    values = " ".join(m["value"] for m in d["metrics"])
    assert "ты" not in values.lower().split() and "Боб ведёт" in values


async def test_pair_from_participant_is_regular_h2h(db):
    a, b, c = await _club(db)
    d = await server.pair(_Req(b.id, c.id), db, c)       # смотрит сама Вика — «Ты против Боб»
    assert d["pair"] is False and d["title"] == "Ты против Боб" and d["me"]["name"] == "Вика"


async def test_pair_bad_requests(db):
    a, b, _ = await _club(db)
    assert await server.pair(_Req(b.id, b.id), db, a) is None
    assert await server.pair(_Req(b.id, 999), db, a) is None
    assert await server.pair(_Req("x", b.id), db, a) is None


async def test_pair_never_met(db):
    a, b, c = await _club(db)
    d = await api.h2h_payload(db, a, b, third=True)
    assert d["empty"] == "Они ещё не встречались за столом." and d["matches"] == []


def test_route_and_entry_points():
    assert '("/api/pair/{a}/{b}", pair)' in (ROOT / "bot" / "webapp" / "server.py").read_text(encoding="utf-8")
    routes = (SRC / "90-routes.js").read_text(encoding="utf-8")
    assert 'api("pair/" + m[1] + "/" + m[2])' in routes
    versus = (SRC / "13-matches.js").read_text(encoding="utf-8")
    assert "if (!d.pair) c.appendChild(challengeButton(d.them.id));" in versus     # вызов — только за себя
    assert "duelCard(d.duel, d.them.name, d.pair ? d.me.name : null)" in versus
    profile = (SRC / "12-profile.js").read_text(encoding="utf-8")
    assert "function comparePicker(playerId, name)" in profile and '"Сравнить с другим игроком"' in profile
    assert '"pair/" + rowP.id + "/" + colP.id' in (SRC / "25-new.js").read_text(encoding="utf-8")
    assert '"pair/" + d.a.id + "/" + d.b.id' in (SRC / "20-match.js").read_text(encoding="utf-8")

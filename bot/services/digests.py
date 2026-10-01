"""Тихий режим дайджестов (v2.145.0) — игрок отключает отдельные автосводки.

Хранится одним полем `Player.muted_digests` — ключи отключённых сводок через
запятую («day,month»). Отключить можно ТОЛЬКО периодические сводки; личные
уведомления (вызовы, результаты матчей, трон), голосование за звания года и
админская рассылка «Что нового» отключению не подлежат — это не «шум», а
функциональные сообщения.
"""
from bot.db.models import Player

# (ключ, подпись на кнопке) в порядке показа на экране «Рассылки»
DIGEST_KINDS: list[tuple[str, str]] = [
    ("day", "📅 Итоги дня"),
    ("week", "📆 Итоги недели"),
    ("month", "🗓 Итоги месяца"),
    ("quarter", "📊 Итоги квартала"),
    ("year", "🎆 Итоги года"),
]

DIGEST_KEYS = {key for key, _ in DIGEST_KINDS}


def muted_digests(player: Player) -> set[str]:
    """Множество ключей отключённых сводок. Неизвестные ключи (например, если
    список сводок когда-то сократят) молча отбрасываются."""
    raw = player.muted_digests or ""
    return {k for k in raw.split(",") if k in DIGEST_KEYS}


def is_digest_muted(player: Player, kind: str) -> bool:
    return kind in muted_digests(player)


def toggle_digest(player: Player, kind: str) -> bool | None:
    """Переключает сводку `kind`. Возвращает новое состояние «отключена» (True —
    теперь отключена) или None для неизвестного ключа. Коммит — на вызывающем."""
    if kind not in DIGEST_KEYS:
        return None
    muted = muted_digests(player)
    now_muted = kind not in muted
    if now_muted:
        muted.add(kind)
    else:
        muted.discard(kind)
    # порядок сохраняем стабильным — как в DIGEST_KINDS
    player.muted_digests = ",".join(k for k, _ in DIGEST_KINDS if k in muted)
    return now_muted

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class MatchStatus(enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    completed = "completed"


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String, nullable=True)
    display_name = Column(String, nullable=False)
    rating = Column(Float, default=1000.0, nullable=False)
    peak_rating = Column(Float, nullable=True)   # максимальный рейтинг за всё время
    achievements = Column(String, default="[]", nullable=True)  # JSON-список id заработанных ачивок
    backfill_version = Column(Integer, default=0, nullable=True)  # версия последнего бэкфилла
    is_champion = Column(Boolean, default=False, nullable=False)  # владелец 1-го места (босс-файт)
    last_menu_message_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    challenges_sent = relationship(
        "Match", foreign_keys="Match.challenger_id", back_populates="challenger"
    )
    challenges_received = relationship(
        "Match", foreign_keys="Match.challenged_id", back_populates="challenged"
    )


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    challenger_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    challenged_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    status = Column(Enum(MatchStatus), default=MatchStatus.pending, nullable=False)
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    # [{"w": 11, "l": 7}, ...] — winner's score : loser's score per set
    sets_data = Column(JSON, nullable=True)
    rating_change = Column(Float, nullable=True)
    reminder_sent = Column(Boolean, default=False, nullable=False)
    is_boss_fight = Column(Boolean, default=False, nullable=False)  # ×2 к дельте, ничья запрещена
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    accepted_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    challenger = relationship(
        "Player", foreign_keys=[challenger_id], back_populates="challenges_sent"
    )
    challenged = relationship(
        "Player", foreign_keys=[challenged_id], back_populates="challenges_received"
    )
    winner = relationship("Player", foreign_keys=[winner_id])


class ChampionReign(Base):
    """Один период владения местом #1. ended_at=None — текущее правление.

    Существование хотя бы одной строки здесь также служит признаком «фича
    боссфайта когда-либо была инициализирована» — отдельно от Player.is_champion,
    который админ может обнулить руками (см. bootstrap_champion в utils.py):
    is_champion можно сбросить, а факт «уже было» — не должен теряться при
    следующем перезапуске, иначе рубильник не переживает деплой.
    """
    __tablename__ = "champion_reigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)


class AchievementEarned(Base):
    """Дата получения ачивки (v2.106.0 — раньше нигде не хранилась, только
    сам факт в Player.achievements). Одна строка на (player_id,
    achievement_id) — ачивки не переоткрываются, в отличие от личных рекордов.

    earned_at=None — дата принципиально неизвестна: либо ачивка входит в
    список из 7 полностью исключённых из бэкфилла (нужен снапшот рейтинга/роли
    на момент КОНКРЕТНОГО исторического матча — highlander, david_goliath,
    revenge, throne_denied, chance_blown, rock_bottom, rating_1200), либо
    получена до появления этой таблицы, а восстановить точку в истории не
    удалось. Новые ачивки (и в реальном времени, и при повторном бэкфилле
    существующих) всегда получают точную дату — см. backfill_achievements().
    """
    __tablename__ = "achievements_earned"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    achievement_id = Column(String, nullable=False)
    earned_at = Column(DateTime, nullable=True)


class PersonalRecordEarned(Base):
    """История личных рекордов (v2.106.0). В отличие от ачивок метрику можно
    бить многократно за карьеру — поэтому не одна строка на (player_id,
    metric), а полная история: каждое улучшение — новая строка.

    match_id — матч, на котором рекорд был установлен (может быть NULL для
    записей, где восстановить конкретный матч не удалось). earned_at=None —
    та же семантика неизвестной даты, что у AchievementEarned.
    """
    __tablename__ = "personal_records_earned"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    metric = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)
    earned_at = Column(DateTime, nullable=True)

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
    challenge_reminder_sent = Column(Boolean, default=False, nullable=False)
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

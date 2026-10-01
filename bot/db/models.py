import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MatchStatus(enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    completed = "completed"


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(unique=True)
    username: Mapped[str | None]
    display_name: Mapped[str]
    rating: Mapped[float] = mapped_column(default=1000.0)
    peak_rating: Mapped[float | None]  # максимальный рейтинг за всё время
    achievements: Mapped[str | None] = mapped_column(default="[]")  # JSON-список id заработанных ачивок
    backfill_version: Mapped[int | None] = mapped_column(default=0)  # версия последнего бэкфилла
    is_champion: Mapped[bool] = mapped_column(default=False)  # владелец 1-го места (босс-файт)
    last_menu_message_id: Mapped[int | None]
    # Ключи отключённых игроком автосводок через запятую («day,month»), см.
    # bot/services/digests.py. Пусто — получает все (v2.145.0).
    muted_digests: Mapped[str] = mapped_column(default="", server_default="")
    created_at: Mapped[datetime | None] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    challenges_sent = relationship(
        "Match", foreign_keys="Match.challenger_id", back_populates="challenger"
    )
    challenges_received = relationship(
        "Match", foreign_keys="Match.challenged_id", back_populates="challenged"
    )


class Match(Base):
    __tablename__ = "matches"
    # Индексы (v2.143.0): почти каждый экран и дайджест фильтрует матчи по
    # участнику и/или статусу + дате завершения. Для уже существующей БД те же
    # индексы создаёт _migrate_db() (create_all индексы старых таблиц не добавляет).
    __table_args__ = (
        Index("ix_matches_challenger_id", "challenger_id"),
        Index("ix_matches_challenged_id", "challenged_id"),
        Index("ix_matches_status_completed_at", "status", "completed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    challenged_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus), default=MatchStatus.pending)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    # [{"w": 11, "l": 7}, ...] — winner's score : loser's score per set
    sets_data: Mapped[list[dict] | None] = mapped_column(JSON)
    rating_change: Mapped[float | None]
    reminder_sent: Mapped[bool] = mapped_column(default=False)
    is_boss_fight: Mapped[bool] = mapped_column(default=False)  # ×2 к дельте, ничья запрещена
    created_at: Mapped[datetime | None] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    accepted_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]


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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    achievement_id: Mapped[str]
    earned_at: Mapped[datetime | None]


class UsageEvent(Base):
    """Пассивный счётчик открытий экранов (v2.132.0, этап 1 дорожной карты
    в CLAUDE.md) — какие экраны реально открывают, чтобы позже (этап 4)
    можно было убрать неиспользуемое. Пишется `UsageMiddleware` молча,
    игроки ничего не видят и не замечают. Без FK на players — незарегистри-
    рованный тоже может нажать кнопку. Питает команду /usage."""
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(index=True)  # normalize_action() — bot/services/usage.py
    created_at: Mapped[datetime] = mapped_column(
        index=True,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )


class YearVote(Base):
    """Голос в номинации ежегодного голосования «Итоги года: неформальные
    звания» (v2.134.0, этап 3 дорожной карты). Одна строка на
    (year, nomination, voter_id) — уникальный индекс гарантирует, что смена
    голоса до закрытия перезаписывает существующую строку (UPDATE), а не
    создаёт вторую. nomination — id из YEAR_VOTE_NOMINATIONS
    (bot/services/year_vote.py)."""
    __tablename__ = "year_votes"
    __table_args__ = (
        UniqueConstraint("year", "nomination", "voter_id", name="uq_year_vote_voter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    year: Mapped[int]
    nomination: Mapped[str]
    voter_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    nominee_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    updated_at: Mapped[datetime | None] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )


class PersonalRecordEarned(Base):
    """История личных рекордов (v2.106.0). В отличие от ачивок метрику можно
    бить многократно за карьеру — поэтому не одна строка на (player_id,
    metric), а полная история: каждое улучшение — новая строка.

    match_id — матч, на котором рекорд был установлен (может быть NULL для
    записей, где восстановить конкретный матч не удалось). earned_at=None —
    та же семантика неизвестной даты, что у AchievementEarned.
    """
    __tablename__ = "personal_records_earned"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    metric: Mapped[str]
    value: Mapped[float]
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    earned_at: Mapped[datetime | None]

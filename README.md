# 🏓 bottennis

![tests](https://github.com/petershor99-qa/bottennis/actions/workflows/tests.yml/badge.svg)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

> Telegram-бот для рейтинговых игр в настольный теннис внутри команды.
> Реальный проект в проде: регистрация, вызовы, рейтинг ELO, статистика, ачивки, авто-сводки.
>
> *A production Telegram bot for office table-tennis rankings — ELO ratings, stats, achievements, daily digests. Built and tested end-to-end.*

---

## 🎯 Об этом проекте

Это не учебный todo-лист, а **работающий бот в проде** для закрытой группы коллег, играющих в настольный теннис в офисе. Я спроектировал, реализовал и **покрыл автотестами** весь цикл: от регистрации игрока до пересчёта рейтинга и вечерних сводок.

Проект сделан с уклоном в **качество и тестируемость** — это моя основная специализация (QA).

## ✅ Тестирование (главное)

```bash
pytest -v          # 97 автотестов, < 2 секунд
```

- **97 автотестов** на `pytest` + `pytest-asyncio`
- **CI на GitHub Actions** — тесты прогоняются на каждый push и PR (бейдж выше)
- Чистое разделение: бизнес-логика вынесена в чистые функции и покрыта unit-тестами без БД и сети

**Что покрыто:**

| Модуль | Файл тестов | Что проверяется |
|---|---|---|
| Рейтинг ELO | `tests/test_rating.py` | дельта, апсеты, разгром vs упорный матч, штраф за 1 партию, симметрия вероятности победы |
| Валидация счёта | `tests/test_validation.py` | правила настольного тенниса: ≥11 с отрывом ≥2, дьюс, некорректные счета |
| Достижения | `tests/test_achievements.py` | все 21 ачивка, идемпотентность, бэкфилл по истории (in-memory SQLite) |
| «Матч дня» | `tests/test_match_of_day.py` | индекс драмы, выбор матча, перспектива счёта |
| Личные встречи | `tests/test_h2h.py` | H2H-счёт, подсчёт партий с перспективы игрока, серии |

**Примеры пойманных и исправленных багов** (см. `RELEASE_NOTES.md`):
- Краш при вводе счёта, если у игрока 2+ активных матча (`MultipleResultsFound`)
- Некорректная дельта при срабатывании «пола» рейтинга
- Неверная перспектива счёта при инверсии победителя
- Сброс ввода при наборе счёта на экране подтверждения

## 🤖 Что умеет бот

- Регистрация по инвайт-ссылке (`/start <CODE>`)
- Вызов соперника — матч становится активным сразу
- Внесение результата пошаговым FSM или **прямым вводом счёта** в чат (`11:7 9:11 11:5`)
- Автоматический пересчёт рейтинга (ELO с бонусами за доминирование)
- Таблица рейтинга с ▲▼ изменением позиции за неделю, винрейтом, сериями
- Личная статистика, профиль игрока, **личные встречи (H2H)**
- Система достижений (21 ачивка)
- ⏰ Напоминание о незавершённых матчах
- 📅 Итоги дня в 20:00 МСК + «матч дня»
- 📊 Еженедельный дайджест

## ⭐ Рейтинговая система

Модифицированный **ELO** с бонусами за доминирование в партиях:

```
E          = win_probability(winner, loser) = 1 / (1 + 10 ^ ((loser - winner) / 400))
base_delta = 32 * (1 - E)

score_mult       = 1 + 0.5*(sets_ratio - 0.5) + 0.3*(pts_ratio - 0.5)
short_match_mult = 0.75 если матч из 1 партии
newcomer_bonus   = 1.2 если у победителя < 15 матчей
repeat_mult      = max(0.5, 1.0 - 0.05 × streak)   # штраф за серию побед над тем же соперником

delta = base_delta × score_mult × short_match_mult × newcomer_bonus × repeat_mult
```

Подробный анализ формулы на реальных данных — в [`RATING_ANALYSIS.md`](RATING_ANALYSIS.md).

## 🧱 Стек

| Слой | Технология |
|---|---|
| Язык | Python 3.11+ |
| Telegram | aiogram 3.x (long-polling) |
| ORM | SQLAlchemy 2.x (async) |
| БД | SQLite + aiosqlite |
| Фоновые задачи | APScheduler 3.x |
| Тесты | pytest, pytest-asyncio |
| CI | GitHub Actions |
| Хостинг | Railway |

## 📁 Структура

```
bottennis/
├── main.py                     # точка входа: Bot, Dispatcher, роутеры, планировщик
├── bot/
│   ├── handlers/
│   │   ├── start.py            # /start, /help, навигация
│   │   ├── leaderboard.py      # таблица рейтинга, «сегодня»
│   │   ├── profile.py          # статистика, профиль, достижения
│   │   ├── history.py          # история матчей, личные встречи (H2H)
│   │   ├── challenge.py        # вызов, отмена матча
│   │   ├── match_result.py     # FSM ввода результата
│   │   └── admin.py            # админ-команды (/dbstats)
│   ├── services/
│   │   ├── rating.py           # формула ELO
│   │   ├── validation.py       # валидация счёта
│   │   └── achievements.py     # система достижений
│   ├── keyboards/inline.py     # все inline-клавиатуры
│   ├── db/                     # models.py, database.py
│   ├── scheduler.py            # напоминания, итоги дня, дайджест
│   ├── middleware.py           # сессия БД на каждый апдейт
│   ├── states/states.py        # FSM-состояния
│   └── utils.py                # хелперы + чистая логика (H2H, «матч дня»)
└── tests/                      # 97 автотестов
```

## 🚀 Запуск у себя

Бот легко развернуть со своим токеном и своей базой:

```bash
git clone https://github.com/petershor99-qa/bottennis.git
cd bottennis

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # впиши свой BOT_TOKEN от @BotFather
python main.py
```

Деплой на Railway — через `Dockerfile` и `railway.toml` (БД хранится в Railway Volume).

### Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен от @BotFather |
| `INVITE_CODE` | код для пригласительной ссылки (пусто → регистрация открыта) |
| `ADMIN_ID` | Telegram ID администратора (для `/dbstats`, `/fix_rating`) |
| `DATABASE_URL` | напр. `sqlite+aiosqlite:////data/bottennis.db` |

## 📜 История изменений

Подробный changelog — в [`RELEASE_NOTES.md`](RELEASE_NOTES.md).

## 📄 Лицензия

[MIT](LICENSE) © Peter Shor

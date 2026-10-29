---
name: checkup
description: >
  Quarterly "техосмотр" of the bottennis club: dependency check (outdated
  packages, GitHub security alerts) plus a review of formulas and thresholds on
  real production data — style radar (card colors, archetypes), rank titles and
  rating inflation, rating deltas, weekly goals completion, achievements nobody
  or everybody has, club record pings, Mini App boot times. Use when the user
  says "техосмотр", "квартальная проверка", "пересчитай радар", "проверь
  зависимости", forwards the bot's reminder «Техосмотр клуба», or pastes the
  output of scripts/quarterly_report.py.
---

# Техосмотр клуба (раз в квартал)

Расписание: **15 января, 15 апреля, 15 июля, 15 октября** — бот сам напоминает
администратору (`send_checkup_reminder`, scheduler.py). Первый техосмотр —
вместе с этапом 16 дорожной карты (середина ноября 2026). Заморозка деплоя
15–31.12 действует: в эти даты только анализ, без релизов.

## 1. Зависимости (сам, сервер не нужен)

```bash
git pull --ff-only
py -3.13 -m pip list --outdated
gh api repos/petershor99-qa/bottennis/dependabot/alerts --jq '.[] | select(.state=="open") | [.security_advisory.severity, .dependency.package.name, .security_advisory.summary] | @tsv'
```

Смотреть только прямые зависимости из `requirements.txt` / `requirements-dev.txt`
/ `requirements-e2e.txt`. `aiohttp` закреплён в пределах `aiogram` — обновлять
вместе. Обновление — отдельным релизом и только после «да» владельца; открытый
security-алерт — предложить сразу, не ждать квартала.

## 2. Данные с прода (запускает владелец)

Свои ключи сервер не принимает — запускает владелец. Дать пошагово, по-русски
(IP не писать в файлы репозитория, взять у владельца):

1. PowerShell → `ssh root@<IP сервера>` → `yes` при первом входе → пароль.
2. `cd /opt/bottennis && .venv/bin/python scripts/quarterly_report.py`
3. Скопировать весь вывод и прислать, выйти — `exit`.

Если ssh «висит» — выключить VPN или зайти через «Консоль» в панели FirstVDS.
Вывод обезличен (u1, u2… по числу матчей), база только читается.

## 3. Разбор — на что смотреть

| Раздел | Нормально | Если нет — что править |
|---|---|---|
| 1. Радар | Каждый показатель различает игроков; цветных клеток ≥ трети; архетипов ≥4 видов | `_ARCHETYPE_RULES` (stats.py), `style_tiers.CORRIDOR`; обновить `tests/test_radar_calibration.py` новыми обезличенными данными |
| 2. Звания | «Ген дир» — только лидер; ни в одном звании больше половины клуба | `RANK_TITLE_BANDS` (utils.py) |
| 2. Инфляция, пол | Растёт медленно; на полу 900/1000 — новички | Обсудить с владельцем (формула рейтинга — `RATING_HELP_TEXT` тоже менять) |
| 3. Начисления | Средняя дельта ~8–12, без резких скачков по месяцам | Обсудить формулу ELO |
| 4. Цели недели | Каждая цель закрывается в 30–70% случаев | `MATCH_GOAL_*`, цели `SKILLS`, `FOCUS_ANCHORS` (api_return.py) |
| 5. Ачивки | «Не получил никто» — только редкие/скрытые | Проверить, достижима ли ачивка вообще (баг условия?) |
| 6. Пинги рекордов | 0–6 событий за квартал | Пороги `club_records.py` |
| 7. Запуск | Медиана < 3 с, от 5 с — меньше 10% | Скилл `prod-check` (сеть, сервер) |

Также сверить с прошлым техосмотром (таблица «Техосмотры» в CLAUDE.md).

## 4. Предложение и выпуск

- Владельцу — коротко: что в норме, что нет, таблица «было → станет» по
  игрокам (обезличенно), тексты для игроков дословно. Без «да» — не править.
- После «да» — обычный выпуск (скилл `release`), тесты на новых данных.
- В конце — строка в таблице «Техосмотры» в CLAUDE.md: дата, что смотрели,
  что поменяли (или «без изменений»).

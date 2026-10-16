---
name: prod-check
description: >
  Read-only health check of the bottennis production server in one command:
  server (uptime, memory, disk), bot and Caddy services, deployed commit vs
  origin/main, bot journal errors, database, nightly backups, Mini App boot
  times from the journal (median, slow ≥5 s, failed launches with network
  timing), the app page from here (by IP and by the old sslip.io name), HTTPS
  certificate expiry (the IP certificate lives 6 days) and port 443 from
  several countries (check-host.net). Use when the user says "проверь прод", "всё ли живо",
  "бот не отвечает", "приложение не грузится / зависает", "что с сервером",
  after a deploy, or before blaming the network.
---

# Проверка прода

Только чтение: скрипт ничего не перезапускает и не меняет на сервере.

## 1. Запуск

IP прод-сервера — **из памяти ассистента** (заметка `reference-prod-server`),
никогда не из файлов репозитория: репозиторий публичный, IP в него не пишется.
Старый сервер `188.120.228.39` выведен из эксплуатации — по нему не проверять.

```bash
git fetch -q origin
py -3.13 .claude/skills/prod-check/scripts/check_prod.py <IP из памяти>
```

Флаги: `--hours 72` — окно журнала (по умолчанию 24 ч), `--no-outside` — без
проверки из других стран. Код выхода: 0 — всё OK, 1 — есть WARN, 2 — FAIL.

## 2. Что смотреть в выводе

- **FAIL у SSH, но приложение открывается** — сервер жив, проблема с доступом
  по SSH с этой машины (VPN/прокси пользователя). Не делать выводов «сервер
  лёг», пока «Снаружи (check-host)» не покажет недоступность порта 443.
- **Снаружи: 443 недоступен из всех стран** — не работает Caddy или сервер.
  Это повод попросить пользователя перезагрузить VPS в панели FirstVDS
  (пошагово, по-русски), а не раньше.
- **Сервис не `active` или растёт число перезапусков** — смотреть журнал:
  `ssh root@<IP> "journalctl -u bottennis -n 80 --no-pager"`.
- **Деплой: сервер отстаёт от origin/main** — проверить job `deploy` последнего
  мержа в main (`gh run list --branch main --limit 3`).
- **Бэкапы: меньше 3 шт.** — ночной бэкап (`/opt/bottennis/backup.sh`, cron
  03:00) не копит копии. До 16.10.2026 скрипт перезаписывал один файл с датой,
  вписанной буквально.
- **Сертификат по IP: осталось меньше 1,5 дня** — с v2.200.1 приложение
  открывается по IP (`https://<IP>/`), сертификат Let's Encrypt на IP живёт
  6 дней, Caddy (не ниже 2.10, профиль `shortlived`) продлевает его сам. Если
  срок подходит к концу — продление не идёт: попросить пользователя прислать
  `journalctl -u caddy -n 60 --no-pager`. Временный откат — `WEBAPP_URL` на
  адрес sslip.io (`docs/MINIAPP.md`, «Вход по IP»).
- **Медленные загрузки приложения** — в строке журнала с v2.194.0 есть замер
  сети: «адрес» (DNS; с переходом на IP должен быть 0), «соединение», «первый байт», когда пришли
  стили, скрипт Telegram и `app.js`, и «не пришли: …». По нему видно, где
  встало: DNS, сеть до сервера или конкретный файл. Сервер отвечает за доли
  секунды — долгие «соединение»/«первый байт» означают сеть телефона.
- **Не запустилось** — этап `page_pending` (страница не дошла до запуска
  `app.js`), `app_js_pending` (не пришёл `app.js`), `app_js_failed`,
  `no_first_screen`.

## 3. Отчёт пользователю

По-русски, коротко: «всё ок» или что не так и что предлагаешь. Пользователь не
разработчик — без внутренних названий, где можно обойтись. Изменения на сервере
(правка скриптов, перезапуск сервисов) — только с его явного согласия.

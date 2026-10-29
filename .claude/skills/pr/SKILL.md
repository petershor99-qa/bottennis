---
name: pr
description: >
  Проводит правки через защищённый develop репозитория bottennis: ветка →
  коммит → push → PR через GitHub API → зелёный CI → merge → удаление ветки →
  git pull. Прямой push в develop запрещён правилами GitHub (GH013). Используй,
  когда нужно закоммитить и залить изменения (включая AGENTS.md, CLAUDE.md,
  .zcode/) или пользователь просит «заливай», «сделай PR», «отправь на GitHub».
  Для полного релиза (develop→main→деплой на VPS) — скилл release.
---

Ты проводишь изменения в репозитории bottennis (GitHub: petershor99-qa/bottennis)
через обязательный PR-конвейер. gh CLI НЕ установлен — всё через git + REST API.

## Правила

1. Все git-команды — с явным путём: `git -C "D:/claude/bottennis" ...`
   (сессия обычно открыта в папке G:, кода там нет — только конфиги).
2. Прямой push в `develop` и `main` запрещён правилами репозитория
   (remote отклоняет с GH013). Только ветка → PR → merge. Имена веток:
   `chore/...`, `docs/...`, `feature/...`.
3. Токен для API берётся из хранилища Windows и НИКОГДА не печатается:

   ```bash
   t=$(printf "protocol=https\nhost=github.com\n\n" | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null | sed -n 's/^password=//p')
   ```

4. **Кириллица в аргументе curl под Git Bash ломает JSON** (GitHub отвечает
   «Problems parsing JSON»): JSON-payload для создания PR писать ФАЙЛОМ в UTF-8
   (например `C:\Users\User\AppData\Local\Temp\pr_payload.json`) и отправлять
   `curl --data-binary @файл -H "Content-Type: application/json; charset=utf-8"`.

## Шаги

1. Ветка и коммит:

   ```bash
   git -C "D:/claude/bottennis" checkout -b <тип>/имя
   git -C "D:/claude/bottennis" add <файлы>
   git -C "D:/claude/bottennis" commit -m "<тип>: сообщение по-русски, без эмодзи"
   git -C "D:/claude/bottennis" push origin <тип>/имя
   ```

2. Создать PR: `POST /repos/petershor99-qa/bottennis/pulls`, head=ветка,
   base=`develop`, title/body из payload-файла.
3. Дождаться CI (workflow `tests`, ~2 мин: lint + pytest; e2e и mypy —
   continue-on-error, не блокируют). Опрашивать раз в 30 с:
   `GET /repos/petershor99-qa/bottennis/actions/runs?branch=<ветка>` до
   `completed:success`. Красный — чинить и пушить снова, не обходить
   (без `--no-verify`, `--force`, отключения проверок).
4. Смержить: `PUT /repos/petershor99-qa/bottennis/pulls/<N>/merge`
   (`{"merge_method":"merge"}`). Удалить ветку:
   `git push origin --delete <ветка>`.
5. Синхронизировать локально:
   `git -C "D:/claude/bottennis" checkout develop && git -C "D:/claude/bottennis" pull --ff-only`.
6. Если в PR менялись `AGENTS.md`, `CLAUDE.md` или `.zcode/` — скопировать
   обновлённое в папку сессий G: и сверить (скилл `sync-config`).

## Не делать

- Не печатать токен; не добавлять хвосты `Co-Authored-By` (решение владельца —
  Claude не должен расти в контрибуторах).
- Не мержить в `main` — это релиз (скилл `release`; заморозка деплоя 15–31.12).
- Не отключать проверки, не форсить историю (решение 2026-10-30: историю не
  переписываем).

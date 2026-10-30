---
name: sync-config
description: >
  Синхронизирует конфиги ассистента из репозитория D:\claude\bottennis в
  облачную папку сессий G:\Мой диск\Claude\work\bottennis: .zcode/ и AGENTS.md,
  с побайтовой сверкой diff. Используй после мержа правок .zcode/, AGENTS.md,
  CLAUDE.md или по просьбам «синхронизируй конфиги», «обнови G:».
---

Конфиги ассистента живут в репозитории и приезжают с `git pull` в D:-клон;
сессии ZCode читают их из папки G:, поэтому после изменения конфигов в репо их
нужно скопировать на G: (Google Drive сам разнесёт их на второй компьютер).

1. Скопировать и сверить:

   ```bash
   cp -rf "D:/claude/bottennis/.zcode/." "G:/Мой диск/Claude/work/bottennis/.zcode/"
   chmod +x "G:/Мой диск/Claude/work/bottennis/.zcode/hooks/"*.sh
   cp -f "D:/claude/bottennis/AGENTS.md" "G:/Мой диск/Claude/work/bottennis/AGENTS.md"
   diff -r "D:/claude/bottennis/.zcode" "G:/Мой диск/Claude/work/bottennis/.zcode" && \
     diff -q "D:/claude/bottennis/AGENTS.md" "G:/Мой диск/Claude/work/bottennis/AGENTS.md" && echo SYNC_OK
   ```

2. `diff` обязан быть пустым (SYNC_OK). Если ругается на лишние файлы на G:
   (удалённые в репо) — удалить их там же вручную и повторить.
3. CLAUDE.md на G: не нужен (ZCode читает AGENTS.md); если копируешь — он
   обязан быть идентичен AGENTS.md.
4. **Код на G: не копировать** — его там намеренно нет, код живёт только в
   D:-клоне (решение владельца 2026-10-29).
5. Новые скиллы/правила подхватятся в НОВЫХ сессиях ZCode — текущую можно
   не перезапускать.

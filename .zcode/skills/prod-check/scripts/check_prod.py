"""
Проверка прода bottennis одной командой: сервер, бот, Caddy, приложение снаружи,
сертификаты HTTPS, журнал загрузок Mini App. Только чтение — ничего не
перезапускает и не меняет.

    py -3.13 .claude/skills/prod-check/scripts/check_prod.py <IP>
    py -3.13 .claude/skills/prod-check/scripts/check_prod.py <IP> --hours 72 --no-outside

IP можно задать и переменной BOTTENNIS_PROD_HOST. В репозиторий IP не пишется
(репозиторий публичный) — он в памяти ассистента и в секретах GitHub Actions.

Код выхода: 0 — всё в порядке, 1 — есть предупреждения (WARN), 2 — что-то не
работает (FAIL) или не удалось проверить.
"""
import argparse
import json
import os
import socket
import ssl
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime

# Порог «медленного» запуска приложения — тот же порядок, что в «Использовании»
SLOW_BOOT_MS = 5000
SSH_TIMEOUT_S = 25
# Сертификаты (v2.200.1): приложение открывается по IP — сертификат Let's Encrypt
# на IP живёт 6 дней, Caddy продлевает его сам примерно за 2–3 дня до конца.
# Меньше полутора дней — продление не идёт. Старый адрес sslip.io (кнопки в
# старых сообщениях чата) — обычный сертификат на 90 дней, продление за 30.
CERT_IP_MIN_DAYS = 1.5
CERT_NAME_MIN_DAYS = 14
# check-host.net отвечает 403 на стандартный User-Agent Python
UA = {"User-Agent": "bottennis-prod-check/1.0", "Accept": "application/json"}

results: list[tuple[str, str, str]] = []   # (уровень, раздел, текст)


def add(level: str, section: str, text: str) -> None:
    results.append((level, section, text))


# ── Удалённая часть: один ssh, на сервере — bash + python из venv бота ──────────
REMOTE = r"""
set +e
echo "@@uptime"; uptime -p; uptime | sed 's/.*load average: //'
echo "@@boot"; uptime -s
echo "@@mem"; free -m | awk '/Mem:/ {print $2, $7}'
echo "@@disk"; df -P / | awk 'NR==2 {print $5}'; df -P /data 2>/dev/null | awk 'NR==2 {print $5}'
echo "@@services"; for s in bottennis caddy; do echo "$s $(systemctl is-active $s) $(systemctl show $s --property=NRestarts --value) $(systemctl show $s --property=ActiveEnterTimestamp --value)"; done
echo "@@commit"; cd /opt/bottennis && git log -1 --format='%h %cs %s'
echo "@@errors"; timeout 30 journalctl -u bottennis --since "-HOURSh" --no-pager -p err 2>/dev/null | grep -v '^-- ' | wc -l
timeout 30 journalctl -u bottennis --since "-HOURSh" --no-pager -p err 2>/dev/null | grep -v '^-- ' | tail -3 | cut -c1-220
echo "@@tracebacks"; timeout 30 journalctl -u bottennis --since "-HOURSh" --no-pager 2>/dev/null | grep -c 'Traceback'
echo "@@boots"; timeout 30 journalctl -u bottennis --since "-HOURSh" --no-pager -o cat 2>/dev/null | grep -E 'Mini App: (загрузка|не запустилось)' | tail -400
echo "@@db"; /opt/bottennis/.venv/bin/python - <<'PY'
import json, sqlite3
try:
    c = sqlite3.connect("file:/data/bottennis.db?mode=ro", uri=True)
    q = lambda sql: c.execute(sql).fetchone()[0]
    print(json.dumps({
        "players": q("select count(*) from players"),
        "matches_today": q("select count(*) from matches where status='completed' and completed_at >= datetime('now','start of day','-3 hours')"),
        "boots_7d": q("select count(*) from app_boots where created_at > datetime('now','-7 days')"),
        "failed_7d": q("select count(*) from app_boots where failed=1 and created_at > datetime('now','-7 days')"),
    }))
except Exception as e:
    print(json.dumps({"error": str(e)[:200]}))
PY
echo "@@backups"; ls -1t /data/backups 2>/dev/null | head -1; ls -1 /data/backups 2>/dev/null | wc -l
echo "@@end"
"""


def run_remote(host: str, hours: int) -> dict[str, list[str]] | None:
    cmd = ["ssh", "-o", f"ConnectTimeout={SSH_TIMEOUT_S}", "-o", "BatchMode=yes", f"root@{host}", "bash -s"]
    try:
        # байты, не текст: в текстовом режиме Windows превращает LF в CRLF, и
        # bash на сервере не находит конец вложенного heredoc
        out = subprocess.run(cmd, input=REMOTE.replace("HOURS", str(hours)).encode("utf-8"),
                             capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        add("FAIL", "SSH", "сервер не ответил за 3 минуты")
        return None
    stdout = out.stdout.decode("utf-8", "replace")
    if "@@end" not in stdout:
        err = out.stderr.decode("utf-8", "replace").strip().splitlines()
        add("FAIL", "SSH", "не удалось зайти: " + (err[-1] if err else f"код {out.returncode}"))
        return None
    blocks: dict[str, list[str]] = {}
    key = ""
    for line in stdout.splitlines():
        if line.startswith("@@"):
            key = line[2:]
            blocks[key] = []
        elif key:
            blocks[key].append(line)
    return blocks


def check_server(b: dict[str, list[str]], hours: int) -> None:
    add("OK", "Сервер", f"работает {b['uptime'][0]} (с {b['boot'][0]}), нагрузка {b['uptime'][1]}")
    total, avail = map(int, b["mem"][0].split())
    add("OK" if avail > 300 else "WARN", "Память", f"свободно {avail} из {total} МБ")
    for label, value in zip(("Диск /", "Диск /data"), b["disk"]):
        pct = int(value.rstrip("%"))
        add("OK" if pct < 80 else "WARN" if pct < 90 else "FAIL", label, f"занято {pct}%")
    for line in b["services"]:
        name, state, restarts, *since = line.split()
        level = "OK" if state == "active" else "FAIL"
        extra = f", перезапусков после сбоя: {restarts}" if restarts not in ("0", "") else ""
        add(level, f"Сервис {name}", f"{state} с {' '.join(since)}{extra}")
    add("OK", "Код на сервере", b["commit"][0])
    errors = int(b["errors"][0]) if b["errors"] else 0
    tracebacks = int(b["tracebacks"][0]) if b["tracebacks"] else 0
    level = "OK" if errors == 0 and tracebacks == 0 else "WARN"
    add(level, "Журнал бота", f"за {hours} ч ошибок: {errors}, трассировок: {tracebacks}")
    for line in b["errors"][1:]:
        add("WARN", "  последняя ошибка", line)
    try:
        db = json.loads(b["db"][0])
    except (IndexError, ValueError):
        db = {"error": "нет ответа"}
    if "error" in db:
        add("WARN", "База", db["error"])
    else:
        add("OK", "База", f"игроков {db['players']}, матчей сегодня {db['matches_today']}")
        add("OK" if db["failed_7d"] == 0 else "WARN", "Запуски приложения за 7 дней",
            f"{db['boots_7d']}, неудачных: {db['failed_7d']}")
    if len(b["backups"]) >= 2 and int(b["backups"][1]) > 0:
        count = int(b["backups"][1])
        # ночной бэкап хранит 7 последних; меньше трёх — скрипт бэкапа не работает
        # как задумано (до 16.10.2026 он перезаписывал один и тот же файл)
        add("OK" if count >= 3 else "WARN", "Бэкапы", f"{count} шт., последний: {b['backups'][0]}")
    else:
        add("WARN", "Бэкапы", "папка /data/backups пустая или её нет")


def check_boots(lines: list[str], hours: int) -> None:
    times, slow, fails = [], [], []
    for line in lines:
        if "не запустилось" in line:
            fails.append(line)
            continue
        try:
            ms = int(line.split("загрузка ", 1)[1].split(" ", 1)[0])
        except (IndexError, ValueError):
            continue
        times.append(ms)
        if ms >= SLOW_BOOT_MS:
            slow.append(line)
    if not times and not fails:
        add("OK", "Загрузки приложения", f"за {hours} ч запусков не было")
        return
    if times:
        add("OK" if not slow else "WARN", "Загрузки приложения",
            f"за {hours} ч: {len(times)}, медиана {int(statistics.median(times))} мс, "
            f"максимум {max(times)} мс, медленных (≥{SLOW_BOOT_MS // 1000} с): {len(slow)}")
    for line in slow[-3:]:
        add("WARN", "  медленный", line.split("Mini App: ", 1)[-1][:300])
    for line in fails[-3:]:
        add("FAIL", "  не запустилось", line.split("Mini App: ", 1)[-1][:300])


# ── Снаружи: само приложение и проверка из других стран ───────────────────────

def sslip_name(host: str) -> str:
    return f"{host.replace('.', '-')}.sslip.io"


def check_app_outside(host: str) -> None:
    # v2.200.1: приложение открывается по IP (без поиска адреса); sslip.io — для старых кнопок
    for url in (f"https://{host}/", f"https://{sslip_name(host)}/"):
        check_app_url(url)


def cert_days_left(host: str, name: str) -> float:
    """Сколько дней до конца сертификата, который сервер отдаёт для имени `name`."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=name) as tls:
            not_after = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    return (not_after - time.time()) / 86400


def check_certificates(host: str) -> None:
    for name, label, min_days in ((host, "по IP", CERT_IP_MIN_DAYS),
                                  (sslip_name(host), "sslip.io", CERT_NAME_MIN_DAYS)):
        try:
            days = cert_days_left(host, name)
        except Exception as e:
            add("FAIL", f"Сертификат {label}", f"не проверить: {e}")
            continue
        until = datetime.fromtimestamp(time.time() + days * 86400, UTC).strftime("%d.%m %H:%M UTC")
        level = "OK" if days >= min_days else "WARN" if days > 0 else "FAIL"
        note = "" if level == "OK" else (" — Caddy не продлевает: journalctl -u caddy" if days > 0 else " — истёк")
        add(level, f"Сертификат {label}", f"до {until}, осталось {days:.1f} дн{note}")


def check_app_url(url: str) -> None:
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            took = int((time.monotonic() - started) * 1000)
            gz = resp.headers.get("Content-Encoding") == "gzip"
            ok = resp.status == 200
    except Exception as e:
        add("FAIL", "Приложение (отсюда)", f"{url} не открылось: {e}")
        return
    if gz:
        import gzip
        body = gzip.decompress(body)
    alive = b"__clubBoot" in body
    level = "OK" if ok and alive and took < 3000 else "WARN"
    add(level, "Приложение (отсюда)", f"{url} — {took} мс, {len(body)} Б"
        + (", сжата" if gz else ", без сжатия") + ("" if alive else ", загрузчика в странице нет"))


def check_host_net(host: str) -> None:
    """Порт 443 из нескольких стран (check-host.net) — отличить «лёг сервер» от
    «плохая сеть у нас»."""
    try:
        api = f"https://check-host.net/check-tcp?host={host}:443&max_nodes=5"
        req = urllib.request.Request(api, headers=UA)
        rid = json.load(urllib.request.urlopen(req, timeout=20))["request_id"]
        data: dict = {}
        for _ in range(6):                     # узлы отвечают за 5–30 с
            time.sleep(5)
            req = urllib.request.Request(f"https://check-host.net/check-result/{rid}", headers=UA)
            data = json.load(urllib.request.urlopen(req, timeout=20))
            if data and all(v is not None for v in data.values()):
                break
    except Exception as e:
        add("WARN", "Снаружи (check-host)", f"проверка не удалась: {e}")
        return
    ok = bad = 0
    for res in data.values():
        if res and isinstance(res[0], dict) and "time" in res[0]:
            ok += 1
        elif res:
            bad += 1
    if not ok and not bad:
        add("WARN", "Снаружи (check-host)", "узлы не успели ответить")
        return
    level = "OK" if ok and not bad else "WARN" if ok else "FAIL"
    add(level, "Снаружи (check-host)", f"порт 443 открыт из {ok} стран, недоступен из {bad}")


def check_deployed_commit(blocks: dict[str, list[str]]) -> None:
    try:
        main = subprocess.run(["git", "rev-parse", "--short", "origin/main"], capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return
    deployed = blocks["commit"][0].split()[0] if blocks.get("commit") else ""
    if main and deployed:
        same = main.startswith(deployed) or deployed.startswith(main)
        add("OK" if same else "WARN", "Деплой", f"на сервере {deployed}, в origin/main {main}"
            + ("" if same else " — сервер отстаёт или git fetch не делался"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка прода bottennis (только чтение)")
    ap.add_argument("host", nargs="?", default=os.getenv("BOTTENNIS_PROD_HOST"))
    ap.add_argument("--hours", type=int, default=24, help="окно журнала, часов (по умолчанию 24)")
    ap.add_argument("--no-outside", action="store_true", help="без проверки из других стран")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.host:
        print("Нужен IP прод-сервера: аргументом или BOTTENNIS_PROD_HOST (в репозиторий не пишется).")
        return 2
    blocks = run_remote(args.host, args.hours)
    if blocks:
        check_server(blocks, args.hours)
        check_boots(blocks.get("boots", []), args.hours)
        check_deployed_commit(blocks)
    check_app_outside(args.host)
    check_certificates(args.host)
    if not args.no_outside:
        check_host_net(args.host)
    width = max(len(s) for _, s, _ in results)
    for level, section, text in results:
        print(f"[{level:4}] {section.ljust(width)}  {text}")
    levels = {lvl for lvl, _, _ in results}
    return 2 if "FAIL" in levels else 1 if "WARN" in levels else 0


if __name__ == "__main__":
    sys.exit(main())

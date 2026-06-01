"""
Анализ начислений рейтинга по реальной БД.
Запуск: python analyze_ratings.py /data/bottennis.db
         python analyze_ratings.py bottennis.db
"""
import json
import sqlite3
import sys
from collections import defaultdict

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "bottennis.db"

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
cur = con.cursor()

# ── Игроки ────────────────────────────────────────────────────────────────────
players = {r["id"]: r["display_name"] for r in cur.execute("SELECT id, display_name FROM players")}

# ── Завершённые матчи с winner_id (не ничьи) ─────────────────────────────────
rows = cur.execute("""
    SELECT m.id, m.challenger_id, m.challenged_id, m.winner_id,
           m.rating_change, m.sets_data, m.completed_at
    FROM matches m
    WHERE m.status = 'completed' AND m.winner_id IS NOT NULL
    ORDER BY m.completed_at
""").fetchall()

print(f"Всего завершённых матчей с победителем: {len(rows)}\n")

# ── 1. Распределение rating_change ────────────────────────────────────────────
print("=" * 60)
print("1. РАСПРЕДЕЛЕНИЕ НАЧИСЛЕНИЙ (все матчи)")
print("=" * 60)

deltas = [r["rating_change"] for r in rows if r["rating_change"] is not None]
if deltas:
    buckets = defaultdict(int)
    for d in deltas:
        bucket = round(d / 5) * 5  # группируем по 5
        buckets[bucket] += 1

    print(f"  Мин:    {min(deltas):.1f}")
    print(f"  Макс:   {max(deltas):.1f}")
    print(f"  Среднее:{sum(deltas)/len(deltas):.1f}")
    print(f"  Медиана:{sorted(deltas)[len(deltas)//2]:.1f}")
    print()
    print("  Диапазон  | Кол-во матчей")
    print("  ----------|" + "-" * 20)
    for b in sorted(buckets):
        bar = "█" * (buckets[b] // max(1, max(buckets.values()) // 30))
        print(f"  {b:>5.1f}–{b+4.9:<4.1f} | {buckets[b]:>4}  {bar}")

# ── 2. rating_change по формату матча ─────────────────────────────────────────
print()
print("=" * 60)
print("2. СРЕДНЕЕ НАЧИСЛЕНИЕ ПО ФОРМАТУ МАТЧА")
print("=" * 60)

format_stats = defaultdict(list)
for r in rows:
    if r["rating_change"] is None or not r["sets_data"]:
        continue
    try:
        sets = json.loads(r["sets_data"]) if isinstance(r["sets_data"], str) else r["sets_data"]
    except Exception:
        continue
    w_sets = sum(1 for s in sets if s["w"] > s["l"])
    total_sets = len(sets)
    fmt = f"{w_sets}-{total_sets - w_sets}"
    format_stats[fmt].append(r["rating_change"])

print(f"  {'Формат':<10} | {'Матчей':>7} | {'Мин':>6} | {'Среднее':>7} | {'Макс':>6}")
print("  " + "-" * 50)
for fmt in sorted(format_stats, key=lambda x: (int(x.split('-')[1]), x)):
    vals = format_stats[fmt]
    print(f"  {fmt:<10} | {len(vals):>7} | {min(vals):>6.1f} | {sum(vals)/len(vals):>7.1f} | {max(vals):>6.1f}")

# ── 3. Влияние разницы рейтингов на начисление ────────────────────────────────
# Нужно восстановить рейтинги «до матча» — считаем ретроспективно
print()
print("=" * 60)
print("3. ВЛИЯНИЕ РАЗНИЦЫ РЕЙТИНГОВ (ретроспективно)")
print("=" * 60)

# Стек рейтингов: восстанавливаем в обратном хронологическом порядке
player_rating_now = {r["id"]: r["rating"]
                     for r in con.execute("SELECT id, rating FROM players")}
rating_at = {}  # match_id -> (winner_rating_before, loser_rating_before)

rows_desc = sorted(rows, key=lambda x: x["completed_at"] or "", reverse=True)
rating_snapshot = dict(player_rating_now)

for r in rows_desc:
    delta = r["rating_change"]
    if delta is None:
        continue
    wid, lid = r["winner_id"], (r["challenged_id"] if r["winner_id"] == r["challenger_id"] else r["challenger_id"])
    # восстановить рейтинг ДО этого матча
    winner_r_after = rating_snapshot.get(wid, 1000.0)
    loser_r_after = rating_snapshot.get(lid, 1000.0)
    winner_r_before = round(winner_r_after - delta, 1)
    loser_r_before = round(loser_r_after + delta, 1)
    rating_at[r["id"]] = (winner_r_before, loser_r_before, delta)
    rating_snapshot[wid] = winner_r_before
    rating_snapshot[lid] = loser_r_before

gap_buckets = defaultdict(list)
for mid, (wr, lr, delta) in rating_at.items():
    gap = round((lr - wr) / 50) * 50  # группы по 50 pts
    gap_buckets[gap].append(delta)

print(f"  {'Отрыв (ло-победитель)':>25} | {'Матчей':>7} | {'Среднее Δ':>9} | {'Мин Δ':>6} | {'Макс Δ':>6}")
print("  " + "-" * 65)
for gap in sorted(gap_buckets):
    label = f"{'победитель сильнее' if gap < 0 else 'победитель слабее'} {abs(gap)}"
    if gap == 0:
        label = "~равные"
    vals = gap_buckets[gap]
    print(f"  {label:>25} | {len(vals):>7} | {sum(vals)/len(vals):>9.1f} | {min(vals):>6.1f} | {max(vals):>6.1f}")

# ── 4. Топ матчи по начислению ────────────────────────────────────────────────
print()
print("=" * 60)
print("4. ТОП-10 МАТЧЕЙ ПО НАЧИСЛЕНИЮ")
print("=" * 60)

top_rows = sorted(
    [r for r in rows if r["rating_change"] is not None],
    key=lambda x: x["rating_change"],
    reverse=True,
)[:10]

for i, r in enumerate(top_rows, 1):
    wname = players.get(r["winner_id"], "?")
    lid = r["challenged_id"] if r["winner_id"] == r["challenger_id"] else r["challenger_id"]
    lname = players.get(lid, "?")
    try:
        sets = json.loads(r["sets_data"]) if isinstance(r["sets_data"], str) else r["sets_data"]
        sets_str = "  ".join(f"{s['w']}:{s['l']}" for s in sets)
    except Exception:
        sets_str = "?"
    before = rating_at.get(r["id"])
    gap_str = f"(оппонент был +{before[1]-before[0]:.0f})" if before and before[1] > before[0] else \
              f"(оппонент был {before[1]-before[0]:.0f})" if before else ""
    print(f"  {i:>2}. +{r['rating_change']:.1f}  {wname} → {lname}  [{sets_str}]  {gap_str}")

# ── 5. Матчи с минимальным начислением ────────────────────────────────────────
print()
print("=" * 60)
print("5. МАТЧИ С НАИМЕНЬШИМ НАЧИСЛЕНИЕМ (повторный бонус?)")
print("=" * 60)

bot_rows = sorted(
    [r for r in rows if r["rating_change"] is not None],
    key=lambda x: x["rating_change"],
)[:10]

for i, r in enumerate(bot_rows, 1):
    wname = players.get(r["winner_id"], "?")
    lid = r["challenged_id"] if r["winner_id"] == r["challenger_id"] else r["challenger_id"]
    lname = players.get(lid, "?")
    try:
        sets = json.loads(r["sets_data"]) if isinstance(r["sets_data"], str) else r["sets_data"]
        sets_str = "  ".join(f"{s['w']}:{s['l']}" for s in sets)
    except Exception:
        sets_str = "?"
    before = rating_at.get(r["id"])
    gap_str = f"(оппонент был +{before[1]-before[0]:.0f})" if before and before[1] > before[0] else \
              f"(оппонент был {before[1]-before[0]:.0f})" if before else ""
    print(f"  {i:>2}. +{r['rating_change']:.1f}  {wname} → {lname}  [{sets_str}]  {gap_str}")

# ── 6. Статистика по парам игроков ────────────────────────────────────────────
print()
print("=" * 60)
print("6. СТАТИСТИКА ПО ПАРАМ (голова-к-голове)")
print("=" * 60)

pair_stats = defaultdict(lambda: {"wins_a": 0, "wins_b": 0, "total_delta": [], "names": ("?", "?")})

for r in rows:
    if r["rating_change"] is None:
        continue
    a, b = min(r["challenger_id"], r["challenged_id"]), max(r["challenger_id"], r["challenged_id"])
    key = (a, b)
    pair_stats[key]["names"] = (players.get(a, "?"), players.get(b, "?"))
    if r["winner_id"] == a:
        pair_stats[key]["wins_a"] += 1
    else:
        pair_stats[key]["wins_b"] += 1
    pair_stats[key]["total_delta"].append(r["rating_change"])

print(f"  {'Пара':<30} | {'Счёт':>7} | {'Матчей':>7} | {'Ср. Δ':>6}")
print("  " + "-" * 60)
for (a, b), s in sorted(pair_stats.items(), key=lambda x: -sum(x[1]["total_delta"])):
    na, nb = s["names"]
    score = f"{s['wins_a']}-{s['wins_b']}"
    n = len(s["total_delta"])
    avg = sum(s["total_delta"]) / n
    print(f"  {na} vs {nb:<20} | {score:>7} | {n:>7} | {avg:>6.1f}")

# ── 7. Текущие рейтинги ───────────────────────────────────────────────────────
print()
print("=" * 60)
print("7. ТЕКУЩИЕ РЕЙТИНГИ")
print("=" * 60)

standings = con.execute("""
    SELECT p.display_name, p.rating,
           COUNT(CASE WHEN m.winner_id = p.id THEN 1 END) as wins,
           COUNT(CASE WHEN m.winner_id != p.id AND m.winner_id IS NOT NULL THEN 1 END) as losses,
           COUNT(CASE WHEN m.winner_id IS NULL AND m.status='completed' THEN 1 END) as draws,
           COUNT(CASE WHEN m.status='completed' THEN 1 END) as total
    FROM players p
    LEFT JOIN matches m ON (m.challenger_id=p.id OR m.challenged_id=p.id)
    GROUP BY p.id
    ORDER BY p.rating DESC
""").fetchall()

print(f"  {'#':>3}  {'Имя':<20} {'Рейтинг':>8}  {'Матчи':>6}  {'W':>4}  {'L':>4}  {'D':>4}")
print("  " + "-" * 60)
for i, s in enumerate(standings, 1):
    print(f"  {i:>3}. {s['display_name']:<20} {s['rating']:>8.1f}  {s['total']:>6}  {s['wins']:>4}  {s['losses']:>4}  {s['draws']:>4}")

con.close()
print()
print("=" * 60)
print("Готово.")

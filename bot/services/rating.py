"""
Modified ELO rating system.

Base K=32. Bonuses applied on top:
  - Set dominance:    3-0 win gets more points than 3-2
  - Point dominance:  winning sets 11-0 gets more points than 11-9
  - Short match:      1-set match gets ×0.75 (sets_ratio=1.0 otherwise inflates delta)

Formula:
  multiplier      = 1 + 0.5*(sets_ratio - 0.5) + 0.3*(pts_ratio - 0.5)
  short_match_mult = SHORT_MATCH_MULT if len(sets_data) == 1 else 1.0

  sets_ratio = winner_sets / total_sets    (0.6 → 1.0)
  pts_ratio  = winner_total_pts / all_pts  (≈ 0.5 → 0.85)

Examples (500 vs 500 rating, so base_delta = 16):
  3-0, crushing: multiplier ≈ 1.35  → +21.6 pts
  3-2, close:    multiplier ≈ 1.06  → +16.9 pts
  1-0, 11:7:     multiplier ≈ 1.28, ×0.75 → +15.4 pts

Draw formula (standard ELO):
  challenger_delta = round(32 * (0.5 - E_challenger), 1)
  Positive if challenger was underdog, negative if challenger was favourite.
  Challenged gets the mirror value: -challenger_delta.
"""

SHORT_MATCH_MULT = 0.75   # штраф за матч из одной партии


def win_probability(rating_a: float, rating_b: float) -> float:
    """Ожидаемый результат игрока A против B по ELO (0..1).

    win_probability(a, b) + win_probability(b, a) == 1.
    """
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def calculate_rating_change(
    winner_rating: float,
    loser_rating: float,
    sets_data: list[dict],
) -> float:
    """Return delta: winner gains +delta, loser loses -delta."""
    E = win_probability(winner_rating, loser_rating)
    base_delta = 32 * (1 - E)

    w_sets = sum(1 for s in sets_data if s["w"] > s["l"])
    total_sets = len(sets_data)
    w_pts = sum(s["w"] for s in sets_data)
    total_pts = sum(s["w"] + s["l"] for s in sets_data)

    sets_ratio = w_sets / total_sets
    pts_ratio = w_pts / total_pts if total_pts else 0.5

    multiplier = 1 + 0.5 * (sets_ratio - 0.5) + 0.3 * (pts_ratio - 0.5)
    short_match_mult = SHORT_MATCH_MULT if len(sets_data) == 1 else 1.0
    return round(base_delta * multiplier * short_match_mult, 1)


def calculate_draw_rating_change(
    challenger_rating: float,
    challenged_rating: float,
) -> float:
    """Return challenger_delta (signed). Challenged gets -challenger_delta.

    Positive  → challenger was underdog, gains points.
    Negative  → challenger was favourite, loses points.
    Zero      → equal ratings.
    """
    E = win_probability(challenger_rating, challenged_rating)
    return round(32 * (0.5 - E), 1)

"""Валидация игровых данных — чистые функции без зависимостей."""


def validate_set_score(my_score: int, opp_score: int) -> str | None:
    """Валидирует счёт партии настольного тенниса.

    Возвращает None если счёт корректен, иначе ключ ошибки:
      "negative" | "draw" | "invalid"
    """
    if my_score < 0 or opp_score < 0:
        return "negative"
    if my_score == opp_score:
        return "draw"
    winner_score = max(my_score, opp_score)
    loser_score = min(my_score, opp_score)
    normal_win = winner_score == 11 and loser_score <= 9
    deuce_win = winner_score >= 12 and winner_score - loser_score == 2
    if not (normal_win or deuce_win):
        return "invalid"
    return None

from aiogram.fsm.state import State, StatesGroup


class MatchResultStates(StatesGroup):
    entering_set_score = State()
    confirming = State()


class PhotoStates(StatesGroup):
    """Своё фото для приложения (v2.174.0): ждём фото от игрока."""
    waiting = State()

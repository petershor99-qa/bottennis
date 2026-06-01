from aiogram.fsm.state import State, StatesGroup


class MatchResultStates(StatesGroup):
    entering_set_score = State()
    confirming = State()

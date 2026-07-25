from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    entering_promo_code = State()
    entering_topup_amount = State()

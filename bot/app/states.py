from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    entering_promo_code = State()
    entering_topup_amount = State()


class AdminStates(StatesGroup):
    broadcast_text = State()
    broadcast_segment = State()
    broadcast_confirm = State()

    promo_code = State()
    promo_bonus_days = State()
    promo_discount_percent = State()

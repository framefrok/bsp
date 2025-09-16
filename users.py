# users.py
from typing import Dict
import logging
import time

import database

logger = logging.getLogger(__name__)


def save_user_settings(user_id: int, has_anchor: bool, trade_level: int):
    """
    Сохраняет настройки пользователя: есть ли якорь и уровень торговли (0-10).
    """
    database.upsert_user_settings(user_id, bool(has_anchor), int(trade_level))
    logger.info(f"Сохранены настройки для {user_id}: anchor={has_anchor}, trade_level={trade_level}")


def get_user_settings(user_id: int) -> Dict[str, int]:
    """
    Возвращает settings: {'has_anchor': 0/1, 'trade_level': int, 'notify_personal', 'notify_interval'}
    """
    return database.get_user_settings_db(user_id)


def get_user_bonus(user_id: int) -> float:
    """
    Возвращает бонус в виде дробного коэффициента (например 0.22 для +22%).
    Формула: +2% от наличия якоря + 2% * trade_level.
    """
    s = get_user_settings(user_id)
    bonus = 0.02 if s.get("has_anchor", 0) else 0.0
    bonus += 0.02 * int(s.get("trade_level", 0))
    return float(bonus)


def adjust_prices_for_user(user_id: int, buy: float, sell: float):
    """
    Применяет бонусы пользователя к "сырым" ценам:
       покупка дешевле: buy_adj = buy / (1 + bonus)
       продажа дороже: sell_adj = sell * (1 + bonus)
    """
    bonus = get_user_bonus(user_id)
    try:
        adj_buy = float(buy) / (1 + bonus) if buy is not None else None
        adj_sell = float(sell) * (1 + bonus) if sell is not None else None
    except Exception:
        adj_buy = buy
        adj_sell = sell
    return adj_buy, adj_sell


# Notification control
def set_user_notification(user_id: int, enabled: bool, interval: int = 15):
    database.set_user_notify(user_id, bool(enabled), int(interval))
    logger.info(f"User {user_id} notify_personal set to {enabled} interval {interval}")


def get_user_notification_settings(user_id: int) -> Dict[str, int]:
    s = get_user_settings(user_id)
    return {"notify_personal": s.get("notify_personal", 1), "notify_interval": s.get("notify_interval", 15), "last_reminder": s.get("last_reminder", 0)}

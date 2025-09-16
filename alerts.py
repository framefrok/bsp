import asyncio
import logging
from datetime import datetime, timedelta
from database import get_db
from users import get_user_bonus, adjust_prices_for_user

logger = logging.getLogger(__name__)

# хранение задач по таймерам
timers = {}


async def set_timer(user_id: int, interval: int, callback, bot):
    """Запуск таймера для пользователя"""
    if user_id in timers:
        timers[user_id].cancel()

    async def timer_loop():
        while True:
            try:
                await callback(user_id, bot)
            except Exception as e:
                logger.error(f"Ошибка в таймере {user_id}: {e}")
            await asyncio.sleep(interval)

    task = asyncio.create_task(timer_loop())
    timers[user_id] = task


def cancel_timer(user_id: int):
    """Остановка таймера"""
    if user_id in timers:
        timers[user_id].cancel()
        del timers[user_id]


async def check_alerts_for_user(user_id: int, bot):
    """
    Проверка алертов (цены на рынке против условий игрока)
    """
    db = get_db()
    c = db.cursor()

    # берём последние цены
    c.execute("SELECT buy, sell, timestamp FROM market ORDER BY timestamp DESC LIMIT 1")
    row = c.fetchone()
    if not row:
        return

    raw_buy, raw_sell, ts = row
    buy, sell = adjust_prices_for_user(user_id, raw_buy, raw_sell)

    # проверка условий
    c.execute("SELECT id, type, value FROM alerts WHERE user_id=?", (user_id,))
    alerts = c.fetchall()

    for alert_id, a_type, value in alerts:
        if a_type == "buy_below" and buy <= value:
            await bot.send_message(user_id, f"📉 Цена покупки упала до {buy} (алерт {value})")
            c.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        elif a_type == "sell_above" and sell >= value:
            await bot.send_message(user_id, f"📈 Цена продажи поднялась до {sell} (алерт {value})")
            c.execute("DELETE FROM alerts WHERE id=?", (alert_id,))

    db.commit()

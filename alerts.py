# alerts.py
import threading
import time
import logging
from datetime import datetime, timedelta
from typing import List, Optional

import database
import users
import market

logger = logging.getLogger(__name__)


def calculate_speed(records: List[dict], price_field: str = "buy") -> Optional[float]:
    if not records or len(records) < 2:
        return None
    first = records[0]
    last = records[-1]
    price_delta = last[price_field] - first[price_field]
    time_delta_minutes = (last['timestamp'] - first['timestamp']) / 60.0
    if time_delta_minutes < 0.1:
        return None
    return round(price_delta / time_delta_minutes, 6)


def get_trend(records: List[dict], price_field: str = "buy") -> str:
    if not records or len(records) < 2:
        return "stable"
    first_price = records[0][price_field]
    last_price = records[-1][price_field]
    if last_price > first_price:
        return "up"
    elif last_price < first_price:
        return "down"
    else:
        return "stable"


def schedule_alert(alert_id: int, bot):
    try:
        alert = database.get_alert_by_id(alert_id)
        if not alert:
            return

        alert_time = datetime.fromisoformat(alert['alert_time'])
        now = datetime.now()
        sleep_s = (alert_time - now).total_seconds()
        if sleep_s > 0:
            time.sleep(sleep_s)

        current = database.get_latest_market(alert['resource'])
        if not current:
            bot.send_message(alert['user_id'], f"⚠️ Нет данных по {alert['resource']}.")
            database.update_alert_status(alert_id, 'error')
            return

        current_price_adj, _ = users.adjust_prices_for_user(alert['user_id'], current['buy'], current['sell'])

        reached = (
            (alert['direction'] == 'down' and current_price_adj <= alert['target_price'])
            or (alert['direction'] == 'up' and current_price_adj >= alert['target_price'])
        )

        if reached:
            bot.send_message(alert['user_id'], f"🔔 Таймер сработал! {alert['resource']} достигла {alert['target_price']:.2f} (текущая: {current_price_adj:.2f})")
            database.update_alert_status(alert_id, 'completed')

            if alert.get('chat_id'):
                bot.send_message(alert['chat_id'], f"🔔 Таймер @{alert['user_id']} выполнен: {alert['resource']}={current_price_adj:.2f}")
        else:
            bot.send_message(alert['user_id'], f"⏰ Время вышло. Цель {alert['target_price']:.2f} не достигнута. ({current_price_adj:.2f})")
            database.update_alert_status(alert_id, 'expired')

    except Exception:
        logger.exception("Ошибка в schedule_alert")
        database.update_alert_status(alert_id, 'error')


def update_dynamic_timers_once(bot):
    try:
        active_alerts = database.get_active_alerts()
        now = datetime.now()
        for alert in active_alerts:
            try:
                records = database.get_recent_market(alert['resource'], minutes=15)
                if not records or len(records) < 2:
                    continue

                latest = database.get_latest_market(alert['resource'])
                if not latest:
                    continue

                created_ts = datetime.fromisoformat(alert['created_at']).timestamp() if alert.get('created_at') else 0
                if latest['timestamp'] <= created_ts:
                    continue

                bonus = users.get_user_bonus(alert['user_id'])
                current_adj_price, _ = users.adjust_prices_for_user(alert['user_id'], latest['buy'], latest['sell'])
                speed_raw = calculate_speed(records, "buy")
                if not speed_raw:
                    continue

                adj_speed = speed_raw / (1 + bonus) if isinstance(bonus, float) else speed_raw
                current_trend = get_trend(records, "buy")

                if (alert['direction'] == "down" and current_trend == "up") or (alert['direction'] == "up" and current_trend == "down"):
                    bot.send_message(alert['user_id'], f"⚠️ Тренд {alert['resource']} изменился ({current_trend}), таймер остановлен.")
                    database.update_alert_status(alert['id'], 'trend_changed')
                    continue

                if (alert['direction'] == "down" and current_adj_price <= alert['target_price']) or (alert['direction'] == "up" and current_adj_price >= alert['target_price']):
                    bot.send_message(alert['user_id'], f"🔔 {alert['resource']} достигла цели {alert['target_price']:.2f} (текущая: {current_adj_price:.2f})")
                    database.update_alert_status(alert['id'], 'completed')
                    continue

                price_diff = alert['target_price'] - current_adj_price
                if adj_speed == 0 or (alert['direction'] == "down" and adj_speed >= 0) or (alert['direction'] == "up" and adj_speed <= 0):
                    continue

                time_minutes = abs(price_diff) / abs(adj_speed)
                new_alert_time = now + timedelta(minutes=time_minutes)

                database.update_alert_fields(alert['id'], {
                    'alert_time': new_alert_time.isoformat(),
                    'speed': adj_speed,
                    'current_price': current_adj_price
                })

            except Exception as e:
                logger.exception(f"Ошибка при обновлении алерта {alert.get('id')}: {e}")
    except Exception:
        logger.exception("Ошибка в update_dynamic_timers_once")


def cleanup_expired_alerts_loop():
    while True:
        try:
            now = datetime.now()
            active = database.get_active_alerts()
            for a in active:
                try:
                    if not a.get('alert_time'):
                        continue
                    at = datetime.fromisoformat(a['alert_time'])
                    if at < (now - timedelta(hours=1)):
                        database.update_alert_status(a['id'], 'cleanup_expired')
                        logger.info(f"❌ Таймер {a['id']} очищен как просроченный")
                except Exception:
                    continue
        except Exception:
            logger.exception("Ошибка в cleanup_expired_alerts_loop")
        time.sleep(600)


def stale_db_reminder_loop(bot):
    while True:
        try:
            global_ts = database.get_global_latest_timestamp()
            now_ts = int(time.time())
            delta = None if not global_ts else now_ts - global_ts

            if delta is not None and delta < 15 * 60:
                time.sleep(60)
                continue

            for u in database.get_users_with_notifications_enabled():
                uid = u["id"]
                interval = int(u.get("notify_interval", 15))
                last = int(u.get("last_reminder", 0))
                if now_ts - last >= interval * 60:
                    bot.send_message(uid, "⚠️ База не обновлялась >15 минут. Перешлите 🎪 рынок. Настройки: /push")
                    database.set_user_last_reminder(uid, now_ts)

            for c in database.get_chats_with_notifications_enabled():
                cid = c["chat_id"]
                interval = int(c.get("notify_interval", 15))
                last = int(c.get("last_reminder", 0))
                if now_ts - last >= interval * 60:
                    bot.send_message(cid, "⚠️ Внимание: рынок не обновлялся >15 минут. Нужен форвард 🎪.")
                    database.set_chat_last_reminder(cid, now_ts)

        except Exception:
            logger.exception("Ошибка в stale_db_reminder_loop")
        time.sleep(60)


def profitable_price_alerts_loop(bot):
    """
    Проверка выгодных цен для ресурсов (камень, дерево, провизия).
    Если цена выгодна и количество >= 10M → уведомляем всех.
    """
    resources = ["Камень", "Дерево", "Провизия"]
    while True:
        try:
            for res in resources:
                latest = database.get_latest_market(res)
                if not latest:
                    continue
                if latest.get("amount", 0) >= 10_000_000:
                    price = latest["buy"]
                    msg = f"🔥 Пора брать! {res} ({price:.2f}) ожидает твоей покупки.\n@all"
                    for chat in database.get_chats_with_notifications_enabled():
                        cid = chat["chat_id"]
                        try:
                            sent = bot.send_message(cid, msg)
                            bot.pin_chat_message(cid, sent.message_id, disable_notification=True)
                            database.insert_pinned_message(cid, sent.message_id)
                            database.cleanup_old_pins(cid, keep_last=5)
                        except Exception as e:
                            logger.debug(f"Не удалось отправить алерт в чат {cid}: {e}")
        except Exception:
            logger.exception("Ошибка в profitable_price_alerts_loop")
        time.sleep(300)


def update_dynamic_timers_loop(bot):
    while True:
        try:
            update_dynamic_timers_once(bot)
        except Exception:
            logger.exception("Ошибка в update_dynamic_timers_loop")
        time.sleep(60)


def start_background_tasks(bot):
    threading.Thread(target=cleanup_expired_alerts_loop, daemon=True).start()
    threading.Thread(target=update_dynamic_timers_loop, args=(bot,), daemon=True).start()
    threading.Thread(target=stale_db_reminder_loop, args=(bot,), daemon=True).start()
    threading.Thread(target=profitable_price_alerts_loop, args=(bot,), daemon=True).start()

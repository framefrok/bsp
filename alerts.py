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
    speed = price_delta / time_delta_minutes
    return round(speed, 6)


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
            try:
                bot.send_message(alert['user_id'], f"⚠️ Невозможно проверить цель: нет данных по {alert['resource']}.")
            except Exception:
                pass
            database.update_alert_status(alert_id, 'error')
            return

        current_price_adj, _ = users.adjust_prices_for_user(alert['user_id'], current['buy'], current['sell'])

        reached = False
        if alert['direction'] == 'down' and current_price_adj <= alert['target_price']:
            reached = True
        if alert['direction'] == 'up' and current_price_adj >= alert['target_price']:
            reached = True

        if reached:
            try:
                bot.send_message(alert['user_id'], f"🔔 Ваш таймер сработал! {alert['resource']} достигла {alert['target_price']:.2f}. Текущая: {current_price_adj:.2f}")
            except Exception:
                pass
            database.update_alert_status(alert_id, 'completed')
            try:
                if alert.get('chat_id'):
                    bot.send_message(alert['chat_id'], f"🔔 Таймер @{alert['user_id']} сработал: {alert['resource']} достигла {alert['target_price']:.2f} (текущая: {current_price_adj:.2f}).")
            except Exception:
                pass
        else:
            try:
                bot.send_message(alert['user_id'], f"⏰ Таймер сработал, но цель ({alert['target_price']:.2f}) не достигнута. Текущая: {current_price_adj:.2f}")
            except Exception:
                pass
            database.update_alert_status(alert_id, 'expired')

    except Exception as e:
        logger.exception("Ошибка в schedule_alert")
        try:
            database.update_alert_status(alert_id, 'error')
        except Exception:
            pass


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
                if speed_raw is None:
                    continue

                adj_speed = speed_raw / (1 + bonus) if isinstance(bonus, float) else speed_raw
                if adj_speed is None or adj_speed == 0:
                    continue

                current_trend = get_trend(records, "buy")
                if (alert['direction'] == "down" and current_trend == "up") or (alert['direction'] == "up" and current_trend == "down"):
                    try:
                        bot.send_message(alert['user_id'], f"⚠️ Тренд для {alert['resource']} изменился (теперь {current_trend}). Оповещение будет деактивировано.")
                    except Exception:
                        pass
                    database.update_alert_status(alert['id'], 'trend_changed')
                    continue

                if (alert['direction'] == "down" and current_adj_price <= alert['target_price']) or (alert['direction'] == "up" and current_adj_price >= alert['target_price']):
                    try:
                        bot.send_message(alert['user_id'], f"🔔 {alert['resource']} достигла цели {alert['target_price']:.2f} (текущая: {current_adj_price:.2f}).")
                    except Exception:
                        pass
                    database.update_alert_status(alert['id'], 'completed')
                    continue

                price_diff = alert['target_price'] - current_adj_price
                if (alert['direction'] == "down" and adj_speed >= 0) or (alert['direction'] == "up" and adj_speed <= 0):
                    continue

                time_minutes = abs(price_diff) / abs(adj_speed)
                new_alert_time = datetime.now() + timedelta(minutes=time_minutes)

                database.update_alert_fields(alert['id'], {
                    'alert_time': new_alert_time.isoformat(),
                    'speed': adj_speed,
                    'current_price': current_adj_price
                })

                try:
                    old = datetime.fromisoformat(alert['alert_time']) if alert.get('alert_time') else None
                    if old:
                        diff_min = abs((new_alert_time - old).total_seconds() / 60.0)
                        if diff_min > 5:
                            bot.send_message(alert['user_id'], f"🔄 Таймер для {alert['resource']} обновлён. Новое время: {new_alert_time.strftime('%H:%M:%S')}")
                except Exception:
                    pass

            except Exception as e:
                logger.exception(f"Ошибка при обновлении алерта {alert.get('id')}: {e}")
    except Exception as e:
        logger.exception("Ошибка в update_dynamic_timers_once")


def cleanup_expired_alerts_loop():
    while True:
        try:
            now = datetime.now()
            active = database.get_active_alerts()
            expired_ids = []
            for a in active:
                try:
                    if not a.get('alert_time'):
                        continue
                    at = datetime.fromisoformat(a['alert_time'])
                    if at < (now - timedelta(hours=1)):
                        expired_ids.append(a['id'])
                except Exception:
                    continue
            for aid in expired_ids:
                database.update_alert_status(aid, 'cleanup_expired')
                logger.info(f"Очистка: деактивирован алерт {aid} (просрочен)")
        except Exception as e:
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

            users_list = database.get_users_with_notifications_enabled()
            for u in users_list:
                uid = u["id"]
                interval = int(u.get("notify_interval", 15))
                last = int(u.get("last_reminder", 0))
                if now_ts - last >= interval * 60:
                    try:
                        bot.send_message(uid, "⚠️ База данных рынков не обновлялась более 15 минут. Пожалуйста, пришлите свежий форвард рынка (🎪). Вы можете отключить уведомления или изменить интервал в /push.")
                    except Exception:
                        pass
                    database.set_user_last_reminder(uid, now_ts)

            chats = database.get_chats_with_notifications_enabled()
            for c in chats:
                chat_id = c["chat_id"]
                interval = int(c.get("notify_interval", 15))
                last = int(c.get("last_reminder", 0))
                if now_ts - last >= interval * 60:
                    try:
                        bot.send_message(chat_id, "⚠️ Внимание: база данных рынков не обновлялась более 15 минут. Пожалуйста, пришлите форвард рынка или проверьте, что бот имеет доступ к сообщениям.")
                    except Exception:
                        pass
                    database.set_chat_last_reminder(chat_id, now_ts)

        except Exception:
            logger.exception("Ошибка в stale_db_reminder_loop")
        time.sleep(60)


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


def cmd_timer_handler(bot, message):
    try:
        parts = message.text.split()[1:]
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат команды: /timer <ресурс> <целевая цена>\nПример: /timer Дерево 8.50")
            return

        resource = parts[0].capitalize()
        try:
            target_price = float(parts[1].replace(',', '.'))
            if target_price <= 0:
                bot.reply_to(message, "❌ Цена должна быть положительным числом.")
                return
        except ValueError:
            bot.reply_to(message, "❌ Неверный формат цены. Пример: 8.50")
            return

        latest = database.get_latest_market(resource)
        if not latest:
            bot.reply_to(message, f"⚠️ Нет данных по {resource}. Пришлите форвард рынка.")
            return

        records = database.get_recent_market(resource, minutes=15)
        if len(records) < 2:
            bot.reply_to(message, f"⚠️ Недостаточно данных за 15 минут для {resource}.")
            return

        current_raw_buy = latest['buy']
        user_id = message.from_user.id
        current_buy_adj, _ = users.adjust_prices_for_user(user_id, current_raw_buy, latest['sell'])
        bonus = users.get_user_bonus(user_id)
        direction = "down" if target_price < current_buy_adj else "up"

        speed_raw = calculate_speed(records, "buy")
        if speed_raw is None:
            bot.reply_to(message, "⚠️ Не удалось рассчитать скорость изменения цены.")
            return

        adj_speed = speed_raw / (1 + bonus) if isinstance(bonus, float) else speed_raw
        if adj_speed == 0:
            bot.reply_to(message, "⚠️ Скорость изменения слишком мала.")
            return

        if (direction == "down" and target_price >= current_buy_adj) or (direction == "up" and target_price <= current_buy_adj):
            bot.reply_to(message, f"⚠️ Целевая цена должна быть {'ниже' if direction == 'down' else 'выше'} текущей ({current_buy_adj:.2f}).")
            return

        trend = get_trend(records, "buy")
        if (direction == "down" and trend == "up") or (direction == "up" and trend == "down"):
            bot.reply_to(message, "⚠️ Внимание — выбранное направление противоречит текущему тренду. Оповещение может не сработать.")

        if (direction == "down" and adj_speed >= 0) or (direction == "up" and adj_speed <= 0):
            bot.reply_to(message, "⚠️ Цена сейчас движется не в ту сторону. Оповещение не будет установлено.")
            return

        price_diff = target_price - current_buy_adj
        time_minutes = abs(price_diff) / abs(adj_speed)
        alert_time = datetime.now() + timedelta(minutes=time_minutes)

        chat_id = message.chat.id if message.chat.type in ['group', 'supergroup'] else None

        alert_id = database.insert_alert_record(user_id, resource, target_price, direction, adj_speed, current_buy_adj, alert_time.isoformat(), chat_id)

        alert_time_str = alert_time.strftime("%H:%M:%S")
        username = message.from_user.username or str(message.from_user.id)
        notify = (
            f"✅ Таймер установлен!\n"
            f"Пользователь: @{username}\n"
            f"Ресурс: {resource}\n"
            f"Текущая цена: {current_buy_adj:.2f}\n"
            f"Цель: {target_price:.2f} ({'падение' if direction == 'down' else 'рост'})\n"
            f"Скорость: {adj_speed:+.6f} в минуту\n"
            f"Осталось: ~{int(time_minutes)} мин.\n"
            f"Ожидаемое время: {alert_time_str}"
        )
        sent = bot.reply_to(message, notify)

        if chat_id and chat_id != user_id:
            try:
                bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
                database.upsert_chat_settings(chat_id, True, database.get_chat_settings(chat_id)["notify_interval"], pinned_message_id=sent.message_id)
            except Exception:
                pass

        threading.Thread(target=schedule_alert, args=(alert_id, bot), daemon=True).start()

    except Exception:
        logger.exception("Ошибка в cmd_timer_handler")
        bot.reply_to(message, "❌ Произошла ошибка при установке таймера.")

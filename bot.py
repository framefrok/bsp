# bot.py
import logging
import telebot
from telebot import types
import database
import users
import alerts
import market
import time
from datetime import datetime

TOKEN = "YOUR_BOT_TOKEN_HERE"
bot = telebot.TeleBot(TOKEN)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

alerts.start_background_tasks(bot)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    users.ensure_user(message.from_user.id, message.from_user.username)
    welcome = f"Привет, @{message.from_user.username}! Бот BS Market Analytics запущен.\n\nДоступные команды:\n/stat - Статистика\n/history [ресурс] - История\n/timer <ресурс> <цена> - Таймер\n/status - Оповещения\n/settings - Настройки\n/push - Уведомления\n/help - Помощь\n\nПерешлите 🎪 для обновления рынка."
    bot.reply_to(message, welcome)

@bot.message_handler(commands=['help'])
def cmd_help(message):
    alerts.cmd_help_handler(bot, message)

@bot.message_handler(commands=['stat'])
def cmd_stat(message):
    user_id = message.from_user.id
    bonus_pct = int(users.get_user_bonus(user_id) * 100)
    now = datetime.now()
    global_ts = database.get_global_latest_timestamp()
    update_str = datetime.fromtimestamp(global_ts).strftime("%d.%m.%Y %H:%M") if global_ts else "Неизвестно"

    resources = ['Дерево', 'Камень', 'Провизия', 'Лошади']
    reply = f"📊 Текущая статистика рынка\n🕗 Обновлено: {update_str}\n🔃 Бонус игрока: {bonus_pct}%\n──────────────────────\n\n"
    week_start = int(time.time()) - 7*24*3600

    for res in resources:
        pred_buy, pred_sell, trend, speed, last_ts = market.compute_extrapolated_price(res, user_id)
        if pred_buy is None:
            continue
        last_update_str = datetime.fromtimestamp(last_ts).strftime("%H:%M") if last_ts else "N/A"
        was_buy, was_sell = users.adjust_prices_for_user(user_id, database.get_market_week_max_qty(res, 'buy', week_start)[0], database.get_market_week_max_qty(res, 'sell', week_start)[0])  # simplified
        buy_range = database.get_market_week_range(res, 'buy', week_start)
        sell_range = database.get_market_week_range(res, 'sell', week_start)
        max_qty = database.get_market_week_max_qty(res, week_start)
        trend_emoji = "📈" if trend == "up" else "📉" if trend == "down" else "➖"
        speed_str = f"{speed:+.4f}/мин" if speed else "0"
        reply += f"{market.RESOURCE_EMOJI.get(res, '')} {res}\n"
        reply += f"├ 🕒 Последнее обновление: {last_update_str}\n"
        reply += f"├ 💹 Покупка: {pred_buy:>8.3f} (было: {was_buy:.3f})\n"
        reply += f"│   Диапазон за неделю: {buy_range[0]:.3f} — {buy_range[1]:.3f}\n"
        reply += f"├ 💰 Продажа: {pred_sell:>8.3f} (было: {was_sell:.3f})\n"
        reply += f"│   Диапазон за неделю: {sell_range[0]:.3f} — {sell_range[1]:.3f}\n"
        reply += f"├ 📦 Макс. объём: {max_qty:,} шт.\n"
        reply += f"└ 📊 Тренд: {trend_emoji} {'растёт' if trend=='up' else 'падает' if trend=='down' else 'стабилен'} ({speed_str})\n\n"

    reply += "──────────────────────\n📈 — рост | 📉 — падение | ➖ — стабильно\nЦены скорректированы с учетом бонусов игрока ({bonus_pct}%)."
    bot.reply_to(message, reply)

@bot.message_handler(commands=['history'])
def cmd_history(message):
    parts = message.text.split()
    resource = parts[1].capitalize() if len(parts) > 1 else None
    if not resource or resource not in ['Дерево', 'Камень', 'Провизия', 'Лошади']:
        bot.reply_to(message, "Укажите ресурс: /history Дерево")
        return
    records = database.get_market_history(resource, hours=24)
    if not records:
        bot.reply_to(message, f"Нет истории для {resource}.")
        return
    reply = f"BS Market Analytics:\n📊 История цен на {resource} за последние 24 часов:\n\n"
    grouped = {}
    for r in records:
        dt = datetime.fromtimestamp(r['timestamp'])
        hour = dt.hour
        if hour not in grouped:
            grouped[hour] = []
        grouped[hour].append(r)
    for hour in sorted(grouped, reverse=True):
        reply += f"🕐 {hour:02d}:00:\n"
        for rec in sorted(grouped[hour], key=lambda x: x['timestamp']):
            time_str = datetime.fromtimestamp(rec['timestamp']).strftime("%H:%M")
            buy_adj, sell_adj = users.adjust_prices_for_user(message.from_user.id, rec['buy'], rec['sell'])
            reply += f"  {time_str} - Купить: {buy_adj:.2f}, Продать: {sell_adj:.2f}\n"
        reply += "\n"
    trend = market.get_trend(records, "buy")
    speed = alerts.calculate_speed(records, "buy")
    trend_str = f"Тренд: {'падает 📉' if trend=='down' else 'растёт 📈' if trend=='up' else 'стабилен ➖'} ({speed:+.4f}/мин)" if speed else "Тренд: стабильный"
    reply += trend_str
    bot.reply_to(message, reply)

@bot.message_handler(commands=['status'])
def cmd_status(message):
    alerts.cmd_status_handler(bot, message)

@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    alerts.cmd_cancel_handler(bot, message)

@bot.message_handler(commands=['settings'])
def cmd_settings(message):
    user_id = message.from_user.id
    user = database.get_user(user_id)
    anchor = bool(user.get('anchor', 0))
    trade_level = user.get('trade_level', 0)
    bonus = (0.02 if anchor else 0) + (0.02 * trade_level)
    users.set_user_bonus(user_id, bonus)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Якорь: " + ("Вкл" if anchor else "Выкл"), callback_data="settings_anchor"))
    markup.add(types.InlineKeyboardButton(f"Уровень торговли: {trade_level}", callback_data="settings_trade"))
    reply = f"⚙️ Настройки:\nЯкорь: {'Да (+2%)' if anchor else 'Нет'}\nУровень торговли: {trade_level} (+{trade_level*2}%)\nБонус: {bonus*100:.0f}%"
    bot.reply_to(message, reply, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("settings_"))
def callback_settings(call):
    user_id = call.from_user.id
    if call.data == "settings_anchor":
        current = database.get_user(user_id).get('anchor', 0)
        new = 1 - current
        database.update_user_field(user_id, 'anchor', new)
        bonus = users.get_user_bonus(user_id)
        bot.answer_callback_query(call.id, f"Якорь {'включен' if new else 'выключен'} ({bonus*100:.0f}%)")
    elif call.data == "settings_trade":
        bot.answer_callback_query(call.id, "Отправьте новый уровень торговли (число)")
        bot.register_next_step_handler(call.message, set_trade_level)

def set_trade_level(message):
    try:
        level = int(message.text)
        database.update_user_field(message.from_user.id, 'trade_level', level)
        bonus = users.get_user_bonus(message.from_user.id)
        bot.reply_to(message, f"Уровень торговли: {level} ({bonus*100:.0f}%)")
    except ValueError:
        bot.reply_to(message, "Неверное число.")

@bot.message_handler(commands=['push'])
def cmd_push(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    is_group = message.chat.type in ['group', 'supergroup']
    settings = database.get_user_push_settings(user_id) if not is_group else database.get_chat_settings(chat_id)
    markup = types.InlineKeyboardMarkup()
    enabled_text = "Включить" if not settings.get('notify_enabled', True) else "Отключить"
    markup.add(types.InlineKeyboardButton(f"{enabled_text} уведомления", callback_data="push_toggle"))
    markup.add(types.InlineKeyboardButton(f"Интервал: {settings.get('notify_interval', 15)} мин", callback_data="push_interval"))
    if is_group:
        markup.add(types.InlineKeyboardButton("Открепить все", callback_data="push_unpin"))
        markup.add(types.InlineKeyboardButton("Не закреплять в чате", callback_data="push_no_pin"))
    bot.reply_to(message, "⚡ Настройки уведомлений:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("push"))
def callback_push(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    is_group = call.message.chat.type in ['group', 'supergroup']
    settings = database.get_user_push_settings(user_id) if not is_group else database.get_chat_settings(chat_id)
    if call.data == "push_toggle":
        new_status = not settings.get('enabled', True)
        if is_group:
            database.upsert_chat_settings(chat_id, new_status, settings['notify_interval'])
        else:
            database.update_user_push_settings(user_id, enabled=new_status)
        bot.answer_callback_query(call.id, f"Уведомления {'включены' if new_status else 'отключены'}")
    elif call.data == "push_interval":
        bot.answer_callback_query(call.id, "Отправьте число минут")
        if is_group:
            bot.register_next_step_handler(call.message, lambda m: set_chat_interval(m, chat_id))
        else:
            bot.register_next_step_handler(call.message, lambda m: set_user_interval(m, user_id))
    elif call.data == "push_unpin":
        database.unpin_all_messages(chat_id)
        bot.answer_callback_query(call.id, "Все закрепленные сообщения откреплены")
    elif call.data == "push_no_pin":
        database.set_chat_no_pin(chat_id, True)
        bot.answer_callback_query(call.id, "Закрепление отключено")

def set_user_interval(message, user_id):
    try:
        minutes = int(message.text)
        database.update_user_push_settings(user_id, interval=minutes)
        bot.reply_to(message, f"Интервал: {minutes} мин")
    except ValueError:
        bot.reply_to(message, "Неверный формат")

def set_chat_interval(message, chat_id):
    try:
        minutes = int(message.text)
        settings = database.get_chat_settings(chat_id)
        database.upsert_chat_settings(chat_id, settings['notify_enabled'], minutes)
        bot.reply_to(message, f"Интервал: {minutes} мин")
    except ValueError:
        bot.reply_to(message, "Неверный формат")

@bot.message_handler(commands=['timer'])
def cmd_timer(message):
    alerts.cmd_timer_handler(bot, message)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_forward(message):
    if "🎪" in message.text:
        market.handle_market_forward(bot, message)

def main():
    logger.info("Бот запущен.")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        logger.exception(f"Ошибка при запуске polling: {e}")

if __name__ == "__main__":
    main()

import logging
import telebot
from telebot import types
from database import init_db, get_db, save_pinned_message, get_pinned_messages, delete_pinned_message
from users import ensure_user, set_user_bonus, adjust_prices_for_user
from alerts import check_alerts_for_user, set_timer, cancel_timer

API_TOKEN = "YOUR_BOT_TOKEN"
bot = telebot.TeleBot(API_TOKEN)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

init_db()


# ======================
# Команды
# ======================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    ensure_user(message.from_user.id)
    bot.reply_to(message, "👋 Привет! Я бот для отслеживания рынка.\nИспользуй /settings для бонусов, /push для уведомлений.")


@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    """Установка бонусов"""
    ensure_user(message.from_user.id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Бонусы покупки -1", callback_data="bonus:buy:-1"))
    kb.add(types.InlineKeyboardButton("Бонусы продажи +1", callback_data="bonus:sell:+1"))
    bot.send_message(message.chat.id, "⚙️ Настройки бонусов:", reply_markup=kb)


@bot.message_handler(commands=["push"])
def cmd_push(message):
    """Меню пушей"""
    db = get_db()
    c = db.cursor()
    c.execute("INSERT OR IGNORE INTO push_settings (chat_id) VALUES (?)", (message.chat.id,))
    db.commit()

    c.execute("SELECT enabled, interval, pin_messages FROM push_settings WHERE chat_id=?", (message.chat.id,))
    row = c.fetchone()
    enabled, interval, pin = row

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔔 Вкл" if enabled else "🔕 Выкл", callback_data="push:toggle"))
    kb.add(types.InlineKeyboardButton(f"⏱ Интервал: {interval} мин", callback_data="push:interval"))
    kb.add(types.InlineKeyboardButton("📌 Закреплять" if not pin else "❌ Не закреплять", callback_data="push:pin"))
    kb.add(types.InlineKeyboardButton("🗑 Удалить все закрепы", callback_data="push:clearpins"))

    bot.send_message(message.chat.id, "⚙️ Настройки уведомлений:", reply_markup=kb)


@bot.message_handler(commands=["stat"])
def cmd_stat(message):
    """Показ статистики бота"""
    db = get_db()
    c = db.cursor()
    c.execute("SELECT buy, sell, resource, amount, timestamp FROM market ORDER BY timestamp DESC LIMIT 1")
    last = c.fetchone()

    if not last:
        bot.reply_to(message, "❌ Данных о рынке пока нет.")
        return

    user_id = message.from_user.id
    buy, sell = adjust_prices_for_user(user_id, last["buy"], last["sell"])
    text = (
        f"📊 Статистика\n"
        f"Ресурс: {last['resource']}\n"
        f"Цена покупки: {buy}\n"
        f"Цена продажи: {sell}\n"
        f"Кол-во: {last['amount']:,}\n"
        f"Обновлено: {last['timestamp']}"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["history"])
def cmd_history(message):
    """История изменений бота (заглушка)"""
    text = (
        "📜 История обновлений:\n"
        "v1.0 — старт\n"
        "v1.1 — бонусы\n"
        "v1.2 — push + закрепы\n"
        "v1.3 — stat/history\n"
    )
    bot.reply_to(message, text)


# ======================
# Callback обработка
# ======================

@bot.callback_query_handler(func=lambda call: call.data.startswith("bonus:"))
def callback_bonus(call):
    parts = call.data.split(":")
    typ, val = parts[1], int(parts[2])
    user_id = call.from_user.id
    db = get_db()
    c = db.cursor()
    c.execute("SELECT buy_bonus, sell_bonus FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if row:
        buy_bonus, sell_bonus = row
        if typ == "buy":
            buy_bonus += val
        else:
            sell_bonus += val
        set_user_bonus(user_id, buy_bonus, sell_bonus)
    bot.answer_callback_query(call.id, "Бонус обновлён!")


@bot.callback_query_handler(func=lambda call: call.data.startswith("push:"))
def callback_push(call):
    chat_id = call.message.chat.id
    db = get_db()
    c = db.cursor()
    c.execute("SELECT enabled, interval, pin_messages FROM push_settings WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    enabled, interval, pin = row

    if call.data == "push:toggle":
        enabled = 0 if enabled else 1
        c.execute("UPDATE push_settings SET enabled=? WHERE chat_id=?", (enabled, chat_id))
        bot.answer_callback_query(call.id, "Уведомления переключены")

    elif call.data == "push:interval":
        interval = 30 if interval == 15 else 15
        c.execute("UPDATE push_settings SET interval=? WHERE chat_id=?", (interval, chat_id))
        bot.answer_callback_query(call.id, f"Интервал: {interval} мин")

    elif call.data == "push:pin":
        pin = 0 if pin else 1
        c.execute("UPDATE push_settings SET pin_messages=? WHERE chat_id=?", (pin, chat_id))
        bot.answer_callback_query(call.id, "Настройка закрепов изменена")

    elif call.data == "push:clearpins":
        pinned = get_pinned_messages(chat_id)
        for rec in pinned:
            try:
                bot.unpin_chat_message(chat_id, rec["message_id"])
                bot.delete_message(chat_id, rec["message_id"])
            except Exception as e:
                logging.error(f"Ошибка при удалении закрепа: {e}")
            delete_pinned_message(rec["id"])
        bot.answer_callback_query(call.id, "Все закрепы удалены")

    db.commit()


# ======================
# Запуск
# ======================
if __name__ == "__main__":
    logging.info("Бот запущен...")
    bot.infinity_polling(skip_pending=True)

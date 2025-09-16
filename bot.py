# bot.py
import logging
import telebot
from telebot import types
import database
import users
import alerts
import market
from datetime import datetime

TOKEN = "YOUR_BOT_TOKEN_HERE"
bot = telebot.TeleBot(TOKEN)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

alerts.start_background_tasks(bot)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    users.ensure_user(message.from_user.id, message.from_user.username)
    bot.reply_to(message, f"Привет, @{message.from_user.username}! Бот запущен и готов к работе.")

@bot.message_handler(commands=['stat'])
def cmd_stat(message):
    stats = database.get_bot_stats()
    reply = "📊 Статистика бота:\n"
    reply += f"- Всего пользователей: {stats.get('users', 0)}\n"
    reply += f"- Всего ресурсов: {stats.get('resources', 0)}\n"
    reply += f"- Последняя цена Камень: {stats.get('stone', 'N/A')}\n"
    reply += f"- Последняя цена Дерево: {stats.get('wood', 'N/A')}\n"
    reply += f"- Последняя цена Провизия: {stats.get('food', 'N/A')}\n"
    reply += f"- Максимальное количество ресурсов за неделю: {stats.get('max_week', 0)}"
    bot.reply_to(message, reply)

@bot.message_handler(commands=['history'])
def cmd_history(message):
    records = database.get_bot_history(limit=20)
    if not records:
        bot.reply_to(message, "История пуста.")
        return
    reply = "📜 Последние обновления:\n"
    for r in records:
        time_str = datetime.fromtimestamp(r['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
        reply += f"- {time_str}: {r['text']}\n"
    bot.reply_to(message, reply)

@bot.message_handler(commands=['push'])
def cmd_push(message):
    users.ensure_user(message.from_user.id, message.from_user.username)
    settings = database.get_user_push_settings(message.from_user.id)
    markup = types.InlineKeyboardMarkup()
    if settings.get('enabled', True):
        markup.add(types.InlineKeyboardButton("Отключить уведомления", callback_data="push_toggle"))
    else:
        markup.add(types.InlineKeyboardButton("Включить уведомления", callback_data="push_toggle"))
    markup.add(types.InlineKeyboardButton("Изменить интервал (мин)", callback_data="push_interval"))
    bot.reply_to(message, "⚡ Настройки уведомлений:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("push"))
def callback_push(call):
    user_id = call.from_user.id
    settings = database.get_user_push_settings(user_id)
    if call.data == "push_toggle":
        new_status = not settings.get('enabled', True)
        database.update_user_push_settings(user_id, enabled=new_status)
        bot.answer_callback_query(call.id, f"Уведомления {'включены' if new_status else 'отключены'}")
    elif call.data == "push_interval":
        bot.answer_callback_query(call.id, "Отправьте число минут для интервала уведомлений")
        @bot.message_handler(func=lambda m: m.from_user.id == user_id)
        def set_interval(message):
            try:
                minutes = int(message.text)
                database.update_user_push_settings(user_id, interval=minutes)
                bot.reply_to(message, f"Интервал уведомлений установлен на {minutes} мин.")
            except ValueError:
                bot.reply_to(message, "Неверный формат. Введите число.")

@bot.message_handler(commands=['timer'])
def cmd_timer(message):
    alerts.cmd_timer_handler(bot, message)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_forward(message):
    if "🎪" in message.text:
        try:
            resource_data = market.parse_market_forward(message.text)
            for r in resource_data:
                database.insert_market_record(r['resource'], r['buy'], r['sell'], r['timestamp'])
            bot.reply_to(message, "✅ Данные рынка обновлены.")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка при обработке форварда: {e}")

def main():
    logger.info("Бот запущен.")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        logger.exception(f"Ошибка при запуске polling: {e}")

if __name__ == "__main__":
    main()

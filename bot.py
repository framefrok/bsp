import logging
import threading
from datetime import datetime
import telebot

from database import users_table, alerts_table, market_table
from market import handle_market_forward, get_latest_data, adjust_prices_for_user
from alerts import start_background_tasks
from users import cmd_settings, get_user_bonus

# 🔧 Логгер
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MarketBot")

# ⚙️ Инициализация бота
API_TOKEN = ""
bot = telebot.TeleBot(API_TOKEN, parse_mode="HTML")

# =====================================================
# Команды
# =====================================================

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message, "👋 Привет! Я бот для анализа рынка.\n"
                          "Доступные команды:\n"
                          "/settings — установить бонусы\n"
                          "/push — опубликовать рынок в чате\n"
                          "/timer — установить таймер\n")

@bot.message_handler(commands=['settings'])
def cmd_settings_handler(message):
    cmd_settings(bot, message)

@bot.message_handler(commands=['push'])
def cmd_push(message):
    """
    Публикация текущего рынка в чат.
    Учитывает бонусы пользователя и поддержку групп.
    """
    user_id = message.from_user.id
    bonus = get_user_bonus(user_id)

    resources = market_table.all()
    if not resources:
        bot.reply_to(message, "⚠️ Рынок пока пуст. Пришлите форвард рынка.")
        return

    lines = ["📊 <b>Рынок</b>:"]
    for rec in resources:
        res = rec['resource']
        latest = get_latest_data(res)
        if not latest:
            continue
        adj_buy, adj_sell = adjust_prices_for_user(user_id, latest['buy'], latest['sell'])
        lines.append(f"{res}: Покупка {adj_buy:.2f} / Продажа {adj_sell:.2f}")

    text = "\n".join(lines)
    chat_id = message.chat.id

    sent = bot.send_message(chat_id, text)

    # Пытаемся закрепить в группе
    if message.chat.type in ['group', 'supergroup']:
        try:
            bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
            alerts_table.update({'message_id': sent.message_id}, doc_ids=[rec.doc_id for rec in market_table.all()])
        except Exception as e:
            logger.error(f"Не удалось закрепить сообщение в группе {chat_id}: {e}")
            bot.send_message(user_id, "⚠️ Не удалось закрепить сообщение в группе. "
                                      "Данные всё равно отправлены.")

@bot.message_handler(commands=['timer'])
def cmd_timer(message):
    from alerts import cmd_timer_handler
    cmd_timer_handler(bot, message)

# =====================================================
# Парсинг форвардов рынка
# =====================================================

@bot.message_handler(content_types=['text'])
def handle_forward(message):
    """
    Обработка форварда рынка (игрок пересылает сообщение из игры).
    """
    if not message.forward_from:
        return

    try:
        user_id = message.from_user.id
        handle_market_forward(bot, message, user_id)
    except Exception as e:
        logger.error(f"Ошибка при парсинге форварда: {e}", exc_info=True)
        bot.reply_to(message, "❌ Не удалось обработать форвард. Проверьте формат.")

# =====================================================
# Запуск
# =====================================================

if __name__ == '__main__':
    logger.info("🚀 Бот запущен и готов к работе!")
    start_background_tasks(bot)
    bot.polling(none_stop=True, interval=0, timeout=20)

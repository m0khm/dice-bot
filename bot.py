import logging
import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from game import TournamentManager

# ——— Настройка логирования ———
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ——— Читаем токен и инициализируем менеджер ———
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Нужно указать BOT_TOKEN в .env")

tournament = TournamentManager()

# Список ID админов (дополнительно к настоящим администраторам чата)
ALLOWED_IDS = {123456789, 987654321}  # <-- вставьте сюда свои Telegram ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и справка."""
    text = (
        "Привет! Я TournamentBot🎲\n\n"
        "Команды:\n"
        "/game — собрать участников (только админ)\n"
        "/game_start — запустить турнир (только админ)\n"
        "/dice — бросить кубик (во время хода)\n"
    )
    await update.effective_chat.send_message(text=text)

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать сбор участников (только админы)."""
    chat = update.effective_chat
    user = update.effective_user

    # Проверка на админа чата или в ALLOWED_IDS
    member = await context.bot.get_chat_member(chat.id, user.id)
    if not (member.status in ("administrator", "creator") or user.id in ALLOWED_IDS):
        return await update.message.reply_text("❌ Только администратор может начать сбор участников.")

    # Начинаем сбор
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Участвую", callback_data="join_game")
    )
    await context.bot.send_message(
        chat_id=chat.id,
        text="📝 Набор на игру открыт! Нажмите «Участвую» чтобы записаться.",
        reply_markup=kb,
    )

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки «Участвую»."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    added = tournament.add_player(query.message.chat.id, user)
    if not added:
        return  # уже записан или набор не идёт
    txt = (
        "📝 Набор на игру открыт! Участвуют:\n"
        f"{tournament.list_players(query.message.chat.id)}"
    )
    await query.edit_message_text(text=txt, reply_markup=query.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить турнир (только админы)."""
    chat = update.effective_chat
    user = update.effective_user
    member = await context.bot.get_chat_member(chat.id, user.id)
    if not (member.status in ("administrator", "creator") or user.id in ALLOWED_IDS):
        return await update.message.reply_text("❌ Только администратор может запускать турнир.")

    try:
        text, kb = tournament.start_tournament(chat.id)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение готовности пары."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user = query.from_user

    result = tournament.confirm_ready(chat_id, user, query.data)
    if result:
        # result — либо строка с чьим ходом, либо строка "Раунд ...", либо финал
        # удаляем старую кнопку
        await query.message.delete()
        await context.bot.send_message(chat_id=chat_id, text=result)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бросить кубик."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = tournament.roll_dice(chat_id, user)
    await update.message.reply_text(text)

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("game", game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_cb, pattern="^ready_"))
    app.add_handler(CommandHandler("dice", dice))

    app.run_polling()

if __name__ == "__main__":
    main()

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

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Укажите BOT_TOKEN в файле .env")

# Менеджер турниров: хранит все состояния по чатам
tournament = TournamentManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и список команд."""
    chat = update.effective_chat
    text = (
        "Привет! Я TournamentBot🎲\n\n"
        "Команды:\n"
        "/game — собрать участников\n"
        "/game_start — запустить турнир\n"
        "/dice — бросить кубик (во время хода)\n"
    )
    await context.bot.send_message(chat_id=chat.id, text=text)

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ запускает сбор участников."""
    chat = update.effective_chat
    if not update.effective_user or not update.effective_user.id:
        return
    # только админы
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("Только администратор может запускать сбор.")
    keyboard = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Участвую", callback_data="join_game")
    )
    tournament.begin_signup(chat.id)
    await context.bot.send_message(
        chat_id=chat.id,
        text="Набор на игру! Нажмите «Участвую», чтобы записаться.",
        reply_markup=keyboard,
    )

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия кнопки «Участвую»."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    added = tournament.add_player(query.message.chat.id, user)
    if added:
        await query.edit_message_text(
            text=f"Набор на игру! Участвуют: {tournament.list_players(query.message.chat.id)}",
            reply_markup=query.message.reply_markup,
        )

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запустить первый раунд."""
    chat_id = update.effective_chat.id
    try:
        bracket_msg, keyboard = tournament.start_tournament(chat_id)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    await context.bot.send_message(chat_id=chat_id, text=bracket_msg, reply_markup=keyboard)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение готовности пары."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user = query.from_user
    result = await tournament.confirm_ready(chat_id, user, context.bot)
    if result:
        await query.message.delete() # удаляем старую кнопку
        # если оба готовы — отправляем ход первому
        await context.bot.send_message(chat_id=chat_id, text=result)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бросить кубик во время хода."""
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
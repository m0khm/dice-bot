# bot.py
import logging
import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from game import TournamentManager

# ──────────── Логирование ────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ──────────── Токен ────────────
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")

# Текст для /start
COMMANDS_TEXT = (
    "Привет! Я бот-рандомайзер. Доступные команды:\n"
        "/start — 🤖 Все функции этого бота\n"
        "/game — 👤 Начать побор участников (админ)\n"
        "/game_start — 🎮 Запустить турнир (админ)\n"
        "/dice — 🎲 Бросить кубик во время хода\n"
        "/help — 🛟 Помощь\n"
)

# ──────────── Регистрация команд для подсказки `/` ────────────
async def on_startup(app):
    await app.bot.set_my_commands([
        BotCommand("start",      "Показать информацию о боте"),
        BotCommand("help",       "Список команд"),
        BotCommand("game",       "Начать сбор (админ)"),
        BotCommand("game_start", "Запустить турнир (админ)"),
        BotCommand("dice",       "Бросить кубик"),
    ])

# ──────────── Хендлеры ────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)
# ──────────── Хендлер для /help ────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Текст с HTML-разметкой
        caption = (
            "<b>Привет, я бот-рандомайзер</b>\n"
            "cозданный для розыгрышей и интерактивов!\n\n"
            "✨ Хочешь испытать удачу? Этот бот для тебя! 🎯\n"
            "С помощью наших универсальных инструментов ты сможешь организовать или поучаствовать в любом розыгрыше или интерактиве👾 \n\n"
            "<b>Пиши /start и добавляй бота в свои чаты</b> 💥\n\n"
            " Создано для <a href='https://t.me/NookiqqOnTon'>@NookiqqOnTon</a>\n"
            " При поддержке <a href='https://t.me/rapuzan'>@rapuzan</a>")
        
        # Создание inline-кнопок
        keyboard = [
            [
                InlineKeyboardButton("👤 Dev.", url="https://t.me/rapuzan"),
                InlineKeyboardButton("⚡️ The Best Community", url="https://t.me/nookiqqonton")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправка фото с подписью
        try:
            with open('noki_rapu.jpg', 'rb') as photo:
                await update.effective_chat.send_photo(
                    photo=photo,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
        except FileNotFoundError:
            # Если фото не найдено, отправляется только текст с кнопками
            await update.effective_chat.send_message(
                text="📷 " + caption,
                parse_mode='HTML',
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )

    except Exception as e:
        await update.effective_chat.send_message(f"⚠️ Ошибка: {str(e)}")

# ─────────────────────────────────────

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("Только админ может начать сбор.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("Участвую", callback_data="join_game")
    )
    await chat.send_message("🔔Набор на игру! Нажмите «Участвую»", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if tournament.add_player(q.message.chat.id, q.from_user):
        lst = tournament.list_players(q.message.chat.id)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    member = await context.bot.get_chat_member(chat_id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может запустить турнир.")
    try:
        byes, pairs_list, first_msg, kb = tournament.start_tournament(chat_id)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    for bye in byes:
        await context.bot.send_message(chat_id, f"🎉 {bye} сразу проходит в 2-й раунд (bye).")
    m = await context.bot.send_message(chat_id, "Сетки турнира:\n" + pairs_list)
    await context.bot.pin_chat_message(chat_id, m.message_id)
    await context.bot.send_message(chat_id, first_msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await tournament.roll_dice(update, context)
    if text:
        await update.message.reply_text(text)

# ──────────── main ────────────
def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(on_startup)
        .build()
    )
    global tournament
    tournament = TournamentManager(app.job_queue)

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("game",       game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_cb,   pattern="^ready_"))
    app.add_handler(CommandHandler("dice",       dice))

    app.run_polling()

if __name__ == "__main__":
    main()

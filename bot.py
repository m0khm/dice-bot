# bot.py
import logging
import os
from dotenv import load_dotenv
from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup
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
logger = logging.getLogger(__name__)

# ──────────── Загрузка конфигурации ────────────
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")
# Список разрешённых чатов через запятую
ALLOWED_CHATS = {int(x) for x in os.getenv("ALLOWED_CHATS", "").split(",") if x}
# Ваш ID для уведомлений (например, при обмене очков)
OWNER_IDS = [int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()]
# Путь к файлу базы очков
DB_PATH = os.getenv("DB_PATH", "scores.db")

# ──────────── Текст /start и /help ────────────
COMMANDS_TEXT = (
    "Привет! Я бот-рандомайзер. Доступные команды:\n"
    "/start       — 🤖 Список команд\n"
    "/game        — 👤 Начать сбор (админ)\n"
    "/game_start  — 🎮 Запустить турнир (админ)\n"
    "/dice        — 🎲 Бросить кубик\n"
    "/exchange    — 💱 Обменять очки (в личке)\n"
    "/id          — 📋 Показать ID чата"
)

# ──────────── Удаление вебхука ────────────
async def remove_webhook(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook removed, pending updates cleared.")

# ──────────── Регистрация команд для автодополнения ────────────
async def set_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start",      "Показать список команд"),
        BotCommand("help",       "Помощь"),
        BotCommand("game",       "Начать сбор (админ)"),
        BotCommand("game_start", "Запустить турнир (админ)"),
        BotCommand("dice",       "Бросить кубик"),
        BotCommand("exchange",   "Обменять очки"),
        BotCommand("id",         "Показать ID чата"),
    ])
    logger.info("Bot commands registered.")

# ──────────── Проверка разрешённых чатов ────────────
def is_allowed(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHATS

# ──────────── Handlers ────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(COMMANDS_TEXT)

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"Chat ID: `{chat.id}`", parse_mode="Markdown")

async def game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате не активен.")
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может начать сбор.")
    tournament.begin_signup(chat.id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Участвую", callback_data="join_game")]])
    await chat.send_message("🔔 Нажмите «Участвую» для регистрации", reply_markup=kb)

async def join_game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    if not is_allowed(chat_id):
        return
    if tournament.add_player(chat_id, q.from_user):
        lst = tournament.list_players(chat_id)
        await q.edit_message_text(f"Участвуют: {lst}", reply_markup=q.message.reply_markup)

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате не активен.")
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("⚠️ Только админ может запустить турнир.")
    try:
        byes, pairs_list, first_msg, kb = tournament.start_tournament(chat.id)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    for bye in byes:
        await context.bot.send_message(chat.id, f"🎉 {bye} получает bye")
    m = await context.bot.send_message(chat.id, "Сетки:\n" + pairs_list)
    await context.bot.pin_chat_message(chat.id, m.message_id)
    await context.bot.send_message(chat.id, first_msg, reply_markup=kb)

async def ready_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.callback_query.message.chat.id
    if not is_allowed(chat_id):
        return
    await tournament.confirm_ready(update, context)

async def dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private" and not is_allowed(chat.id):
        return await update.message.reply_text("❌ Бот в этом чате не активен.")
    text = await tournament.roll_dice(update, context)
    if text:
        await update.message.reply_text(text)

async def exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        return
    user = update.effective_user
    pts = tournament.get_points(user.username or user.full_name)
    if pts is None:
        return await update.message.reply_text("У вас пока нет очков.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Обменять", callback_data="exchange")]])
    await update.message.reply_text(f"Ваши очки: {pts}", reply_markup=kb)

async def exchange_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    username = user.username or user.full_name
    pts = tournament.get_points(username) or 0

    text = f"Пользователь {username} запрашивает обмен {pts} очков."
    for admin_id in OWNER_IDS:
        await context.bot.send_message(admin_id, text)

    await q.edit_message_text("✅ Ваш запрос отправлен администраторам.")


# ──────────── main ────────────
def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(remove_webhook)
        .post_init(set_commands)
        .build()
    )

    global tournament
    tournament = TournamentManager(
        job_queue=app.job_queue,
        allowed_chats=ALLOWED_CHATS,
        db_path=DB_PATH,
        owner_id=OWNER_ID
    )

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_command))
    app.add_handler(CommandHandler("id",         show_id))
    app.add_handler(CommandHandler("game",       game))
    app.add_handler(CallbackQueryHandler(join_game_cb, pattern="^join_game$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_cb,   pattern="^ready_"))
    app.add_handler(CommandHandler("dice",       dice))
    app.add_handler(CommandHandler("exchange",   exchange))
    app.add_handler(CallbackQueryHandler(exchange_cb, pattern="^exchange$"))

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()

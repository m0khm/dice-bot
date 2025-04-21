
from __future__ import annotations
import logging, random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from .game import Tournament, PairState, JOIN_TIMEOUT

logger = logging.getLogger(__name__)

def get_tournament(chat_data) -> Tournament:
    if "tournament" not in chat_data:
        chat_data["tournament"] = Tournament()
    return chat_data["tournament"]

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    tournament = get_tournament(context.chat_data)
    tournament.players.clear()
    tournament.winners.clear()
    tournament.bracket.clear()
    keyboard = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton("✅ Я играю", callback_data="join")
    )
    await update.effective_message.reply_text(
        "🎮 Турнир начинается! Нажмите кнопку, чтобы участвовать. "
        "Админ запускает /game_start, когда все соберутся.",
        reply_markup=keyboard,
    )

async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    tournament = get_tournament(context.chat_data)
    if user.id not in tournament.players:
        tournament.players.append(user.id)
        await query.message.reply_text(f"➕ {user.mention_html()} присоединился!", parse_mode="HTML")

async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tournament = get_tournament(context.chat_data)
    if len(tournament.players) < 2:
        await update.effective_message.reply_text("Недостаточно игроков.")
        return
    tournament.make_bracket()
    await announce_next_pair(update, context)

async def announce_next_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tournament = get_tournament(context.chat_data)
    if not tournament.bracket:
        # турнир закончен
        winners = tournament.winners
        if not winners:
            await update.effective_message.reply_text("Нет победителей 🤷‍♂️")
            return
        names = []
        for uid in winners:
            try:
                user = await context.bot.get_chat_member(update.effective_chat.id, uid)
                names.append(user.user.mention_html())
            except Exception:
                names.append(str(uid))
        await update.effective_message.reply_html("🏆 Победители: " + ", ".join(names))
        return
    pair = tournament.bracket[0]
    buttons = [
        [InlineKeyboardButton("✅ Готов", callback_data="ready")]
    ]
    u1 = await context.bot.get_chat_member(update.effective_chat.id, pair.players[0])
    u2 = await context.bot.get_chat_member(update.effective_chat.id, pair.players[1])
    pair.current_turn = None
    pair.ready.clear()
    pair.scores = {pair.players[0]: 0, pair.players[1]: 0}
    msg = await update.effective_message.reply_html(
        f"🎲 Пара: {u1.user.mention_html()} vs {u2.user.mention_html()}
"
        "Нажмите «Готов» за 60 с.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    context.job_queue.run_once(timeout_ready, JOIN_TIMEOUT, data={
        "chat_id": update.effective_chat.id,
        "message_id": msg.message_id,
    }, name=str(msg.message_id))

async def ready_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tournament = get_tournament(context.chat_data)
    if not tournament.bracket:
        return
    pair = tournament.bracket[0]
    user_id = query.from_user.id
    if user_id not in pair.players:
        return
    pair.ready.add(user_id)
    await query.message.reply_text(f"✔️ {query.from_user.mention_html()} готов!", parse_mode="HTML")
    if len(pair.ready) == 2:
        # обе готовы раньше таймаута
        await start_duel(update, context)

async def timeout_ready(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    tournament = get_tournament(context.chat_data)
    if not tournament.bracket:
        return
    pair = tournament.bracket[0]
    winners = list(pair.ready)
    if len(winners) == 1:
        winner = winners[0]
        tournament.winners.append(winner)
        await context.bot.send_message(chat_id, f"⏰ Таймаут. {winner} проходит дальше.")
    elif len(winners) == 0:
        await context.bot.send_message(chat_id, "⏰ Никто не готов. Оба выбывают.")
    tournament.bracket.popleft()
    await announce_next_pair(await context.bot.get_chat(chat_id), context)

async def start_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = get_tournament(context.chat_data).bracket[0]
    pair.current_turn = pair.players[0]    # кто первый
    await update.effective_message.reply_text("Первый игрок, бросай /dice!")

async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tournament = get_tournament(context.chat_data)
    if not tournament.bracket:
        return
    pair = tournament.bracket[0]
    if user_id != pair.current_turn:
        return
    roll = random.randint(1, 6)
    await update.effective_message.reply_text(f"🎲 {update.effective_user.first_name} бросил {roll}")
    opponent = [uid for uid in pair.players if uid != user_id][0]
    pair.current_turn = opponent
    # записываем
    pair.scores[user_id] = pair.scores.get(user_id, 0) + roll
    # simple best of 3 by wins per round
    if pair.scores[user_id] >= 2:
        tournament.winners.append(user_id)
        await update.effective_message.reply_text(f"🏅 {update.effective_user.mention_html()} победил пару!",
                                                  parse_mode="HTML")
        tournament.bracket.popleft()
        await announce_next_pair(update, context)

def setup(app):
    app.add_handler(CommandHandler("game", start_game))
    app.add_handler(CallbackQueryHandler(join_callback, pattern="^join$"))
    app.add_handler(CommandHandler("game_start", game_start))
    app.add_handler(CallbackQueryHandler(ready_callback, pattern="^ready$"))
    app.add_handler(CommandHandler("dice", dice_command))

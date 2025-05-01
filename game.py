# game.py
import random
import time
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telegram.ext import CallbackContext, ContextTypes

class TournamentManager:
    def __init__(self, job_queue):
        self.job_queue = job_queue
        self.chats = {}

    # ───────── signup ─────────
    def begin_signup(self, chat_id):
        self.chats[chat_id] = {
            "players": [], "stage": "signup",
            "next_round": [], "pairs": [],
            "current_pair_idx": 0, "round_pairs_count": 0,
            "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {},
            "semifinal_losers": [],
        }

    def add_player(self, chat_id, user):
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        name = user.username or user.full_name
        if name in data["players"]:
            return False
        data["players"].append(name)
        return True

    def list_players(self, chat_id):
        return ", ".join(self.chats[chat_id]["players"])

    # ───────── формируем пары ─────────
    def start_tournament(self, chat_id):
        data = self.chats.get(chat_id)
        players = data and data["players"][:]
        if not players or len(players) < 2:
            raise ValueError("Нужно как минимум 2 игрока.")
        random.shuffle(players)
        data["next_round"] = []

        byes = []
        if len(players) % 2 == 1:
            bye = players.pop(random.randrange(len(players)))
            byes.append(bye)
            data["next_round"].append(bye)

        pairs = [(players[i], players[i+1]) for i in range(0, len(players), 2)]
        data.update({
            "stage": "round", "pairs": pairs,
            "current_pair_idx": 0, "round_pairs_count": len(pairs),
            "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {},
        })

        pairs_list = "\n".join(
            f"Пара {i+1}: {a} vs {b}" for i, (a, b) in enumerate(pairs)
        )
        first_msg = f"Пара 1: {pairs[0][0]} vs {pairs[0][1]}\nНажмите «Готов?»"
        kb = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton("Готов?", callback_data="ready_0")
        )
        return byes, pairs_list, first_msg, kb

    # ───────── кнопка «Готов?» ─────────
    async def confirm_ready(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()

        chat_id = q.message.chat.id
        idx     = int(q.data.split("_")[1])
        name    = q.from_user.username or q.from_user.full_name
        data    = self.chats[chat_id]

        pair = data["pairs"][idx]
        if name not in pair:
            return await q.answer("Вы не в этой паре.", show_alert=True)

        lst = data.setdefault("ready", {}).setdefault(idx, [])
        if name in lst:
            return

        lst.append(name)
        now = time.time()

        # Первый клик — запускаем таймаут-джоб
        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            job = self.job_queue.run_once(
                self._ready_timeout,
                60,             # через 60 секунд
                chat_id=chat_id,
                data={"idx": idx},
                name=f"ready_timeout_{chat_id}_{idx}"
            )
            data["ready_jobs"][idx] = job
            await context.bot.send_message(
                chat_id,
                f"✅ {name} готов! Ждём второго игрока до 60 сек."
            )

        # Второй клик — оба готовы
        else:
            first_ts = data["first_ready_time"].get(idx, 0)
            if now - first_ts <= 60:
                job = data["ready_jobs"].pop(idx, None)
                if job:
                    job.schedule_removal()
                a, b = pair
                data["round_wins"][idx] = {a: 0, b: 0}
                first, second = random.sample(pair, 2)
                data["turn_order"][idx] = (first, second)
                await context.bot.send_message(
                    chat_id,
                    f"🎲 Оба готовы! {first} ходит первым. Используйте /dice"
                )

    # ───────── таймаут 60 с ─────────
    async def _ready_timeout(self, context: CallbackContext):
        job        = context.job
        chat_id    = job.chat_id
        idx        = job.data["idx"]
        data       = self.chats[chat_id]
        confirmed  = data.get("ready", {}).get(idx, [])
        pair       = data["pairs"][idx]

        # если оба подтвердили — уже ушли дальше
        if len(confirmed) >= 2:
            return

        # один подтвердил
        if len(confirmed) == 1:
            winner = confirmed[0]
            loser  = next(p for p in pair if p != winner)
            data["next_round"].append(winner)
            await context.bot.send_message(
                chat_id,
                f"⏰ Время вышло! ✅ {winner} прошёл дальше, а {loser} не подтвердил готовность."
            )
        # никто не подтвердил
        else:
            a, b = pair
            await context.bot.send_message(
                chat_id,
                f"⏰ Никто не подтвердил готовность — оба выбывают: {a}, {b}."
            )

        # переходим к следующей паре
        await self._proceed_next(chat_id, context.bot)

    # … остальной код (_proceed_next, roll_dice и т.д.) …

# game.py

import random
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, ContextTypes

class TournamentManager:
    def __init__(self, job_queue):
        self.job_queue = job_queue
        self.chats = {}

    def begin_signup(self, chat_id):
        self.chats[chat_id] = {
            "players": [],
            "stage": "signup",
            "next_round": [],
            "pairs": [],
            "current_pair_idx": 0,
            "round_pairs_count": 0,
            "ready": {},
            "first_ready_time": {},
            "ready_jobs": {},
            "round_wins": {},
            "round_rolls": {},
            "turn_order": {},
            "semifinal_losers": [],
        }

    def add_player(self, chat_id, user):
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        uname = user.username or user.full_name
        if uname in data["players"]:
            return False
        data["players"].append(uname)
        return True

    def list_players(self, chat_id):
        return ", ".join(self.chats[chat_id]["players"])

    def start_tournament(self, chat_id):
        data = self.chats.get(chat_id)
        players = data and data["players"].copy()
        if not players or len(players) < 2:
            raise ValueError("Нужно как минимум 2 игрока.")
        random.shuffle(players)
        data["next_round"] = []

        # bye for odd count
        byes = []
        if len(players) % 2 == 1:
            bye = players.pop(random.randrange(len(players)))
            byes.append(bye)
            data["next_round"].append(bye)

        # make pairs
        pairs = [(players[i], players[i+1]) for i in range(0, len(players), 2)]
        data.update({
            "stage": "round",
            "pairs": pairs,
            "current_pair_idx": 0,
            "round_pairs_count": len(pairs),
            "ready": {},
            "first_ready_time": {},
            "ready_jobs": {},
            "round_wins": {},
            "round_rolls": {},
            "turn_order": {},
        })

        # first pair
        p1, p2 = pairs[0]
        kb = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton("Готов?", callback_data="ready_0")
        )
        msg = f"Раунд 1 — пара 1: {p1} vs {p2}\nНажмите «Готов?», чтобы подтвердить."
        return byes, msg, kb

    async def confirm_ready(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        chat_id = q.message.chat.id
        idx = int(q.data.split("_")[1])
        uname = q.from_user.username or q.from_user.full_name
        data = self.chats[chat_id]

        lst = data.setdefault("ready", {}).setdefault(idx, [])
        if uname in lst:
            return
        lst.append(uname)
        now = time.time()

        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            job = self.job_queue.run_once(
                self._ready_timeout,
                when=60,
                chat_id=chat_id,
                data={"idx": idx}
            )
            data["ready_jobs"][idx] = job
            await context.bot.send_message(chat_id, f"{uname} готов! Ждём второго…")
        else:
            first_ts = data["first_ready_time"].get(idx, 0)
            if now - first_ts <= 60:
                job = data["ready_jobs"].pop(idx, None)
                if job:
                    job.schedule_removal()
                p1, p2 = data["pairs"][idx]
                data["round_wins"][idx] = {p1: 0, p2: 0}
                first, second = random.sample((p1, p2), 2)
                data["turn_order"][idx] = (first, second)
                await context.bot.send_message(
                    chat_id, f"{first} ходит первым! Используйте /dice"
                )

    def _ready_timeout(self, context: CallbackContext):
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        data = self.chats[chat_id]
        confirmed = data.get("ready", {}).get(idx, [])

        if len(confirmed) >= 2:
            return

        if len(confirmed) == 1:
            winner = confirmed[0]
            loser = next(p for p in data["pairs"][idx] if p != winner)
            data["next_round"].append(winner)
            context.bot.send_message(
                chat_id,
                f"⏰ Время вышло! {winner} проходит дальше, {loser} не нажал «Готов?»."
            )
        else:
            p1, p2 = data["pairs"][idx]
            context.bot.send_message(
                chat_id,
                f"⏰ Никто не подтвердил — оба выбывают: {p1}, {p2}."
            )

        self._proceed_next_match(chat_id, context.bot)

    def _proceed_next_match(self, chat_id, bot):
        data = self.chats[chat_id]
        data["current_pair_idx"] += 1
        idx = data["current_pair_idx"]
        pairs = data["pairs"]

        if idx < len(pairs):
            p1, p2 = pairs[idx]
            kb = InlineKeyboardMarkup.from_button(
                InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")
            )
            bot.send_message(
                chat_id,
                f"Пара {idx+1}: {p1} vs {p2}\nНажмите «Готов?» для старта.",
                reply_markup=kb
            )
            return

        winners = data["next_round"]
        if data["round_pairs_count"] == 2:
            for i, (a, b) in enumerate(data["pairs"]):
                w = data["round_wins"].get(i, {})
                if w.get(a,0) != w.get(b,0):
                    loser = a if w[a] < w[b] else b
                    data["semifinal_losers"].append(loser)

        if len(winners) > 1:
            data["players"] = winners.copy()
            byes, msg, kb = self.start_tournament(chat_id)
            for bye in byes:
                bot.send_message(chat_id, f"🎉 {bye} получает bye.")
            bot.send_message(chat_id, text=msg, reply_markup=kb)
            return

        champ = winners[0]
        runner = None
        wins = data["round_wins"].get(0, {})
        if wins:
            a, b = data["pairs"][0]
            runner = a if wins.get(a,0) < wins.get(b,0) else b
        thirds = data["semifinal_losers"]

        text = f"🏆 Победитель: {champ}\n"
        if runner:
            text += f"🥈 Второе место: {runner}\n"
        if len(thirds) >= 2:
            text += f"🥉 Третьи места: {thirds[0]}, {thirds[1]}\n"

        bot.send_message(chat_id, text)
        data["stage"] = "finished"

    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        data = self.chats[chat_id]

        if data["stage"] != "round":
            return "Турнир ещё не идёт."

        idx = data["current_pair_idx"]
        if idx >= len(data["pairs"]):
            return "Нет активной пары."

        uname = user.username or user.full_name
        p1, p2 = data["pairs"][idx]
        if uname not in (p1, p2):
            return "Вы не участвуете в этой паре."

        wins = data["round_wins"].setdefault(idx, {p1: 0, p2: 0})
        rolls = data["round_rolls"].setdefault(idx, {})
        first, second = data["turn_order"].get(idx, (p1, p2))

        if not rolls:
            turn = first
        elif len(rolls) == 1:
            turn = second
        else:
            turn = None

        if uname != turn:
            return "Сейчас не ваш ход."

        val = random.randint(1, 6)
        rolls[uname] = val

        if len(rolls) < 2:
            next_turn = second if uname == first else first
            return f"{uname} бросил {val}. Ход {next_turn}."
        else:
            r1, r2 = rolls[p1], rolls[p2]
            if r1 == r2:
                data["round_rolls"][idx] = {}
                return f"Ничья {r1}–{r2}! Переброс, {first} снова первым."
            winner = p1 if r1 > r2 else p2
            wins[winner] += 1
            data["round_rolls"][idx] = {}

            if wins[winner] >= 2:
                await update.effective_chat.send_message(f"Победитель пары: {winner}")
                data["next_round"].append(winner)
                self._proceed_next_match(chat_id, context.bot)
                return ""
            else:
                data["turn_order"][idx] = (first, second)
                return f"Счёт {wins[p1]}–{wins[p2]}. {first} ходит первым."

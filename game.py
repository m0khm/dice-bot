import random
import time
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    Message,
)
from telegram.ext import CallbackContext, ContextTypes

class TournamentManager:
    def __init__(self, job_queue):
        self.job_queue = job_queue        # для таймаутов «Готов?»
        self.chats = {}                   # состояние каждого чата

    # ───────── signup ─────────
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

        # bye при нечётном
        byes = []
        if len(players) % 2 == 1:
            bye = players.pop(random.randrange(len(players)))
            byes.append(bye)
            data["next_round"].append(bye)

        pairs = [(players[i], players[i+1]) for i in range(0, len(players), 2)]
        data.update({
            "stage": "round",
            "pairs": pairs,
            "current_pair_idx": 0,
            "round_pairs_count": len(pairs),
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
        idx = int(q.data.split("_")[1])
        name = q.from_user.username or q.from_user.full_name
        data = self.chats[chat_id]

        lst = data.setdefault("ready", {}).setdefault(idx, [])
        if name in lst:
            return
        lst.append(name)
        now = time.time()

        # Первый клик → старт таймаута
        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            job = self.job_queue.run_once(
                self._ready_timeout, when=60, chat_id=chat_id, data={"idx": idx}
            )
            data["ready_jobs"][idx] = job
            await context.bot.send_message(chat_id, f"{name} готов! Ждём второго…")
        else:
            # Второй клик до 60 с → отменяем таймаут и стартуем матч
            first_ts = data["first_ready_time"].get(idx, 0)
            if now - first_ts <= 60:
                job = data["ready_jobs"].pop(idx, None)
                if job:
                    job.schedule_removal()
                a, b = data["pairs"][idx]
                data["round_wins"][idx] = {a: 0, b: 0}
                first, second = random.sample((a, b), 2)
                data["turn_order"][idx] = (first, second)
                await context.bot.send_message(
                    chat_id, f"{first} ходит первым! Используйте /dice"
                )

    # ───────── таймаут 60 с ─────────
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
            loser  = next(p for p in data["pairs"][idx] if p != winner)
            data["next_round"].append(winner)
            context.bot.send_message(
                chat_id, f"⏰ Время вышло! {winner} проходит дальше, {loser} не нажал «Готов?».")
        else:
            a, b = data["pairs"][idx]
            context.bot.send_message(
                chat_id, f"⏰ Никто не подтвердил — оба выбывают: {a}, {b}."
            )

        self._proceed_next(chat_id, context.bot)

    # ───────── переход к следующей паре / раунду / финалу ─────────
    def _proceed_next(self, chat_id, bot):
        data = self.chats[chat_id]
        data["current_pair_idx"] += 1
        idx = data["current_pair_idx"]
        pairs = data["pairs"]

        # ещё есть пары
        if idx < len(pairs):
            a, b = pairs[idx]
            kb = InlineKeyboardMarkup.from_button(
                InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")
            )
            bot.send_message(
                chat_id,
                f"Следующая пара {idx+1}: {a} vs {b}\nНажмите «Готов?»",
                reply_markup=kb
            )
            return

        # формируем next_round
        winners = data["next_round"]
        # фиксируем лузеров полуфинала для 🥉
        if data["round_pairs_count"] == 2:
            for i, (x, y) in enumerate(data["pairs"]):
                w = data["round_wins"].get(i, {})
                if w.get(x, 0) != w.get(y, 0):
                    loser = x if w[x] < w[y] else y
                    data["semifinal_losers"].append(loser)

        # новый раунд
        if len(winners) > 1:
            data["players"] = winners.copy()
            byes, pairs_list, first_msg, kb = self.start_tournament(chat_id)

            # публикуем новую сетку и закрепляем
            m: Message = bot.send_message(chat_id, "Новая сетка раунда:\n" + pairs_list)
            bot.pin_chat_message(chat_id, m.message_id)

            for bye in byes:
                bot.send_message(chat_id, f"🎉 {bye} получает bye.")

            bot.send_message(chat_id, first_msg, reply_markup=kb)
            return

        # финал
        champ  = winners[0]
        runner = None
        w      = data["round_wins"].get(0, {})
        if w:
            p, q = data["pairs"][0]
            runner = p if w.get(p, 0) < w.get(q, 0) else q
        thirds = data["semifinal_losers"]

        text = f"🏆 Победитель: {champ}\n"
        if runner:
            text += f"🥈 Второе: {runner}\n"
        if len(thirds) >= 2:
            text += f"🥉 Третьи: {thirds[0]}, {thirds[1]}\n"

        bot.send_message(chat_id, text)
        data["stage"] = "finished"

    # ───────── /dice ─────────
    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        name = update.effective_user.username or update.effective_user.full_name
        data = self.chats[chat_id]

        if data["stage"] != "round":
            return "Турнир ещё не идёт."

        idx = data["current_pair_idx"]
        if idx >= len(data["pairs"]):
            return "Нет активной пары."

        a, b = data["pairs"][idx]
        if name not in (a, b):
            return "Вы не участвуете в этой паре."

        wins  = data["round_wins"].setdefault(idx, {a: 0, b: 0})
        rolls = data["round_rolls"].setdefault(idx, {})
        first, second = data["turn_order"].get(idx, (a, b))

        # чей ход?
        turn = first if not rolls else second if len(rolls) == 1 else None
        if name != turn:
            return "Сейчас не ваш ход."

        val = random.randint(1, 6)
        rolls[name] = val
        # публикуем число
        await update.effective_chat.send_message(f"{name} бросил 🎲 {val}.")

        if len(rolls) < 2:
            nxt = second if name == first else first
            return f"Ход {nxt}."
        else:
            r1, r2 = rolls[a], rolls[b]
            if r1 == r2:
                data["round_rolls"][idx] = {}
                return f"Ничья {r1}–{r2}! Переброс, {first} снова первым."

            winner = a if r1 > r2 else b
            wins[winner] += 1
            data["round_rolls"][idx] = {}

            if wins[winner] >= 2:
                await update.effective_chat.send_message(f"Победитель пары: {winner}")
                data["next_round"].append(winner)
                self._proceed_next(chat_id, context.bot)
                return ""
            else:
                data["turn_order"][idx] = (first, second)
                return f"Счёт {wins[a]}–{wins[b]}. {first} ходит первым."

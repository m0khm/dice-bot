# game.py
import sqlite3
import random
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import CallbackContext, ContextTypes

class TournamentManager:
    FIRST_POINTS = 50
    SECOND_POINTS = 25
    THIRD_POINTS = 15

    def __init__(self, job_queue, db_path: str, owner_ids=None, allowed_chats=None):
        self.job_queue = job_queue
        self.chats = {}
        self.allowed_chats = allowed_chats or set()
        self.owner_ids = owner_ids or []

        # Инициализация БД для хранения очков
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                username TEXT PRIMARY KEY,
                points   INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def _format_username(self, name: str) -> str:
        if not name:
            return ""
        return name if name.startswith("@") else f"@{name}"

    # ───────── Методы работы с очками ─────────

    def _add_points(self, username: str, pts: int):
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        if row:
            new = row[0] + pts
            cur.execute("UPDATE scores SET points=? WHERE username=?", (new, username))
        else:
            new = pts
            cur.execute("INSERT INTO scores(username, points) VALUES(?, ?)", (username, pts))
        self.conn.commit()

    def get_points(self, username: str) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        return row[0] if row else 0

    def exchange_points(self, username: str) -> int:
        """
        Списывает все имеющиеся очки пользователя и возвращает
        количество списанных очков.
        """
        pts = self.get_points(username)
        if pts > 0:
            cur = self.conn.cursor()
            cur.execute("UPDATE scores SET points=0 WHERE username=?", (username,))
            self.conn.commit()
        return pts

    # ───────── signup ─────────
    def begin_signup(self, chat_id: int):
        self.chats[chat_id] = {
            "players": [], "stage": "signup",
            "next_round": [], "pairs": [],
            "current_pair_idx": 0, "round_pairs_count": 0,
            "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {},
            "semifinal_losers": [], "pair_timers": {},
            "finished_pairs": set(),
        }

    def add_player(self, chat_id: int, user) -> bool:
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        name = user.username or user.full_name
        if name in data["players"]:
            return False
        data["players"].append(name)
        return True

    def list_players(self, chat_id: int) -> str:
        data = self.chats.get(chat_id, {})
        return ", ".join(self._format_username(n) for n in data.get("players", []))

    # ───────── старт турнира ─────────
    def start_tournament(self, chat_id: int):
        data = self.chats.get(chat_id)
        players = data and data["players"][:]
        if not players or len(players) < 2:
            raise ValueError("Нужно минимум 2 игрока.")
        random.shuffle(players)
        data["next_round"] = []

        byes = []
        if len(players) % 2 == 1:
            b = players.pop(random.randrange(len(players)))
            byes.append(b)
            data["next_round"].append(b)

        pairs = [(players[i], players[i+1]) for i in range(0, len(players), 2)]
        data.update({
            "stage":"round","pairs":pairs,
            "current_pair_idx":0,
            "round_pairs_count":len(pairs),
            "ready":{}, "first_ready_time":{},
            "ready_jobs":{}, "round_wins":{},
            "round_rolls":{}, "turn_order":{},
            "finished_pairs":set(),
        })

        pairs_list = "\n".join(
            f"Пара {i+1}: {self._format_username(a)} vs {self._format_username(b)}"
            for i,(a,b) in enumerate(pairs)
        )
        fp = pairs[0]
        first_msg = (
            f"Пара 1: {self._format_username(fp[0])} vs "
            f"{self._format_username(fp[1])}\nНажмите «Готов?»"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Готов?", callback_data="ready_0")]])

        # Планируем общий таймер 120 сек
        if self.job_queue:
            j = self.job_queue.run_once(
                self._pair_timeout, 120,
                chat_id=chat_id,
                data={"idx":0},
                name=f"pair_timeout_{chat_id}_0"
            )
            data["pair_timers"][0] = j

        return byes, pairs_list, first_msg, kb

    # ───────── подтверждение готовности ─────────
    async def confirm_ready(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        chat_id = q.message.chat.id
        idx = int(q.data.split("_")[1])
        name = q.from_user.username or q.from_user.full_name
        data = self.chats[chat_id]
        pair = data["pairs"][idx]

        if name not in pair:
            return await q.answer("❌ Вы не в этой паре.", show_alert=True)

        lst = data["ready"].setdefault(idx, [])
        if name in lst:
            return
        lst.append(name)
        now = time.time()

        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            if self.job_queue:
                j = self.job_queue.run_once(
                    self._ready_timeout, 60,
                    chat_id=chat_id,
                    data={"idx":idx},
                    name=f"ready_timeout_{chat_id}_{idx}"
                )
                data["ready_jobs"][idx] = j
                self._reset_pair_timer(chat_id, idx, 60)
            await context.bot.send_message(
                chat_id,
                f"✅ {self._format_username(name)} готов! Ждём второго…"
            )
        else:
            ft = data["first_ready_time"].get(idx, 0)
            if now - ft <= 60:
                old = data["ready_jobs"].pop(idx, None)
                if old:
                    old.schedule_removal()
                a, b = pair
                data["round_wins"][idx] = {a:0, b:0}
                first, second = random.sample(pair, 2)
                data["turn_order"][idx] = (first, second)
                await context.bot.send_message(
                    chat_id,
                    f"🎲 Оба готовы! {self._format_username(first)} ходит первым."
                )

    # ───────── таймаут готовности ─────────
    async def _ready_timeout(self, ctx: CallbackContext):
        job = ctx.job
        cid = job.chat_id
        idx = job.data["idx"]
        d = self.chats[cid]
        confirmed = d["ready"].get(idx, [])
        pair = d["pairs"][idx]

        if len(confirmed) >= 2:
            return

        if len(confirmed) == 1:
            w = confirmed[0]
            l = pair[0] if pair[1] == w else pair[1]
            d["next_round"].append(w)
            d["finished_pairs"].add(idx)
            await ctx.bot.send_message(
                cid,
                f"⏰ Время вышло! ✅ {self._format_username(w)} проходит дальше."
            )
        else:
            a, b = pair
            await ctx.bot.send_message(
                cid,
                f"⏰ Никто не подтвердил — оба выбывают: "
                f"{self._format_username(a)}, {self._format_username(b)}."
            )
        await self._proceed_next(cid, ctx.bot)

    # ───────── сброс таймера пары ─────────
    def _reset_pair_timer(self, cid: int, idx: int, when: int):
        d = self.chats[cid]
        old = d["pair_timers"].pop(idx, None)
        if old:
            old.schedule_removal()
        if self.job_queue:
            j = self.job_queue.run_once(
                self._pair_timeout, when,
                chat_id=cid,
                data={"idx":idx},
                name=f"pair_timeout_{cid}_{idx}"
            )
            d["pair_timers"][idx] = j

    # ───────── таймаут пары ─────────
    async def _pair_timeout(self, ctx: CallbackContext):
        job = ctx.job
        cid = job.chat_id
        idx = job.data["idx"]
        d = self.chats[cid]
        if idx in d["finished_pairs"]:
            return
        if not d["ready"].get(idx):
            a, b = d["pairs"][idx]
            await ctx.bot.send_message(
                cid,
                f"⏰ Пара {self._format_username(a)} vs {self._format_username(b)} "
                "не подтвердила готовность — оба выбывают."
            )
            d["finished_pairs"].add(idx)
        await self._proceed_next(cid, ctx.bot)

    # ───────── переход к следующей паре или финалу ─────────
    async def _proceed_next(self, chat_id: int, bot):
        d = self.chats[chat_id]
        d["current_pair_idx"] += 1
        idx = d["current_pair_idx"]
        pairs = d["pairs"]

        if idx < len(pairs):
            a, b = pairs[idx]
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")]]
            )
            await bot.send_message(
                chat_id,
                f"Пара {idx+1}: {self._format_username(a)} vs {self._format_username(b)}",
                reply_markup=kb
            )
            return

        winners = d["next_round"]
        if not winners:
            await bot.send_message(
                chat_id,
                "⚠️ Никто не проявил активность. Турнир завершён без победителя."
            )
            self.chats.pop(chat_id, None)
            return

        # Начисляем очки за 1-е, 2-е и 3-е места
        self._add_points(winners[0], self.FIRST_POINTS)
        if len(winners) > 1:
            self._add_points(winners[1], self.SECOND_POINTS)
        for t in d["semifinal_losers"][:2]:
            self._add_points(t, self.THIRD_POINTS)

        # Объявляем результаты
        text = f"🏁 Турнир завершён.\n"
        text += f"🥇 {self._format_username(winners[0])}: +{self.FIRST_POINTS} очков\n"
        if len(winners) > 1:
            text += f"🥈 {self._format_username(winners[1])}: +{self.SECOND_POINTS} очков\n"
        for t in d["semifinal_losers"][:2]:
            text += f"🥉 {self._format_username(t)}: +{self.THIRD_POINTS} очков\n"
        await bot.send_message(chat_id, text)
        d["stage"] = "finished"

    # ───────── бросок кубика ─────────
    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        name = update.effective_user.username or update.effective_user.full_name
        d = self.chats.get(chat_id, {})

        if d.get("stage") != "round":
            return "❗ Турнир ещё не идёт."

        idx = d["current_pair_idx"]
        if idx >= len(d["pairs"]):
            return "❗ Нет активной пары."

        a, b = d["pairs"][idx]
        if name not in (a, b):
            return "❌ Вы не участвуете в этой паре."

        wins = d["round_wins"].setdefault(idx, {a: 0, b: 0})
        rolls = d["round_rolls"].setdefault(idx, {})
        first, second = d["turn_order"].get(idx, (a, b))

        turn = first if not rolls else second if len(rolls) == 1 else None
        if name != turn:
            return "❌ Сейчас не ваш ход."

        val = random.randint(1, 6)
        rolls[name] = val
        await update.effective_chat.send_message(f"{self._format_username(name)} бросил 🎲 {val}.")

        if len(rolls) < 2:
            nxt = second if name == first else first
            return f"Ход {self._format_username(nxt)}."
        else:
            r1, r2 = rolls[a], rolls[b]
            if r1 == r2:
                d["round_rolls"][idx] = {}
                return f"Ничья {r1}–{r2}! Переброс, {self._format_username(first)} снова первым."

            winner = a if r1 > r2 else b
            wins[winner] += 1
            d["round_rolls"][idx] = {}

            if wins[winner] >= 2:
                await update.effective_chat.send_message(
                    f"🎉 Победитель пары: {self._format_username(winner)}"
                )
                d["next_round"].append(winner)
                d["finished_pairs"].add(idx)
                jt = d["pair_timers"].pop(idx, None)
                if jt:
                    jt.schedule_removal()
                await self._proceed_next(chat_id, context.bot)
                return ""
            else:
                d["turn_order"][idx] = (first, second)
                return f"Счёт {wins[a]}–{wins[b]}. {self._format_username(first)} ходит первым."

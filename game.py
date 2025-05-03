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

    def __init__(
        self,
        job_queue,
        allowed_chats=None,
        db_path: str = "scores.db",
        owner_ids=None
    ):
        self.job_queue = job_queue
        self.allowed_chats = set(allowed_chats or [])
        self.owner_ids    = list(owner_ids or [])
        self.chats = {}

        # Инициализируем SQLite
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        cur = self.conn.cursor()
        cur.execute("""
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

    # ─── работа с очками ─────────
    def _add_points(self, username: str, pts: int):
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        if row:
            new = row[0] + pts
            cur.execute("UPDATE scores SET points=? WHERE username=?", (new, username))
        else:
            new = pts
            cur.execute("INSERT INTO scores(username,points) VALUES(?,?)", (username, pts))
        self.conn.commit()

    def get_points(self, username: str) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        return row[0] if row else 0

    def exchange_points(self, username: str) -> int:
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
            "dice_jobs": {},            # таймеры ожидания броска
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

    # ───────── формируем пары ─────────
    def start_tournament(self, chat_id: int):
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
            "stage": "round",
            "pairs": pairs,
            "current_pair_idx": 0,
            "round_pairs_count": len(pairs),
            "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {},
            "finished_pairs": set(),
        })

        pairs_list = "\n".join(
            f"Пара {i+1}: {self._format_username(a)} vs {self._format_username(b)}"
            for i, (a, b) in enumerate(pairs)
        )

        first_pair = pairs[0]
        first_msg = (
            f"Пара 1: {self._format_username(first_pair[0])} vs "
            f"{self._format_username(first_pair[1])}\nНажмите «Готов?»"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Готов?", callback_data="ready_0")]])

        # общая неактивность пары 120 с
        if self.job_queue:
            job = self.job_queue.run_once(
                self._pair_timeout,
                when=120,
                chat_id=chat_id,
                data={"idx": 0},
                name=f"pair_timeout_{chat_id}_0"
            )
            data["pair_timers"][0] = job

        return byes, pairs_list, first_msg, kb

    # ───────── кнопка «Готов?» ─────────
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
                job = self.job_queue.run_once(
                    self._ready_timeout,
                    when=60,
                    chat_id=chat_id,
                    data={"idx": idx},
                    name=f"ready_timeout_{chat_id}_{idx}"
                )
                data["ready_jobs"][idx] = job
            await context.bot.send_message(
                chat_id,
                f"✅ {self._format_username(name)} готов! Ждём второго…"
            )
            # сброс общего таймера пары на 60 с
            if self.job_queue:
                self._reset_pair_timer(chat_id, idx, 60)

        else:
            first_ts = data["first_ready_time"].get(idx, 0)
            if now - first_ts <= 60:
                # оба готовы — отменяем таймер ожидания второго готова
                job = data["ready_jobs"].pop(idx, None)
                if job:
                    job.schedule_removal()
                a, b = pair
                data["round_wins"][idx] = {a: 0, b: 0}
                first, second = random.sample(pair, 2)
                data["turn_order"][idx] = (first, second)
                msg = f"🎲 Оба готовы! {self._format_username(first)} ходит первым. Используйте /dice"
                await context.bot.send_message(chat_id, msg)
                # планируем таймаут первого броска
                if self.job_queue:
                    dj = self.job_queue.run_once(
                        self._dice_timeout,
                        when=60,
                        chat_id=chat_id,
                        data={"idx": idx, "expected": first},
                        name=f"dice_timeout_{chat_id}_{idx}"
                    )
                    data["dice_jobs"][idx] = dj

    # ───────── таймаут готовности ─────────
    async def _ready_timeout(self, context: CallbackContext):
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        data = self.chats[chat_id]
        confirmed = data["ready"].get(idx, [])
        pair = data["pairs"][idx]

        if len(confirmed) >= 2:
            return

        if len(confirmed) == 1:
            winner = confirmed[0]
            loser = pair[0] if pair[1] == winner else pair[1]
            data["next_round"].append(winner)
            data["finished_pairs"].add(idx)
            await context.bot.send_message(
                chat_id,
                f"⏰ Время вышло! ✅ {self._format_username(winner)} проходит дальше."
            )
        else:
            a, b = pair
            await context.bot.send_message(
                chat_id,
                f"⏰ Никто не подтвердил — оба выбывают: {self._format_username(a)}, {self._format_username(b)}."
            )
        await self._proceed_next(chat_id, context.bot)

    # ───────── сброс таймера пары ─────────
    def _reset_pair_timer(self, chat_id: int, idx: int, when: int):
        data = self.chats[chat_id]
        old = data["pair_timers"].pop(idx, None)
        if old:
            old.schedule_removal()
        if self.job_queue:
            job = self.job_queue.run_once(
                self._pair_timeout,
                when=when,
                chat_id=chat_id,
                data={"idx": idx},
                name=f"pair_timeout_{chat_id}_{idx}"
            )
            data["pair_timers"][idx] = job

    # ───────── таймаут пары ─────────
    async def _pair_timeout(self, context: CallbackContext):
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        data = self.chats[chat_id]

        if idx in data["finished_pairs"]:
            return

        pair = data["pairs"][idx]
        confirmed = data["ready"].get(idx, [])
        if not confirmed:
            a, b = pair
            await context.bot.send_message(
                chat_id,
                f"⏰ Пара {self._format_username(a)} vs {self._format_username(b)} не подтвердила готовность — оба выбывают."
            )
            data["finished_pairs"].add(idx)

        await self._proceed_next(chat_id, context.bot)

    # ───────── таймаут броска ─────────
    async def _dice_timeout(self, context: CallbackContext):
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        expected = job.data["expected"]
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "round":
            return

        rolls = data["round_rolls"].get(idx, {})
        a, b = data["pairs"][idx]
        if expected not in rolls:
            # неактивный выбыл
            winner = a if expected == b else b
            data["next_round"].append(winner)
            data["finished_pairs"].add(idx)
            await context.bot.send_message(
                chat_id,
                f"⏰ {self._format_username(expected)} не бросил вовремя — {self._format_username(winner)} проходит дальше."
            )
            # отменяем оба таймера этой пары
            dj = data["dice_jobs"].pop(idx, None)
            if dj: dj.schedule_removal()
            pt = data["pair_timers"].pop(idx, None)
            if pt: pt.schedule_removal()
            await self._proceed_next(chat_id, context.bot)

    # ───────── переход к следующему шагу ─────────
    async def _proceed_next(self, chat_id: int, bot):
        data = self.chats[chat_id]
        data["current_pair_idx"] += 1
        idx = data["current_pair_idx"]
        pairs = data["pairs"]

        if idx < len(pairs):
            a, b = pairs[idx]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")]])
            await bot.send_message(
                chat_id,
                f"Следующая пара {idx+1}: {self._format_username(a)} vs {self._format_username(b)}\nНажмите «Готов?»",
                reply_markup=kb
            )
            return

        winners = data["next_round"]
        if not winners:
            await bot.send_message(chat_id, "⚠️ Никто не активен — турнир завершён.")
            self.chats.pop(chat_id, None)
            return

        # начисляем очки
        self._add_points(winners[0], self.FIRST_POINTS)
        if len(winners) > 1:
            self._add_points(winners[1], self.SECOND_POINTS)
        for t in data["semifinal_losers"][:2]:
            self._add_points(t, self.THIRD_POINTS)

        # объявляем финал и призёров...
        # (оставьте существующую логику вывода итогов)
        data["stage"] = "finished"

    # ───────── бросок кубика ─────────
    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        name = update.effective_user.username or update.effective_user.full_name
        data = self.chats.get(chat_id, {})

        if data.get("stage") != "round":
            return "❗ Турнир ещё не идёт."

        idx = data["current_pair_idx"]
        # отменяем таймер ожидания броска
        job = data["dice_jobs"].pop(idx, None)
        if job:
            job.schedule_removal()

        a, b = data["pairs"][idx]
        if name not in (a, b):
            return "❌ Вы не участвуете в этой паре."

        wins = data["round_wins"].setdefault(idx, {a: 0, b: 0})
        rolls = data["round_rolls"].setdefault(idx, {})
        first, second = data["turn_order"].get(idx, (a, b))

        turn = first if not rolls else second if len(rolls) == 1 else None
        if name != turn:
            return "❌ Сейчас не ваш ход."

        val = random.randint(1, 6)
        rolls[name] = val
        await update.effective_chat.send_message(f"{self._format_username(name)} бросил 🎲 {val}.")

        if len(rolls) < 2:
            nxt = second if name == first else first
            # планируем таймаут второго броска
            if self.job_queue:
                dj = self.job_queue.run_once(
                    self._dice_timeout,
                    when=60,
                    chat_id=chat_id,
                    data={"idx": idx, "expected": nxt},
                    name=f"dice_timeout_{chat_id}_{idx}"
                )
                data["dice_jobs"][idx] = dj
            return f"Ход {self._format_username(nxt)}."
        else:
            # ваше существующее сравнение и логика победы...
            # отмена pair_timer и переход далее
            pass

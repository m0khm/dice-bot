# game.py
import sqlite3
import random
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import CallbackContext, ContextTypes

class TournamentManager:
    FIRST_POINTS  = 50
    SECOND_POINTS = 25
    THIRD_POINTS  = 15

    def __init__(
        self,
        job_queue,
        allowed_chats=None,
        db_path: str = "scores.db",
        owner_ids=None
    ):
        self.job_queue      = job_queue
        self.allowed_chats  = set(allowed_chats or [])
        self.owner_ids      = list(owner_ids or [])
        self.chats          = {}

        # База для очков
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
        return name if name.startswith("@") else f"@{name}"

    def _add_points(self, username: str, pts: int):
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE scores SET points=? WHERE username=?", (row[0] + pts, username))
        else:
            cur.execute("INSERT INTO scores(username, points) VALUES(?, ?)", (username, pts))
        self.conn.commit()

    def get_points(self, username: str) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT points FROM scores WHERE username=?", (username,))
        row = cur.fetchone()
        return row[0] if row else 0

    def exchange_points(self, username: str) -> int:
        pts = self.get_points(username)
        if pts:
            cur = self.conn.cursor()
            cur.execute("UPDATE scores SET points=0 WHERE username=?", (username,))
            self.conn.commit()
        return pts

    # ───────── signup ─────────
    def begin_signup(self, chat_id: int) -> None:
        self.chats[chat_id] = {
            "players":           [],
            "stage":             "signup",
            "next_round":        [],
            "pairs":             [],
            "current_pair_idx":  0,
            "round_pairs_count": 0,
            "ready":             {},
            "first_ready_time":  {},
            "ready_jobs":        {},
            "round_wins":        {},
            "turn_order":        {},
            "semifinal_losers":  [],
            "pair_timers":       {},
            "dice_jobs":         {},
            "current_rolls":     {},   # <-- списки бросков
            "finished_pairs":    set(),
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
        data = self.chats[chat_id]
        players = data["players"][:]
        if len(players) < 2:
            raise ValueError("Нужно как минимум 2 игрока.")
        random.shuffle(players)
        data["next_round"] = []

        # bye, если нечётное
        byes = []
        if len(players) % 2:
            bye = players.pop(random.randrange(len(players)))
            byes.append(bye)
            data["next_round"].append(bye)

        pairs = [(players[i], players[i+1]) for i in range(0, len(players), 2)]
        data.update({
            "stage":             "round",
            "pairs":             pairs,
            "current_pair_idx":  0,
            "round_pairs_count": len(pairs),
            "ready":             {},
            "first_ready_time":  {},
            "ready_jobs":        {},
            "round_wins":        {},
            "turn_order":        {},
            "current_rolls":     {},  # обнуляем старые броски
            "finished_pairs":    set(),
        })

        # текст сетки
        pairs_list = "\n".join(
            f"Пара {i+1}: {self._format_username(a)} vs {self._format_username(b)}"
            for i,(a,b) in enumerate(pairs)
        )

        # первая пара
        a,b = pairs[0]
        first_msg = f"Пара 1: {self._format_username(a)} vs {self._format_username(b)}\nНажмите «Готов?»"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Готов?", callback_data="ready_0")]])

        # общий таймер пары: 120 сек
        if self.job_queue:
            jt = self.job_queue.run_once(
                self._pair_timeout, 120,
                chat_id=chat_id, data={"idx":0},
                name=f"pair_timeout_{chat_id}_0"
            )
            data["pair_timers"][0] = jt

        return byes, pairs_list, first_msg, kb

    # ───────── нажали «Готов?» ─────────
    async def confirm_ready(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()

        chat_id = q.message.chat.id
        idx     = int(q.data.split("_")[1])
        name    = q.from_user.username or q.from_user.full_name
        data    = self.chats[chat_id]
        pair    = data["pairs"][idx]

        if name not in pair:
            return await q.answer("❌ Вы не в этой паре.", show_alert=True)

        lst = data["ready"].setdefault(idx, [])
        if name in lst:
            return
        lst.append(name)
        now = time.time()

        # первый готов -> ждем второго 60 сек
        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            if self.job_queue:
                # таймаут второго «готов»
                rj = self.job_queue.run_once(
                    self._ready_timeout, 60,
                    chat_id=chat_id, data={"idx":idx},
                    name=f"ready_timeout_{chat_id}_{idx}"
                )
                data["ready_jobs"][idx] = rj
                # пересброс общего таймаута пары:
                pj = data["pair_timers"].pop(idx, None)
                if pj: pj.schedule_removal()
                pj2 = self.job_queue.run_once(
                    self._pair_timeout, 60,
                    chat_id=chat_id, data={"idx":idx},
                    name=f"pair_timeout_{chat_id}_{idx}"
                )
                data["pair_timers"][idx] = pj2

            await context.bot.send_message(
                chat_id,
                f"✅ {self._format_username(name)} готов! Ждём второго до 60 с."
            )

        # оба готовы
        else:
            ts0 = data["first_ready_time"].get(idx, 0)
            if now - ts0 <= 60:
                # отменяем оба таймера: второго «готов» и пары
                rj = data["ready_jobs"].pop(idx, None)
                if rj: rj.schedule_removal()
                pj = data["pair_timers"].pop(idx, None)
                if pj: pj.schedule_removal()

                a,b = pair
                data["round_wins"][idx] = {a:0, b:0}
                first, second = random.sample(pair, 2)
                data["turn_order"][idx] = (first, second)

                await context.bot.send_message(
                    chat_id,
                    f"🎲 Оба готовы! {self._format_username(first)} ходит первым. Используйте /dice"
                )

                # таймаут первого броска
                if self.job_queue:
                    dj = self.job_queue.run_once(
                        self._dice_timeout, 60,
                        chat_id=chat_id,
                        data={"idx":idx, "expected":first},
                        name=f"dice_timeout_{chat_id}_{idx}"
                    )
                    data["dice_jobs"][idx] = dj

    # ───────── таймаут второго «готов» ─────────
    async def _ready_timeout(self, context: CallbackContext):
        job     = context.job
        chat_id = job.chat_id
        idx     = job.data["idx"]
        data    = self.chats[chat_id]
        conf    = data["ready"].get(idx, [])
        pair    = data["pairs"][idx]

        if len(conf) >= 2:
            return

        if len(conf) == 1:
            winner = conf[0]
            data["next_round"].append(winner)
            data["finished_pairs"].add(idx)
            await context.bot.send_message(chat_id, f"⏰ Время вышло — {winner} прошёл дальше.")
        else:
            a,b = pair
            await context.bot.send_message(chat_id, f"⏰ Никто не подтвердил — оба выбывают: {a}, {b}.")

        await self._proceed_next(chat_id, context.bot)

    # ───────── общий таймаут пары ─────────
    async def _pair_timeout(self, context: CallbackContext):
        job     = context.job
        chat_id = job.chat_id
        idx     = job.data["idx"]
        data    = self.chats[chat_id]

        if idx in data["finished_pairs"]:
            return

        conf = data["ready"].get(idx, [])
        pair = data["pairs"][idx]
        if not conf:
            a,b = pair
            await context.bot.send_message(
                chat_id,
                f"⏰ Пара {self._format_username(a)} vs {self._format_username(b)} не подтвердила — оба выбывают."
            )
            data["finished_pairs"].add(idx)

        await self._proceed_next(chat_id, context.bot)

    # ───────── таймаут ожидания /dice ─────────
    async def _dice_timeout(self, context: CallbackContext):
        job     = context.job
        chat_id = job.chat_id
        idx     = job.data["idx"]
        exp     = job.data["expected"]
        data    = self.chats.get(chat_id)
        if not data or data["stage"] != "round":
            return

        rolls = data["current_rolls"].get(idx, [])
        a,b   = data["pairs"][idx]
        if exp not in [u for u,_ in rolls]:
            winner = b if exp == a else a
            data["next_round"].append(winner)
            data["finished_pairs"].add(idx)
            await context.bot.send_message(
                chat_id,
                f"⏰ {self._format_username(exp)} не бросил — {self._format_username(winner)} проходит дальше."
            )
            # чистим таймеры пары
            dj = data["dice_jobs"].pop(idx, None)
            if dj: dj.schedule_removal()
            pj = data["pair_timers"].pop(idx, None)
            if pj: pj.schedule_removal()
            await self._proceed_next(chat_id, context.bot)

    # ───────── переход к следующему шагу ─────────
    async def _proceed_next(self, chat_id: int, bot):
        data = self.chats[chat_id]
        data["current_pair_idx"] += 1
        idx  = data["current_pair_idx"]
        pairs = data["pairs"]

        if idx < len(pairs):
            a,b = pairs[idx]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")]])
            await bot.send_message(
                chat_id,
                f"Пара {idx+1}: {self._format_username(a)} vs {self._format_username(b)}\nНажмите «Готов?»",
                reply_markup=kb
            )
            return

        # ... а тут ваша существующая логика начисления очков и объявления финала ...

    # ───────── бросок кубика ─────────
    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        name    = update.effective_user.username or update.effective_user.full_name
        data    = self.chats.get(chat_id, {})

        if data.get("stage") != "round":
            return "❗ Турнир ещё не идёт."

        idx = data["current_pair_idx"]
        # отменяем таймаут этого броска
        dj = data["dice_jobs"].pop(idx, None)
        if dj:
            dj.schedule_removal()

        a,b = data["pairs"][idx]
        if name not in (a,b):
            return "❌ Вы не участвуете в этой паре."

        # работаем со списком текущих бросков
        rolls = data["current_rolls"].setdefault(idx, [])
        first, second = data["turn_order"].get(idx, (a, b))

        # определяем чью очередь
        if not rolls:
            turn = first
        elif len(rolls) == 1:
            turn = second
        else:
            return "❌ Ожидайте результатов."

        if name != turn:
            return "❌ Сейчас не ваш ход."

        # генерируем бросок
        val = random.randint(1,6)
        rolls.append((name, val))
        await update.effective_chat.send_message(f"{self._format_username(name)} бросил 🎲 {val}.")

        # если только первый бросок — планируем второй
        if len(rolls) == 1:
            # таймаут второго броска
            if self.job_queue:
                dj2 = self.job_queue.run_once(
                    self._dice_timeout, 60,
                    chat_id=chat_id,
                    data={"idx":idx, "expected":second},
                    name=f"dice_timeout_{chat_id}_{idx}"
                )
                data["dice_jobs"][idx] = dj2
            return f"Ход {self._format_username(second)}."

        # оба бросили — анализ
        _, v1 = rolls[0]
        _, v2 = rolls[1]
        # ничья
        if v1 == v2:
            rolls.clear()
            await update.effective_chat.send_message(f"Ничья {v1}–{v2}! Переброс, {self._format_username(first)} снова первым.")
            # таймаут первого переброса
            if self.job_queue:
                dj3 = self.job_queue.run_once(
                    self._dice_timeout, 60,
                    chat_id=chat_id,
                    data={"idx":idx, "expected":first},
                    name=f"dice_timeout_{chat_id}_{idx}"
                )
                data["dice_jobs"][idx] = dj3
            return ""

        # определяем победителя пары
        winner = a if v1 > v2 else b
        data["round_wins"].setdefault(idx, {a:0, b:0})[winner] += 1
        data["current_rolls"][idx] = []  # очищаем для следующего броска
        # если набрал 2 победы — пара пройдена
        if data["round_wins"][idx][winner] >= 2:
            await update.effective_chat.send_message(f"🎉 Победитель пары: {self._format_username(winner)}")
            data["next_round"].append(winner)
            data["finished_pairs"].add(idx)
            # отменяем оставшиеся таймауты
            pj = data["pair_timers"].pop(idx, None)
            if pj: pj.schedule_removal()
            await self._proceed_next(chat_id, context.bot)
            return ""
        else:
            # следующий бросок этой же пары
            data["turn_order"][idx] = (first, second)
            await update.effective_chat.send_message(
                f"Счёт {data['round_wins'][idx][a]}–{data['round_wins'][idx][b]}. {self._format_username(first)} ходит первым."
            )
            # планируем таймаут для переброса
            if self.job_queue:
                dj4 = self.job_queue.run_once(
                    self._dice_timeout, 60,
                    chat_id=chat_id,
                    data={"idx":idx, "expected":first},
                    name=f"dice_timeout_{chat_id}_{idx}"
                )
                data["dice_jobs"][idx] = dj4
            return ""

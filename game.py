import random
import time
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telegram.ext import CallbackContext, ContextTypes

class TournamentManager:
    def __init__(self, job_queue):
        self.job_queue = job_queue
        self.chats = {}

    def _format_username(self, name):
        return f"@{name}" if name and not name.startswith('@') else name

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
            "pair_timers": {},  # Новая структура для хранения таймеров
            "finished_pairs": set()
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
        return ", ".join(self._format_username(p) for p in self.chats[chat_id]["players"])

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
            "stage": "round",
            "pairs": pairs,
            "current_pair_idx": 0,
            "round_pairs_count": len(pairs),
            "ready": {}, "first_ready_time": {},
            "ready_jobs": {}, "round_wins": {},
            "round_rolls": {}, "turn_order": {},
            "finished_pairs": set()
        })

        pairs_list = "\n".join(
            f"Пара {i+1}: {self._format_username(a)} vs {self._format_username(b)}" 
            for i, (a, b) in enumerate(pairs)
        )
        first_msg = (f"Пара 1: {self._format_username(pairs[0][0])} vs "
                     f"{self._format_username(pairs[0][1])}\nНажмите «Готов?»")
        kb = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton("Готов?", callback_data="ready_0")
        )

        # Устанавливаем таймер на 120 секунд
        job = self.job_queue.run_once(self._pair_timeout, 120, chat_id=chat_id, data={"idx": 0})
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

        # проверяем, что нажал игрок из пары
        pair = data["pairs"][idx]
        if name not in pair:
            return await q.answer("❌ Вы не в этой паре.", show_alert=True)

        lst = data.setdefault("ready", {}).setdefault(idx, [])
        if name in lst:
            return

        lst.append(name)
        now = time.time()

        # Первый клик — запускаем таймаут
        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            job = self.job_queue.run_once(
                self._ready_timeout,
                60,
                chat_id=chat_id,
                data={"idx": idx},
                name=f"ready_timeout_{chat_id}_{idx}"
            )
            data["ready_jobs"][idx] = job
            await context.bot.send_message(
                chat_id,
                f"✅ {self._format_username(name)} готов! Ждём второго игрока до 60 сек."
            )

            # Обновляем таймер
            self._reset_pair_timer(chat_id, idx, 60)

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
                    f"🎲 Оба готовы! {self._format_username(first)} ходит первым. Используйте /dice"
                )

    # ───────── таймаут 60 с ─────────
    async def _ready_timeout(self, context: CallbackContext):
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        data = self.chats[chat_id]
        confirmed = data.get("ready", {}).get(idx, [])
        pair = data["pairs"][idx]

        # если оба подтвердили — выходим
        if len(confirmed) >= 2:
            return

        # один подтвердил
        if len(confirmed) == 1:
            winner = confirmed[0]
            loser = next(p for p in pair if p != winner)
            data["next_round"].append(winner)
            data["finished_pairs"].add(idx)
            await context.bot.send_message(
                chat_id,
                f"⏰ Время вышло! ✅ {self._format_username(winner)} прошёл дальше, "
                f"а {self._format_username(loser)} не подтвердил готовность."
            )
        # никто не подтвердил
        else:
            a, b = pair
            await context.bot.send_message(
                chat_id,
                f"⏰ Никто не подтвердил готовность — оба выбывают: "
                f"{self._format_username(a)}, {self._format_username(b)}."
            )

        # Далее
        await self._proceed_next(chat_id, context.bot)

    # ───────── сброс таймера пары ─────────
    def _reset_pair_timer(self, chat_id, idx, time_left):
        data = self.chats[chat_id]
        if idx in data["pair_timers"]:
            job = data["pair_timers"].pop(idx)
            job.schedule_removal()

        # Создаем новый таймер
        job = self.job_queue.run_once(self._pair_timeout, time_left, chat_id=chat_id, data={"idx": idx})
        data["pair_timers"][idx] = job

    # ───────── таймаут пары 120 с ─────────
    async def _pair_timeout(self, context: CallbackContext):
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        data = self.chats[chat_id]
        #  Проверка: если пара уже завершена, ничего не делаем
        if idx in data.get("finished_pairs", set()):
            return
        pair = data["pairs"][idx]
        confirmed = data.get("ready", {}).get(idx, [])

        if len(confirmed) < 1:
            a, b = pair
            await context.bot.send_message(
                chat_id,
                f"⏰ Пара {self._format_username(a)} vs {self._format_username(b)} не подтвердила готовность за 120 секунд. Выбывают оба."
            )
            data["finished_pairs"].add(idx)
        await self._proceed_next(chat_id, context.bot)

    # ───────── переход к следующему шагу ─────────
    async def _proceed_next(self, chat_id, bot):
        data = self.chats[chat_id]
        data["current_pair_idx"] += 1
        idx = data["current_pair_idx"]
        pairs = data["pairs"]
        

        # ещё пары в раунде
        if idx < len(pairs):
            a, b = pairs[idx]
            kb = InlineKeyboardMarkup.from_button(
                InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")
            )
            await bot.send_message(
                chat_id,
                f"Следующая пара {idx+1}: {self._format_username(a)} vs "
                f"{self._format_username(b)}\nНажмите «Готов?»",
                reply_markup=kb
            )
            return

        # собираем победителей этого раунда
        winners = data["next_round"]

        # ❗ если нет победителей — турнир прерван
        if not winners:
            await bot.send_message(
                chat_id,
                "⚠️ Никто из участников не проявил активность. Турнир завершён без победителя."
            )
            self.chats.pop(chat_id, None)  # очищаем данные турнира
            return
            
        # определяем лузеров полуфинала для третьего места
        if data["round_pairs_count"] == 2:
            for i, (x, y) in enumerate(data["pairs"]):
                w = data["round_wins"].get(i, {})
                if w.get(x, 0) != w.get(y, 0):
                    loser = x if w[x] < w[y] else y
                    data["semifinal_losers"].append(loser)
                    
        # если есть новый раунд
        if len(winners) > 1:
            data["players"] = winners.copy()
            byes, pairs_list, first_msg, kb = self.start_tournament(chat_id)

            m: Message = await bot.send_message(
                chat_id,
                "Новая сетка раунда:\n" + pairs_list
            )
            await bot.pin_chat_message(chat_id, m.message_id)

            for bye in byes:
                await bot.send_message(chat_id, f"🎉 {self._format_username(bye)} получает bye.")
            await bot.send_message(chat_id, first_msg, reply_markup=kb)
            return
            
        # финал и итоги
        champ  = winners[0]
        runner = None
        w      = data["round_wins"].get(0, {})
        if w:
            p, q = data["pairs"][0]
            runner = p if w.get(p, 0) < w.get(q, 0) else q
        thirds = data["semifinal_losers"]

        text = f"🏆 Победитель: {self._format_username(champ)}\n"
        if runner:
            text += f"🥈 Второе: {self._format_username(runner)}\n"
        if len(thirds) >= 2:
            text += f"🥉 Третьи: {self._format_username(thirds[0])}, {self._format_username(thirds[1])}\n"

        await bot.send_message(chat_id, text)
        data["stage"] = "finished"
        
    # ───────── бросок кубика ─────────
    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        name    = update.effective_user.username or update.effective_user.full_name
        data    = self.chats[chat_id]

        if data["stage"] != "round":
            return "❗Турнир ещё не идёт."

        idx = data["current_pair_idx"]
        if idx >= len(data["pairs"]):
            return "❗ Нет активной пары."

        a, b = data["pairs"][idx]
        if name not in (a, b):
            return "❌ Вы не участвуете в этой паре."

        wins  = data["round_wins"].setdefault(idx, {a: 0, b: 0})
        rolls = data["round_rolls"].setdefault(idx, {})
        first, second = data["turn_order"].get(idx, (a, b))

        # чья очередь
        turn = first if not rolls else second if len(rolls) == 1 else None
        if name != turn:
            return "❌ Сейчас не ваш ход."

        val = random.randint(1, 6)
        rolls[name] = val
        await update.effective_chat.send_message(f"{self._format_username(name)} бросил 🎲 {val}.")

        # если ещё есть второй
        if len(rolls) < 2:
            nxt = second if name == first else first
            return f"Ход {self._format_username(nxt)}."
        else:
            r1, r2 = rolls[a], rolls[b]
            if r1 == r2:
                data["round_rolls"][idx] = {}
                return f"Ничья. Выпало {r1}–{r2}! Переброс, {self._format_username(first)} снова ходит первым."

            winner = a if r1 > r2 else b
            wins[winner] += 1
            data["round_rolls"][idx] = {}

            # если набрал 2 победы
            if wins[winner] >= 2:
                await update.effective_chat.send_message(f"🎉 Победитель пары: {self._format_username(winner)}")
                data["next_round"].append(winner)
            
                # ✅ Отмечаем как завершённую и отменяем таймер
                data["finished_pairs"].add(idx)
                job = data["pair_timers"].pop(idx, None)
                if job:
                    job.schedule_removal()
            
                await self._proceed_next(chat_id, context.bot)
                return ""
            else:
                data["turn_order"][idx] = (first, second)
                return f"Счёт {wins[a]}–{wins[b]}. {self._format_username(first)} ходит первым."

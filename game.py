# game.py

import random
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext

class TournamentManager:
    def __init__(self, job_queue):
        # job_queue — нужен для постановки таймаутов
        self.job_queue = job_queue
        # chats хранит состояние каждого чата по его ID
        self.chats = {}

    def begin_signup(self, chat_id):
        """Инициализация сбора участников."""
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
        """Добавляет игрока при этапе signup."""
        data = self.chats.get(chat_id)
        if not data or data["stage"] != "signup":
            return False
        name = user.username or user.full_name
        if name in data["players"]:
            return False
        data["players"].append(name)
        return True

    def list_players(self, chat_id):
        """Возвращает участников через запятую."""
        return ", ".join(self.chats[chat_id]["players"])

    def start_tournament(self, chat_id):
        """
        Формирует пары:
         - один 'bye' для нечётного числа,
         - список всех пар,
         - возвращает (byes, pairs_list, first_msg, kb).
        """
        data = self.chats.get(chat_id)
        players = data and data["players"][:]
        if not players or len(players) < 2:
            raise ValueError("Нужно как минимум 2 игрока.")
        random.shuffle(players)
        data["next_round"] = []

        # bye
        byes = []
        if len(players) % 2 == 1:
            bye = players.pop(random.randrange(len(players)))
            byes.append(bye)
            data["next_round"].append(bye)

        # пары
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

        # строим текст со всеми парами
        pairs_list = "\n".join(f"Пара {i+1}: {a} vs {b}"
                               for i, (a, b) in enumerate(pairs))
        # приглашение для первой пары
        first_msg = (f"Пара 1: {pairs[0][0]} vs {pairs[0][1]}\n"
                     "Нажмите «Готов?»")
        kb = InlineKeyboardMarkup.from_button(
            InlineKeyboardButton("Готов?", callback_data="ready_0")
        )
        return byes, pairs_list, first_msg, kb

    async def confirm_ready(self, update, context):
        """
        Обрабатывает кнопку «Готов?»:
         - первый клик запускает таймаут 60 с,
         - второй клик до таймаута отменяет его и стартует матч.
        """
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

        # первый клик
        if len(lst) == 1:
            data["first_ready_time"][idx] = now
            job = self.job_queue.run_once(
                self._ready_timeout,
                when=60,
                chat_id=chat_id,
                data={"idx": idx}
            )
            data["ready_jobs"][idx] = job
            await context.bot.send_message(chat_id, f"{name} готов! Ждём второго…")
        else:
            # второй клик до 60 с
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

    def _ready_timeout(self, context: CallbackContext):
        """
        Таймаут 60 с: если вторую готовность не получили —
        объявляем одинокого подтвердившего победителем или обоих выбывшими.
        """
        job = context.job
        chat_id = job.chat_id
        idx = job.data["idx"]
        data = self.chats[chat_id]
        confirmed = data.get("ready", {}).get(idx, [])

        # оба успели — ничего не делаем
        if len(confirmed) >= 2:
            return

        # один успел → проходит дальше
        if len(confirmed) == 1:
            winner = confirmed[0]
            loser = next(p for p in data["pairs"][idx] if p != winner)
            data["next_round"].append(winner)
            context.bot.send_message(
                chat_id,
                f"⏰ Время вышло! {winner} проходит дальше, {loser} не нажал «Готов?»."
            )
        else:
            # никто не нажал — оба выбывают
            a, b = data["pairs"][idx]
            context.bot.send_message(
                chat_id,
                f"⏰ Никто не подтвердил — оба выбывают: {a}, {b}."
            )

        # сразу переходим к следующей паре или раунду/финалу
        self._proceed_next(chat_id, context.bot)

    def _proceed_next(self, chat_id, bot):
        """
        Переход к следующей паре:
         - если остались пары → показываем «Готов?» новой паре,
         - иначе → новый раунд или окончание турнира с объявлением призёров.
        """
        data = self.chats[chat_id]
        data["current_pair_idx"] += 1
        idx = data["current_pair_idx"]
        pairs = data["pairs"]

        # новая пара
        if idx < len(pairs):
            a, b = pairs[idx]
            kb = InlineKeyboardMarkup.from_button(
                InlineKeyboardButton("Готов?", callback_data=f"ready_{idx}")
            )
            bot.send_message(
                chat_id,
                f"Пара {idx+1}: {a} vs {b}\nНажмите «Готов?»",
                reply_markup=kb
            )
            return

        # все пары раунда сыграны
        winners = data["next_round"]
        # полуфинальные лузеры для бронзы (если было 2 пары)
        if data["round_pairs_count"] == 2:
            for i, (x, y) in enumerate(data["pairs"]):
                w = data["round_wins"].get(i, {})
                if w.get(x, 0) != w.get(y, 0):
                    loser = x if w[x] < w[y] else y
                    data["semifinal_losers"].append(loser)

        # новый раунд
        if len(winners) > 1:
            data["players"] = winners.copy()
            byes, pairs_list, msg, kb = self.start_tournament(chat_id)
            for bye in byes:
                bot.send_message(chat_id, f"🎉 {bye} получает bye.")
            bot.send_message(chat_id, "Сетки турнира:\n" + pairs_list)
            bot.send_message(chat_id, msg, reply_markup=kb)
            return

        # финал
        champ = winners[0]
        runner = None
        w = data["round_wins"].get(0, {})
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

    async def roll_dice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /dice — бросок кубика в правильном порядке,
        подсчёт best-of-3 и переход к следующей паре при 2 победах.
        """
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

        wins = data["round_wins"].setdefault(idx, {a: 0, b: 0})
        rolls = data["round_rolls"].setdefault(idx, {})
        first, second = data["turn_order"].get(idx, (a, b))

        # чей ход?
        if not rolls:
            turn = first
        elif len(rolls) == 1:
            turn = second
        else:
            turn = None

        if name != turn:
            return "Сейчас не ваш ход."

        val = random.randint(1, 6)
        rolls[name] = val

        # если ещё не оба бросили
        if len(rolls) < 2:
            nxt = second if name == first else first
            return f"{name} бросил {val}. Ход {nxt}."
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

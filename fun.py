"""Развлечения: каждая команда отключается отдельно через /settings."""
import datetime as dt
import logging
import math
import random
import time

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler

from storage import FUN_TITLES, store
from utils import esc, mention, mention_id, say

log = logging.getLogger("fun")

BALL = [
    "Бесспорно ✅", "Предрешено ✅", "Никаких сомнений ✅", "Определённо да ✅", "Можешь быть уверен ✅",
    "Скорее всего 👍", "Хорошие перспективы 👍", "Знаки говорят — да 👍", "Пока не ясно, спроси позже 🌫",
    "Сконцентрируйся и спроси снова 🌫", "Лучше не рассказывать 🤐", "Даже не думай ❌",
    "Мой ответ — нет ❌", "Перспективы не очень 👎", "Весьма сомнительно 👎",
]
JOKES = [
    "— Доктор, я забываю всё через минуту! — И давно это у вас? — Что «давно»?",
    "Программист ложится спать. Жена: «Закрой окно, холодно». — «И что, станет тепло, если я его закрою?»",
    "— Алло, это прачечная? — Нет, это Пентагон. — А почему так громко стираете?",
    "Купил себе ручку — теперь пишу, как пишется.",
    "— Как дела на работе? — Как в сказке: чем дальше, тем страшнее.",
    "Учитель: «Кто хочет в туалет — выходите!» Вовочка: «А кто не хочет?» — «Тот остаётся на второй год».",
    "Я бы бросил пить кофе, но тогда я бросил бы и всё остальное.",
    "— Сынок, где ты был? — В школе! — Странно, школа закрыта на карантин…",
    "Диета — это когда ты не ешь то, что хочешь, и ешь то, что не хочешь, чтобы хотеть меньше.",
    "Админ чата — это человек, который читает всё, что вы пишете, и молчит. Пока.",
    "— Почему ты опоздал? — Я не опоздал, это ты слишком рано начал.",
    "Будильник — это изобретение, чтобы ненавидеть утро точно по расписанию.",
]
TRUTH = [
    "Какая твоя самая странная привычка?", "О чём ты соврал(а) в последний раз?",
    "Какой твой самый неловкий момент в жизни?", "Кому в чате ты бы доверил(а) секрет?",
    "Что ты делаешь, когда никто не видит?", "Какой подарок был самым худшим?",
    "Чего ты боишься больше всего?", "Какую песню ты слушаешь тайком от всех?",
    "Кого в этом чате ты считаешь самым смешным?", "Что бы ты сделал(а), будь у тебя день невидимости?",
]
DARE = [
    "Отправь в чат своё последнее фото из галереи (не личное 😉).", "Напиши комплимент любому участнику чата.",
    "Сделай голосовое и спой припев любой песни.", "Поставь смешной статус на час.",
    "Напиши сообщение задом наперёд.", "Расскажи анекдот — самый ужасный, какой знаешь.",
    "Отправь стикер, который описывает твоё настроение, без слов.", "Придумай ник для себя на сегодня и пиши только с ним.",
]
FACTS = [
    "Мёд не портится: в египетских гробницах находили съедобный мёд возрастом более 3000 лет.",
    "У осьминога три сердца и голубая кровь.",
    "Бананы с ботанической точки зрения — ягоды, а клубника — нет.",
    "Венера вращается в обратную сторону: Солнце там встаёт на западе.",
    "Акулы появились раньше деревьев.",
    "Сердце креветки находится в голове.",
    "Улитки могут спать до трёх лет подряд.",
    "Эйфелева башня летом становится выше на 10–15 см из-за расширения металла.",
    "Вомбаты делают кубические какашки.",
    "Молния нагревает воздух до температуры около 30 000 °C — это горячее поверхности Солнца.",
    "Коровы имеют лучших друзей и грустят, если их разлучить.",
    "Отпечатки пальцев есть и у коал — почти неотличимые от человеческих.",
]
PREDICT = [
    "Сегодня тебя ждёт неожиданная новость 📬", "Скоро ты найдёшь то, что давно потерял 🔍",
    "Удача улыбнётся тебе ближе к вечеру 🍀", "Остерегайся людей, которые пишут «бан» четыре раза подряд 😏",
    "Тебя ждёт вкусный ужин 🍕", "Кто-то сегодня о тебе вспомнит 💭", "Деньги придут оттуда, откуда не ждёшь 💸",
    "Лучшее время для смелого шага — прямо сейчас 🚀", "Тебе стоит больше спать 😴",
    "Завтра будет лучше, чем сегодня 🌅",
]


def _today() -> str:
    return dt.date.today().isoformat()


def _level(xp: int) -> int:
    return int(math.sqrt(max(xp, 0) / 8)) + 1


def _args(context) -> str:
    return " ".join(context.args or []).strip()


async def _reply(update: Update, context, text: str):
    msg = update.effective_message
    await say(context.bot, update.effective_chat.id, text, reply_to=msg.message_id)


# ───────────────────────────── команды ─────────────────────────────


async def f_ball(update, context, s):
    if not _args(context):
        return await _reply(update, context, "🎱 Задай вопрос: <code>/ball я стану богатым?</code>")
    await _reply(update, context, f"🎱 {random.choice(BALL)}")


async def f_dice(update, context, s):
    await context.bot.send_dice(update.effective_chat.id, emoji="🎲")


async def f_slots(update, context, s):
    await context.bot.send_dice(update.effective_chat.id, emoji="🎰")


async def f_coin(update, context, s):
    r = random.random()
    await _reply(update, context, "🪙 Монета встала на ребро!" if r < 0.01 else ("🪙 Орёл" if r < 0.505 else "🪙 Решка"))


async def f_choose(update, context, s):
    raw = _args(context)
    parts = [p.strip() for p in (raw.split("|") if "|" in raw else raw.replace(" или ", "|").split("|")) if p.strip()]
    if len(parts) < 2:
        return await _reply(update, context, "🤔 Напиши варианты: <code>/choose пицца | суши</code>")
    await _reply(update, context, f"🤔 Выбираю: <b>{esc(random.choice(parts))}</b>")


async def f_rate(update, context, s):
    what = _args(context)
    if not what:
        return await _reply(update, context, "📊 Что оценить? <code>/rate мой ник</code>")
    await _reply(update, context, f"📊 «{esc(what[:100])}» — <b>{random.randint(0, 10)}/10</b>")


async def _pick_user(chat_id: int, fallback):
    store_rows = flush_and_recent(chat_id)
    if store_rows:
        uid, name = random.choice(store_rows)
        return mention_id(uid, name)
    return mention(fallback)


def flush_and_recent(chat_id: int):
    flush()
    return store.recent_users(chat_id)


async def f_who(update, context, s):
    what = _args(context)
    if not what:
        return await _reply(update, context, "🕵️ Спроси: <code>/who самый ленивый</code>")
    who = await _pick_user(update.effective_chat.id, update.effective_user)
    await _reply(update, context, f"🕵️ Я думаю, <b>{esc(what[:100])}</b> — это {who}")


async def f_joke(update, context, s):
    await _reply(update, context, "😂 " + esc(random.choice(JOKES)))


async def f_roulette(update, context, s):
    user = mention(update.effective_user)
    if random.randint(1, 6) == 1:
        await _reply(update, context, f"🔫 {user} крутит барабан… <b>БАХ!</b> 💥 (шутка, все живы)")
    else:
        await _reply(update, context, f"🔫 {user} крутит барабан… щелчок. Повезло 😮‍💨")


async def f_compat(update, context, s):
    msg = update.effective_message
    me = update.effective_user
    if msg.reply_to_message and msg.reply_to_message.from_user:
        other = msg.reply_to_message.from_user
        a, b = sorted([me.id, other.id])
        pct = random.Random(f"{a}-{b}").randint(0, 100)
        title = f"{mention(me)} + {mention(other)}"
    else:
        parts = (context.args or [])[:2]
        if len(parts) < 2:
            return await _reply(update, context, "💞 Ответь на сообщение командой /compat или <code>/compat Аня Вася</code>")
        key = "-".join(sorted(p.lower() for p in parts))
        pct = random.Random(key).randint(0, 100)
        title = f"{esc(parts[0])} + {esc(parts[1])}"
    await _reply(update, context, f"💞 {title}\nСовместимость: <b>{pct}%</b>")


async def f_truth(update, context, s):
    await _reply(update, context, "🗣 <b>Вопрос:</b> " + esc(random.choice(TRUTH)))


async def f_dare(update, context, s):
    await _reply(update, context, "😈 <b>Задание:</b> " + esc(random.choice(DARE)))


async def f_hero(update, context, s):
    chat_id = update.effective_chat.id
    if store.kv_get(chat_id, "hero_date") == _today():
        data = (store.kv_get(chat_id, "hero") or "0|?").split("|", 1)
        return await _reply(update, context, f"👑 Герой дня уже выбран: {mention_id(int(data[0]), data[1])}")
    rows = flush_and_recent(chat_id)
    if rows:
        uid, name = random.choice(rows)
    else:
        uid, name = update.effective_user.id, update.effective_user.full_name
    store.kv_set(chat_id, "hero_date", _today())
    store.kv_set(chat_id, "hero", f"{uid}|{name}")
    await _reply(update, context, f"👑 Герой дня — {mention_id(uid, name)}! Все аплодируем 👏")


async def f_duel(update, context, s):
    msg = update.effective_message
    me = update.effective_user
    other = msg.reply_to_message.from_user if msg.reply_to_message else None
    if not other or other.is_bot or other.id == me.id:
        return await _reply(update, context, "⚔️ Ответь командой /duel на сообщение соперника.")
    win, lose = random.sample([me, other], 2)
    await _reply(update, context, f"⚔️ Дуэль! {mention(me)} против {mention(other)}…\n🏆 Побеждает {mention(win)}!")


async def f_iq(update, context, s):
    u = update.effective_user
    iq = random.Random(f"{u.id}-{_today()}").randint(60, 160)
    await _reply(update, context, f"🧠 IQ {mention(u)} на сегодня: <b>{iq}</b>")


async def f_fact(update, context, s):
    await _reply(update, context, "📚 " + esc(random.choice(FACTS)))


async def f_predict(update, context, s):
    await _reply(update, context, "🔮 " + esc(random.choice(PREDICT)))


async def f_me(update, context, s):
    if not s["fun_xp"]:
        return await _reply(update, context, "Подсчёт активности выключен.")
    flush()
    u = update.effective_user
    row = store.user_stats(update.effective_chat.id, u.id)
    if not row:
        return await _reply(update, context, "Пока нет данных — напиши пару сообщений!")
    msgs, xp, rank = row
    await _reply(
        update, context,
        f"👤 {mention(u)}\nСообщений: <b>{msgs}</b>\nОпыт: <b>{xp}</b>\nУровень: <b>{_level(xp)}</b>\nМесто в чате: <b>#{rank}</b>",
    )


async def f_top(update, context, s):
    if not s["fun_xp"]:
        return await _reply(update, context, "Подсчёт активности выключен.")
    flush()
    rows = store.top(update.effective_chat.id, 10)
    if not rows:
        return await _reply(update, context, "Пока никого в топе.")
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[i] if i < 3 else f'{i + 1}.'} {mention_id(uid, name)} — ур. {_level(xp)} ({msgs} сообщ.)"
        for i, (uid, name, msgs, xp) in enumerate(rows)
    ]
    await _reply(update, context, "🏆 <b>Топ актива</b>\n" + "\n".join(lines))


HANDLERS = {
    "ball": f_ball, "dice": f_dice, "slots": f_slots, "coin": f_coin, "choose": f_choose, "rate": f_rate,
    "who": f_who, "joke": f_joke, "roulette": f_roulette, "compat": f_compat, "truth": f_truth,
    "dare": f_dare, "hero": f_hero, "duel": f_duel, "iq": f_iq, "fact": f_fact, "predict": f_predict,
    "me": f_me, "top": f_top,
}
assert set(HANDLERS) == set(FUN_TITLES), "FUN_TITLES и HANDLERS рассинхронизированы"

_cd: dict = {}


def _wrap(name, fn):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
        if not (chat and user and msg) or user.is_bot:
            return
        s = store.get(chat.id)
        if not s["fun_enabled"] or not s.get("fun_" + name, True):
            return
        now = time.monotonic()
        if now - _cd.get((chat.id, user.id), 0) < 1.5:  # анти-спам самим командам
            return
        _cd[(chat.id, user.id)] = now
        if len(_cd) > 5000:
            _cd.clear()
        try:
            await fn(update, context, s)
        except Exception:
            log.exception("fun command %s failed", name)

    return handler


# ───────────────────────────── учёт активности ─────────────────────────────

_buf: dict = {}  # (chat,user) -> [name, msgs, xp]
_last_xp: dict = {}


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if not (chat and user and msg) or user.is_bot or msg.sender_chat:
        return
    if user.id in (1087968824, 777000):
        return
    if not store.get(chat.id)["fun_xp"]:
        return
    key = (chat.id, user.id)
    row = _buf.setdefault(key, [user.full_name or str(user.id), 0, 0])
    row[0] = user.full_name or row[0]
    row[1] += 1
    now = time.monotonic()
    if now - _last_xp.get(key, 0) >= 20:
        _last_xp[key] = now
        row[2] += random.randint(1, 3)


def flush():
    if not _buf:
        return
    rows = [(c, u, v[0], v[1], v[2]) for (c, u), v in _buf.items()]
    _buf.clear()
    try:
        store.add_stats(rows)
    except Exception:
        log.exception("flush stats failed")
    now = time.monotonic()
    for k in [k for k, t in _last_xp.items() if now - t > 120]:
        del _last_xp[k]


async def flush_job(context: ContextTypes.DEFAULT_TYPE):
    flush()


def register(app):
    from telegram.ext import filters as f

    for name, fn in HANDLERS.items():
        app.add_handler(CommandHandler(name, _wrap(name, fn), filters=f.ChatType.GROUPS))
    app.add_handler(
        MessageHandler(f.ChatType.GROUPS & f.UpdateType.MESSAGE & ~f.COMMAND, track), group=2
    )
    app.job_queue.run_repeating(flush_job, interval=30, first=30)

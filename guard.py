"""Вся защита чата: анти-снос Ирис, флуд «бан»/«кик», флуд медиа, дубли, рейды."""
import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from telegram import Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

import config
import utils
from storage import store
from utils import esc, mention, mention_id, norm, say

log = logging.getLogger("guard")

rates = utils.Rate()
cooldown: dict = {}  # (chat, user) -> monotonic
PREFIX_CHARS = ".!/+-"


@dataclass
class Cmd:
    ts: float
    user_id: int
    name: str
    text: str
    exempt: bool


@dataclass
class Attack:
    started: float
    last: float
    issuers: dict = field(default_factory=dict)  # id -> name
    victims: dict = field(default_factory=dict)  # id -> name


recent_cmds: dict = defaultdict(lambda: deque(maxlen=30))
attacks: dict[int, Attack] = {}
nuke_victims: dict = defaultdict(lambda: deque(maxlen=200))
nuke_active: dict = {}
raid_until: dict[int, float] = {}  # wall-clock
raid_muted: dict[int, set] = defaultdict(set)
recent_joins: dict = defaultdict(lambda: deque(maxlen=300))


# ───────────────────────────── разбор текста ─────────────────────────────


def body_of(text: str) -> str:
    t = (text or "").strip()
    if t and t[0] in PREFIX_CHARS:
        t = t[1:]
    return norm(t)


def first_word(body: str) -> str:
    return re.split(r"[\s@,.:;!?]", body, maxsplit=1)[0]


def has_word(body: str, words, anywhere: bool) -> bool:
    if not body or not words:
        return False
    ws = {norm(w) for w in words if w}
    if anywhere:
        return bool(ws & set(re.findall(r"\w+", body)))
    return first_word(body) in ws


def is_danger(body: str, s: dict) -> bool:
    return any(body.startswith(norm(p)) for p in s["danger_cmds"] if p and p.strip())


def has_link(msg) -> bool:
    ents = list(msg.entities or []) + list(msg.caption_entities or [])
    if any(e.type in ("url", "text_link") for e in ents):
        return True
    return bool(re.search(r"(t\.me/|telegram\.me/|https?://)", msg.text or msg.caption or "", re.I))


# ───────────────────────────── сообщения ─────────────────────────────


async def guard_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not (msg and chat and user):
        return
    if user.is_bot or user.id in (config.ANON_ADMIN_ID, config.CHANNEL_FWD_ID) or msg.sender_chat:
        return
    s = store.get(chat.id)
    if not s["protect"]:
        return
    if time.time() - msg.date.timestamp() > 60:  # старьё после перезапуска — не трогаем
        return
    try:
        await _guard(context, msg, chat, user, s)
    except Exception:  # защита не должна падать
        log.exception("guard_message error")


async def _guard(context, msg, chat, user, s):
    bot = context.bot
    text = msg.text or msg.caption or ""
    body = body_of(text) if text else ""

    # 1) опасная команда для Ириса («кик неактив сутки» и т.п.)
    if s["iris_guard"] and body and is_danger(body, s):
        exempt = await utils.is_exempt(bot, chat.id, user.id, s)
        recent_cmds[chat.id].append(Cmd(time.monotonic(), user.id, utils.user_label(user), text[:100], exempt))
        if not exempt:
            if s["cmd_delete"]:
                try:
                    await msg.delete()
                except TelegramError:
                    pass
            if s["cmd_instant"]:
                line = await punish_hostile(bot, chat.id, user.id, utils.user_label(user), s)
                await utils.alert(
                    bot, chat.id, s,
                    f"🚨 {mention(user)} написал опасную команду <code>{esc(text[:60])}</code>.\n{line}",
                )
                return

    # 2) «бан» / «кик»
    if body:
        for kind, word in (("ban", "бан"), ("kick", "кик")):
            if s[f"{kind}_guard"] and has_word(body, s[f"{kind}_words"], s["words_anywhere"]):
                n = rates.hit((kind, chat.id, user.id), s[f"{kind}_window"])
                if n > s[f"{kind}_limit"]:
                    await word_flood(context, msg, chat, user, s, kind, word, n)
                    return

    # 3) стикеры / гифки / фото (одинаковые тоже считаются здесь, а не как дубли)
    if msg.sticker or msg.animation or msg.photo:
        if s["media_guard"]:
            n = rates.hit(("media", chat.id, user.id), s["media_window"])
            if n > s["media_limit"]:
                await media_flood(context, msg, chat, user, s, "media", n)
        return

    # 4) одинаковые текстовые сообщения (медиа сюда НЕ входят)
    if msg.text and s["dup_guard"]:
        n = rates.hit(("dup", chat.id, user.id, norm(msg.text)), s["dup_window"])
        if n > s["dup_limit"]:
            await media_flood(context, msg, chat, user, s, "dup", n)
            return

    # 5) ссылки и запрещённые слова (только от обычных участников)
    if text and (s["link_guard"] or (s["word_guard"] and s["bad_words"])):
        bad = False
        if s["link_guard"] and has_link(msg):
            bad = True
        if not bad and s["word_guard"] and s["bad_words"]:
            toks = set(re.findall(r"\w+", norm(text)))
            bad = bool({norm(w) for w in s["bad_words"]} & toks)
        if bad:
            m = await utils.get_member(context.bot, chat.id, user.id)
            if m and m.status in ("member", "restricted") and user.id not in s["whitelist"] \
                    and user.id not in config.OWNER_IDS:
                try:
                    await msg.delete()
                except TelegramError:
                    pass


async def _begin_punish(chat_id: int, user_id: int) -> bool:
    """Защита от двойного наказания. True — можно наказывать."""
    key = (chat_id, user_id)
    now = time.monotonic()
    if now - cooldown.get(key, 0) < 20:
        return False
    cooldown[key] = now
    rates.clear_where(lambda k: len(k) >= 3 and k[1] == chat_id and k[2] == user_id)
    return True


async def word_flood(context, msg, chat, user, s, kind, word, n):
    bot = context.bot
    if not await _begin_punish(chat.id, user.id):
        return
    if await utils.is_exempt(bot, chat.id, user.id, s):
        return
    reason = s[f"{kind}_reason"]

    # 1. снимаем права админа
    st, _ = await utils.strip_rights(bot, chat.id, user.id, full=True)
    # 2. пишем команду для Ириса в ответ на сообщение
    sent = False
    if s["iris_text"]:
        sent = bool(await say(bot, chat.id, f"{esc(s['perma_cmd'])}\n{esc(reason)}", reply_to=msg.message_id))
    # 3. страховка: мутим сами (Ирис может не увидеть сообщение другого бота)
    mt = "—"
    if s["native_punish"]:
        mt = utils.mute_text(await utils.punish_native(bot, chat.id, user.id, 0))
    await utils.alert(
        bot, chat.id, s,
        f"🚨 {mention(user)} написал «{word}» {n} раз за {s[f'{kind}_window']} сек.\n"
        f"• права админа: {utils.strip_text(st)}\n"
        f"• команда для Ириса: {'отправлена' if sent else 'не отправлялась'}\n"
        f"• мут ботом навсегда: {mt}",
    )


async def media_flood(context, msg, chat, user, s, kind, n):
    bot = context.bot
    if not await _begin_punish(chat.id, user.id):
        return
    if await utils.is_exempt(bot, chat.id, user.id, s):
        return
    if s["iris_text"]:
        await say(bot, chat.id, esc(s["flood_cmd"]), reply_to=msg.message_id)
    mt = "—"
    if s["native_punish"]:
        mt = utils.mute_text(
            await utils.punish_native(
                bot, chat.id, user.id, s["flood_mute_minutes"], demote_admin=s["flood_demote_admins"]
            )
        )
    what = "стикеров/гифок/фото" if kind == "media" else "одинаковых сообщений"
    limit_w = s["media_window"] if kind == "media" else s["dup_window"]
    mins = s["flood_mute_minutes"]
    await utils.alert(
        bot, chat.id, s,
        f"🚨 {mention(user)}: {n} {what} за {limit_w} сек.\n"
        f"• «{esc(s['flood_cmd'])}» отправлено Ирису\n"
        f"• мут ботом ({'навсегда' if mins <= 0 else f'{mins} мин'}): {mt}",
    )


async def punish_hostile(bot, chat_id: int, user_id: int, name: str, s: dict) -> str:
    """Автор опасной команды / нукер: снять права + мут."""
    st, _ = await utils.strip_rights(bot, chat_id, user_id, full=True)
    out = f"• {esc(name)}: права — {utils.strip_text(st)}"
    if s["native_punish"]:
        out += f"; мут — {utils.mute_text(await utils.punish_native(bot, chat_id, user_id, 0))}"
    return out


# ───────────────────────────── события участников ─────────────────────────────


def _in_chat(m) -> bool:
    return m.status in ("member", "administrator", "creator") or (
        m.status == "restricted" and getattr(m, "is_member", True)
    )


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if not cmu:
        return
    try:
        await _on_cm(context, cmu)
    except Exception:
        log.exception("on_chat_member error")


async def _on_cm(context, cmu):
    bot = context.bot
    chat, actor = cmu.chat, cmu.from_user
    old, new = cmu.old_chat_member, cmu.new_chat_member
    target = new.user
    utils.invalidate(chat.id, target.id)
    if actor.id == bot.id:
        return
    s = store.get(chat.id)
    if not s["protect"]:
        return

    was_in, now_in = _in_chat(old), _in_chat(new)

    # кто-то кого-то убрал из чата
    if was_in and new.status in ("left", "kicked") and actor.id != target.id:
        if utils.is_iris(actor, s):
            if s["iris_guard"]:
                await on_iris_kick(context, chat, actor, target, s)
        elif s["nuke_guard"]:
            await on_nuke(context, chat, actor, target, "kick", s)
        return

    # кто-то разжаловал админа
    if old.status == "administrator" and new.status in ("member", "restricted") and actor.id != target.id:
        if not utils.is_iris(actor, s) and s["nuke_guard"]:
            await on_nuke(context, chat, actor, target, "demote", s)
        return

    # заход в чат
    if not was_in and now_in:
        await on_join(context, chat, target, s)


# ───────────────────────────── Ирис кикает ─────────────────────────────


async def on_iris_kick(context, chat, iris, victim, s):
    bot = context.bot
    now = time.monotonic()
    if s.get("iris_id") != iris.id:
        store.set(chat.id, "iris_id", iris.id)

    st = attacks.get(chat.id)
    if st:  # атака уже идёт — просто запоминаем жертву
        st.victims[victim.id] = utils.user_label(victim)
        st.last = now
        return

    count = rates.hit(("iriskick", chat.id), s["iris_mass_window"])
    cmds = [c for c in recent_cmds[chat.id] if now - c.ts <= s["cmd_window"]]
    issuers: dict = {}
    if cmds:
        if cmds[-1].exempt:  # команду дал владелец — всё законно
            return
        for c in reversed(cmds):
            if c.exempt:
                break
            issuers[c.user_id] = c.name
    elif count <= s["iris_mass_limit"]:
        return  # одиночные кики Ириса по обычным командам админов — норма

    st = Attack(started=now, last=now, issuers=issuers, victims={victim.id: utils.user_label(victim)})
    attacks[chat.id] = st  # ставим ДО await, чтобы параллельные кики попали в эту атаку

    async def strip_iris():
        res, prev = await utils.strip_rights(bot, chat.id, iris.id, full=s["iris_full_demote"])
        if prev and res == "ok":
            store.set(chat.id, "iris_saved_rights", prev)
        return res

    jobs = []
    if s["iris_strip"]:
        jobs.append(strip_iris())
    if s["iris_punish_issuer"]:
        for uid, name in issuers.items():
            jobs.append(punish_hostile(bot, chat.id, uid, name, s))
    results = await asyncio.gather(*jobs, return_exceptions=True) if jobs else []

    lines = []
    idx = 0
    if s["iris_strip"]:
        r = results[idx]
        idx += 1
        what = "ВСЕ права" if s["iris_full_demote"] else "права бана/кика"
        lines.append(
            f"• у Ириса сняты {what}: {utils.strip_text(r) if isinstance(r, str) else '❌ ошибка'}"
        )
    for _ in issuers:
        r = results[idx]
        idx += 1
        lines.append(r if isinstance(r, str) else "• ❌ не удалось наказать автора команды")
    who = "по команде " + ", ".join(esc(n) for n in issuers.values()) if issuers else "(команду не видел)"
    await utils.alert(
        bot, chat.id, s,
        f"🚨🚨 <b>АТАКА: Ирис кикает людей {who}</b>\n" + "\n".join(lines) +
        ("\n• кикнутых верну в чат автоматически" if s["restore_kicked"] else "") +
        "\nВернуть Ирису права: /restoreiris",
    )
    context.job_queue.run_once(finish_attack, 15, data=chat.id, name=f"finish_{chat.id}")


async def finish_attack(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    st = attacks.get(chat_id)
    if not st:
        return
    now = time.monotonic()
    if now - st.last < 12 and now - st.started < 900:  # кики ещё идут
        context.job_queue.run_once(finish_attack, 10, data=chat_id, name=f"finish_{chat_id}")
        return
    attacks.pop(chat_id, None)
    s = store.get(chat_id)
    if not s["restore_kicked"] or not st.victims:
        return
    try:
        await restore_victims(context.bot, chat_id, st, s)
    except Exception:
        log.exception("restore_victims error")


async def _unban(bot, chat_id, uid) -> bool:
    for _ in range(3):
        try:
            await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            return True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramError:
            return False
    return False


async def restore_victims(bot, chat_id, st: Attack, s: dict):
    ids = list(st.victims)
    ok = 0
    for uid in ids:
        if await _unban(bot, chat_id, uid):
            ok += 1
        await asyncio.sleep(0.05)
    link = None
    try:
        inv = await bot.create_chat_invite_link(chat_id, name="restore", expire_date=int(time.time() + 86400))
        link = inv.invite_link
    except TelegramError:
        try:
            link = await bot.export_chat_invite_link(chat_id)
        except TelegramError:
            link = None
    names = ", ".join(mention_id(u, n) for u, n in list(st.victims.items())[:40])
    more = f" и ещё {len(ids) - 40}" if len(ids) > 40 else ""
    text = f"♻️ Разбанил {ok} из {len(ids)} кикнутых: {names}{more}.\n"
    text += f"Вернуться: {esc(link)}" if link else "Ссылку создать не смог (нужно право «приглашать»), но они могут зайти по любой ссылке."
    await utils.alert(bot, chat_id, s, text)
    if link and s["dm_victims"]:
        for uid in ids[:300]:
            try:
                await bot.send_message(uid, f"Тебя кикнули по ошибке/атаке. Вернуться в чат: {link}")
            except RetryAfter as e:
                await asyncio.sleep(min(e.retry_after, 5) + 0.5)
            except TelegramError:
                pass
            await asyncio.sleep(0.05)


# ───────────────────────────── снос от людей-админов ─────────────────────────────


async def on_nuke(context, chat, actor, target, kind, s):
    bot = context.bot
    key = (chat.id, actor.id)
    now = time.monotonic()
    if kind == "kick" and now - nuke_active.get(key, 0) < 120:
        nuke_active[key] = now
        if s["restore_kicked"]:
            await _unban(bot, chat.id, target.id)
        return
    dq = nuke_victims[key]
    dq.append((now, target.id, utils.user_label(target), kind))
    n = rates.hit(("nuke", chat.id, actor.id), s["nuke_window"])
    if n <= s["nuke_limit"]:
        return
    if await utils.is_exempt(bot, chat.id, actor.id, s):
        return
    nuke_active[key] = now
    line = await punish_hostile(bot, chat.id, actor.id, utils.user_label(actor), s)
    victims = {u: nm for t, u, nm, k in dq if k == "kick" and now - t <= s["nuke_window"] + 5}
    dq.clear()
    restored = 0
    if s["restore_kicked"]:
        for u in victims:
            if await _unban(bot, chat.id, u):
                restored += 1
            await asyncio.sleep(0.05)
    await utils.alert(
        bot, chat.id, s,
        f"🚨 {mention(actor)} массово убирает людей/админов ({n} действий за {s['nuke_window']} сек).\n{line}\n"
        f"• разбанено: {restored} из {len(victims)}",
    )


# ───────────────────────────── заходы / рейд ─────────────────────────────


async def on_join(context, chat, target, s):
    bot = context.bot
    if target.id == bot.id:
        return
    now_wall = time.time()
    raiding = now_wall < raid_until.get(chat.id, 0)

    if s["raid_guard"] and not target.is_bot and target.id not in s["whitelist"] \
            and target.id not in config.OWNER_IDS:
        if raiding:
            await utils.restrict_all(bot, chat.id, target.id, int(raid_until[chat.id]))
            raid_muted[chat.id].add(target.id)
            return
        now = time.monotonic()
        dq = recent_joins[chat.id]
        dq.append((now, target.id))
        while dq and now - dq[0][0] > s["raid_window"]:
            dq.popleft()
        if len(dq) > s["raid_limit"]:
            until = now_wall + s["raid_minutes"] * 60
            raid_until[chat.id] = until
            ids = [u for _, u in dq]
            dq.clear()
            for u in ids:
                await utils.restrict_all(bot, chat.id, u, int(until))
                raid_muted[chat.id].add(u)
            await utils.alert(
                bot, chat.id, s,
                f"🚨 <b>РЕЙД!</b> Слишком много заходов. Новички в муте на {s['raid_minutes']} мин "
                f"(уже замучено {len(ids)}). Снять режим: /unraid",
            )
            return

    if s["welcome"] and not target.is_bot and not raiding:
        text = esc(s["welcome_text"]).replace("{name}", mention(target)).replace("{chat}", esc(chat.title or ""))
        await say(bot, chat.id, text)


async def lift_raid(bot, chat_id: int) -> int:
    raid_until[chat_id] = 0
    ids = list(raid_muted.pop(chat_id, set()))
    try:
        perms = (await bot.get_chat(chat_id)).permissions
    except TelegramError:
        perms = None
    n = 0
    from telegram import ChatPermissions

    perms = perms or ChatPermissions.all_permissions()
    for u in ids:
        m = await utils.get_member(bot, chat_id, u, fresh=True)
        if not m or m.status != "restricted":
            continue
        try:
            await bot.restrict_chat_member(chat_id, u, permissions=perms)
            n += 1
        except TelegramError:
            pass
        await asyncio.sleep(0.05)
    return n


# ───────────────────────────── бот добавлен / права изменены ─────────────────────────────


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mcm = update.my_chat_member
    if not mcm or mcm.chat.type not in ("group", "supergroup"):
        return
    old, new = mcm.old_chat_member.status, mcm.new_chat_member.status
    try:
        if new == "administrator" and old != "administrator":
            await say(
                context.bot, mcm.chat.id,
                "✅ Я админ и на страже. Напишите /setup — проверю свои права и список админов, "
                "/settings — настройки, /help — справка.",
            )
        elif new == "member" and old in ("left", "kicked"):
            await say(
                context.bot, mcm.chat.id,
                "👋 Привет! Сделайте меня админом (права: удаление сообщений, блокировка, "
                "приглашение, <b>назначение админов</b>), и я начну защищать чат. Потом — /setup.",
            )
        elif old == "administrator" and new == "member":
            await say(context.bot, mcm.chat.id, "⚠️ С меня сняли админку — защита не работает!")
    except Exception:
        log.exception("on_my_chat_member error")


def purge_job_sync():
    rates.purge()
    now = time.monotonic()
    for k in [k for k, t in cooldown.items() if now - t > 300]:
        del cooldown[k]
    for k in [k for k, t in nuke_active.items() if now - t > 600]:
        del nuke_active[k]
    for k in [k for k, q in nuke_victims.items() if not q]:
        del nuke_victims[k]


async def purge_job(context: ContextTypes.DEFAULT_TYPE):
    purge_job_sync()

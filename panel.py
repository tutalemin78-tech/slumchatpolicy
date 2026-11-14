"""Настройки и команды управления (только создатель чата и OWNER_IDS)."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

import guard
import utils
from storage import DEFAULTS, EDITABLE_KEYS, FUN_TITLES, PROTECT_TOGGLES, store
from utils import esc, mention_id, norm, say

log = logging.getLogger("panel")
GROUPS = filters.ChatType.GROUPS

BOOL_KEYS = {k for k in EDITABLE_KEYS if isinstance(DEFAULTS[k], bool)}

HELP = (
    "<b>🛡 Бот-защитник чата</b>\n\n"
    "<b>Защита (работает сама):</b>\n"
    "• Ирис начал кикать после «кик неактив сутки» → снимаю у Ириса права бана/кика, "
    "наказываю автора команды, возвращаю кикнутых.\n"
    "• «бан» или «кик» &gt;3 раз за минуту → снимаю права админа, пишу в ответ «мут навсегда» + причина.\n"
    "• &gt;10 стикеров/гифок/фото за 10 сек → «мут».\n"
    "• &gt;15 одинаковых сообщений за 10 сек → «мут».\n"
    "• Админ-человек массово банит/кикает → снимаю права. Рейды → мут новичкам.\n\n"
    "<b>Управление (создатель чата):</b>\n"
    "/settings — меню с переключателями\n"
    "/set — список параметров, <code>/set ban_limit 5</code> — изменить\n"
    "/reason [ban|kick] текст — причина слива\n"
    "/setup — проверка прав бота и списка админов\n"
    "/addadmin — назначить админа ЧЕРЕЗ бота (reply / id / iris)\n"
    "/restoreiris — вернуть Ирису права\n"
    "/trust и /untrust — белый список (reply / id)\n"
    "/unraid — выключить режим рейда\n"
    "/status — состояние\n\n"
    "<b>Веселье:</b> /ball /dice /slots /coin /choose /rate /who /joke /roulette /compat "
    "/truth /dare /hero /duel /iq /fact /predict /me /top"
)


# ───────────────────────────── проверки ─────────────────────────────


async def _manager(update: Update, context) -> bool:
    chat, user = update.effective_chat, update.effective_user
    if not chat or not user or chat.type not in ("group", "supergroup"):
        return False
    if await utils.is_manager(context.bot, chat.id, user.id):
        return True
    return False


async def _r(update: Update, context, text: str):
    await say(context.bot, update.effective_chat.id, text, reply_to=update.effective_message.id)


async def resolve_target(update: Update, context):
    msg = update.effective_message
    chat = update.effective_chat
    s = store.get(chat.id)
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, u.full_name
    if not context.args:
        return None
    arg = context.args[0]
    if arg.lstrip("-").isdigit():
        return int(arg), arg
    if arg.lower() in ("iris", "ирис"):
        uid = await utils.find_iris_id(context.bot, chat.id, s)
        return (uid, "Iris") if uid else None
    if arg.startswith("@"):
        try:
            for a in await context.bot.get_chat_administrators(chat.id):
                if (a.user.username or "").lower() == arg[1:].lower():
                    return a.user.id, a.user.full_name
        except TelegramError:
            pass
        try:
            c = await context.bot.get_chat(arg)
            return c.id, arg
        except TelegramError:
            return None
    return None


# ───────────────────────────── /start /help ─────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await say(
            context.bot, chat.id,
            HELP + "\n\n<b>Как начать:</b> добавьте меня в группу, сделайте админом (включая право "
            "«Назначение админов») и напишите там /setup.",
        )
    else:
        await say(context.bot, chat.id, "👋 Я на месте. /help — справка, /settings — настройки.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await say(context.bot, update.effective_chat.id, HELP)


# ───────────────────────────── меню ─────────────────────────────


def _mark(s, key):
    return "✅" if s[key] else "❌"


def menu_text(page: str, s: dict) -> str:
    if page == "prot":
        return "🛡 <b>Защита</b> — нажмите, чтобы включить/выключить."
    if page == "fun":
        return "🎉 <b>Веселье</b> — включайте и выключайте любые команды."
    if page == "nums":
        def lst(k):
            return esc(", ".join(s[k]) or "—")

        return (
            "🔢 <b>Числа и тексты</b>\nИзменить: <code>/set ключ значение</code>\n\n"
            f"<b>Бан:</b> лимит <code>ban_limit</code>={s['ban_limit']}, окно <code>ban_window</code>={s['ban_window']}с, "
            f"слова <code>ban_words</code>={lst('ban_words')}\n"
            f"причина <code>ban_reason</code>: «{esc(s['ban_reason'])}»\n"
            f"<b>Кик:</b> лимит <code>kick_limit</code>={s['kick_limit']}, окно <code>kick_window</code>={s['kick_window']}с, "
            f"слова <code>kick_words</code>={lst('kick_words')}\n"
            f"причина <code>kick_reason</code>: «{esc(s['kick_reason'])}»\n"
            f"<b>Команда слива:</b> <code>perma_cmd</code>: «{esc(s['perma_cmd'])}»\n"
            f"<b>Медиа:</b> <code>media_limit</code>={s['media_limit']} за <code>media_window</code>={s['media_window']}с\n"
            f"<b>Дубли:</b> <code>dup_limit</code>={s['dup_limit']} за <code>dup_window</code>={s['dup_window']}с\n"
            f"<b>Команда флуда:</b> <code>flood_cmd</code>: «{esc(s['flood_cmd'])}», "
            f"мут ботом <code>flood_mute_minutes</code>={s['flood_mute_minutes']} мин (0 = навсегда)\n"
            f"<b>Опасные команды Ириса</b> <code>danger_cmds</code>: {lst('danger_cmds')}\n"
            f"<code>cmd_window</code>={s['cmd_window']}с, <code>iris_mass_limit</code>={s['iris_mass_limit']} "
            f"за <code>iris_mass_window</code>={s['iris_mass_window']}с\n"
            f"<b>Анти-снос людей:</b> <code>nuke_limit</code>={s['nuke_limit']} за <code>nuke_window</code>={s['nuke_window']}с\n"
            f"<b>Рейд:</b> <code>raid_limit</code>={s['raid_limit']} за <code>raid_window</code>={s['raid_window']}с, "
            f"мут <code>raid_minutes</code>={s['raid_minutes']} мин\n"
            f"<b>Слова-фильтр</b> <code>bad_words</code>: {lst('bad_words')}\n"
            f"<b>Приветствие</b> <code>welcome_text</code>: {esc(s['welcome_text'])}"
        )
    return "⚙️ <b>Настройки бота</b>\nВыберите раздел:"


def menu_markup(page: str, s: dict) -> InlineKeyboardMarkup:
    rows = []
    if page == "main":
        rows = [
            [InlineKeyboardButton("🛡 Защита", callback_data="m:pg:prot")],
            [InlineKeyboardButton("🎉 Веселье", callback_data="m:pg:fun")],
            [InlineKeyboardButton("🔢 Числа и тексты", callback_data="m:pg:nums")],
            [InlineKeyboardButton("✖ Закрыть", callback_data="m:close")],
        ]
    elif page == "prot":
        for key, title in PROTECT_TOGGLES:
            rows.append([InlineKeyboardButton(f"{_mark(s, key)} {title.strip()}", callback_data=f"m:t:{key}:prot")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m:pg:main")])
    elif page == "fun":
        rows.append([InlineKeyboardButton(f"{_mark(s, 'fun_enabled')} ВЕСЬ РАЗДЕЛ «Веселье»", callback_data="m:t:fun_enabled:fun")])
        rows.append([InlineKeyboardButton(f"{_mark(s, 'fun_xp')} Подсчёт актива (/me, /top)", callback_data="m:t:fun_xp:fun")])
        items = list(FUN_TITLES.items())
        for i in range(0, len(items), 2):
            row = []
            for name, title in items[i : i + 2]:
                row.append(InlineKeyboardButton(f"{_mark(s, 'fun_' + name)} {title.split(' — ')[0]}", callback_data=f"m:t:fun_{name}:fun"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m:pg:main")])
    else:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m:pg:main")])
    return InlineKeyboardMarkup(rows)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    s = store.get(update.effective_chat.id)
    await context.bot.send_message(
        update.effective_chat.id, menu_text("main", s), parse_mode=ParseMode.HTML,
        reply_markup=menu_markup("main", s),
    )


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat = update.effective_chat
    if not q or not chat or not q.data:
        return
    if not await utils.is_manager(context.bot, chat.id, q.from_user.id):
        await q.answer("Только создатель чата может менять настройки.", show_alert=True)
        return
    s = store.get(chat.id)
    parts = q.data.split(":")
    page = "main"
    if parts[1] == "close":
        await q.answer()
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return
    if parts[1] == "pg" and len(parts) >= 3:
        page = parts[2]
    elif parts[1] == "t" and len(parts) >= 4:
        key, page = parts[2], parts[3]
        if key in BOOL_KEYS:
            store.set(chat.id, key, not s[key])
    if page not in ("main", "prot", "fun", "nums"):
        page = "main"
    s = store.get(chat.id)
    try:
        await q.edit_message_text(menu_text(page, s), parse_mode=ParseMode.HTML, reply_markup=menu_markup(page, s))
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("menu edit failed: %s", e)
    await q.answer()


# ───────────────────────────── /set ─────────────────────────────


def coerce(key: str, raw: str):
    d = DEFAULTS[key]
    raw = raw.strip()
    if isinstance(d, bool):
        v = raw.lower()
        if v in ("1", "on", "true", "yes", "вкл", "да", "+"):
            return True
        if v in ("0", "off", "false", "no", "выкл", "нет", "-"):
            return False
        raise ValueError("нужно вкл/выкл")
    if isinstance(d, int):
        n = int(raw)
        lo = 1 if key.endswith(("_limit", "_window")) else 0
        hi = 525600 if "minutes" in key else 100000
        if not lo <= n <= hi:
            raise ValueError(f"число от {lo} до {hi}")
        return n
    if isinstance(d, list):
        if raw == "-":
            return []
        items = [norm(x) for x in raw.split(",") if x.strip()]
        if not items and key != "bad_words":
            raise ValueError("список пуст (через запятую)")
        return items
    if not raw or len(raw) > 500:
        raise ValueError("текст от 1 до 500 символов")
    return raw


async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    chat = update.effective_chat
    s = store.get(chat.id)
    text = update.effective_message.text or ""
    parts = text.split(None, 2)
    if len(parts) < 3:
        lines = []
        for k in EDITABLE_KEYS:
            v = s[k]
            if isinstance(v, bool):
                v = "вкл" if v else "выкл"
            elif isinstance(v, list):
                v = ", ".join(v) or "-"
            lines.append(f"<code>{k}</code> = {esc(v)}")
        for i in range(0, len(lines), 45):
            await say(context.bot, chat.id, ("<b>Параметры</b> (<code>/set ключ значение</code>):\n" if i == 0 else "") + "\n".join(lines[i : i + 45]))
        return
    key, raw = parts[1].lower(), parts[2]
    if key not in EDITABLE_KEYS:
        return await _r(update, context, "❌ Нет такого параметра. Список: /set")
    try:
        val = coerce(key, raw)
    except ValueError as e:
        return await _r(update, context, f"❌ {esc(e)}")
    store.set(chat.id, key, val)
    await _r(update, context, f"✅ <code>{key}</code> = {esc(val)}")


async def cmd_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    chat = update.effective_chat
    s = store.get(chat.id)
    parts = (update.effective_message.text or "").split(None, 1)
    if len(parts) < 2:
        return await _r(
            update, context,
            f"Причина за «бан»: <b>{esc(s['ban_reason'])}</b>\nПричина за «кик»: <b>{esc(s['kick_reason'])}</b>\n\n"
            "Изменить: <code>/reason новая причина</code> (обе) или <code>/reason ban текст</code> / <code>/reason kick текст</code>",
        )
    rest = parts[1].strip()
    first, _, tail = rest.partition(" ")
    keys = ["ban_reason", "kick_reason"]
    if first.lower() in ("ban", "бан") and tail.strip():
        keys, rest = ["ban_reason"], tail.strip()
    elif first.lower() in ("kick", "кик") and tail.strip():
        keys, rest = ["kick_reason"], tail.strip()
    if len(rest) > 300:
        return await _r(update, context, "❌ Слишком длинно (до 300 символов).")
    for k in keys:
        store.set(chat.id, k, rest)
    await _r(update, context, f"✅ Причина сохранена: <b>{esc(rest)}</b>")


# ───────────────────────────── белый список ─────────────────────────────


async def cmd_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    chat = update.effective_chat
    t = await resolve_target(update, context)
    if not t:
        return await _r(update, context, "Ответьте на сообщение человека или укажите ID: <code>/trust 12345</code>")
    s = store.get(chat.id)
    wl = list(s["whitelist"])
    if t[0] not in wl:
        wl.append(t[0])
    store.set(chat.id, "whitelist", wl)
    await _r(update, context, f"✅ {mention_id(*t)} в белом списке — защита его не трогает.")


async def cmd_untrust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    chat = update.effective_chat
    t = await resolve_target(update, context)
    if not t:
        return await _r(update, context, "Ответьте на сообщение человека или укажите ID.")
    s = store.get(chat.id)
    store.set(chat.id, "whitelist", [x for x in s["whitelist"] if x != t[0]])
    await _r(update, context, f"✅ {mention_id(*t)} убран из белого списка.")


# ───────────────────────────── /setup /addadmin /restoreiris ─────────────────────────────


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    bot, chat = context.bot, update.effective_chat
    s = store.get(chat.id)
    store.save(chat.id)
    me = await utils.get_member(bot, chat.id, bot.id, fresh=True)
    lines = ["<b>🔧 Проверка</b>"]
    if not me or me.status != "administrator":
        lines.append("❌ Я не админ! Сделайте меня админом, иначе ничего не работает.")
    else:
        need = {
            "can_restrict_members": "блокировка участников",
            "can_delete_messages": "удаление сообщений",
            "can_promote_members": "назначение админов",
            "can_invite_users": "приглашение",
        }
        for f, title in need.items():
            lines.append(f"{'✅' if getattr(me, f, False) else '❌'} право: {title}")
    try:
        admins = await bot.get_chat_administrators(chat.id)
    except TelegramError as e:
        admins = []
        lines.append(f"❌ Не могу получить список админов: {esc(e.message)}")
    iris_found = False
    if admins:
        lines.append("\n<b>Админы:</b>")
        for a in admins[:40]:
            if a.user.id == bot.id:
                continue
            tag = ""
            if utils.is_iris(a.user, s):
                iris_found = True
                store.set(chat.id, "iris_id", a.user.id)
                tag = " (Ирис)"
            if a.status == "creator":
                st = "👑 создатель"
            elif getattr(a, "can_be_edited", False):
                st = "🟢 под защитой (могу снять права)"
            else:
                st = "🟡 НЕ под защитой — назначен не мной"
            lines.append(f"• {esc(a.user.full_name)}{tag}: {st}")
    if not iris_found:
        lines.append("\nℹ️ Ирис среди админов не найден (@iris_moon_bot).")
    lines.append(
        "\n<b>Важно:</b> Telegram позволяет боту снимать права только у тех админов, "
        "которых назначил он сам. Чтобы я мог остановить Ирис/админа — снимите ему права вручную и "
        "назначьте заново командой /addadmin (ответом на сообщение, по ID или <code>/addadmin iris</code>)."
    )
    await say(bot, chat.id, "\n".join(lines))


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    bot, chat = context.bot, update.effective_chat
    t = await resolve_target(update, context)
    if not t:
        return await _r(update, context, "Укажите цель: ответом на сообщение, ID или <code>/addadmin iris</code>")
    s = store.get(chat.id)
    is_ir = bool(s["iris_id"]) and t[0] == s["iris_id"]
    err = await utils.grant_rights(bot, chat.id, t[0], utils.full_rights(with_promote=is_ir))
    if err:
        return await _r(
            update, context,
            f"❌ Не вышло: {esc(err)}\nЕсли он уже админ — снимите ему права вручную и повторите; "
            "мне нужно право «Назначение админов».",
        )
    await _r(update, context, f"✅ {mention_id(*t)} назначен админом через меня — теперь я смогу снять права в случае слива.")


async def cmd_restoreiris(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    bot, chat = context.bot, update.effective_chat
    s = store.get(chat.id)
    uid = await utils.find_iris_id(bot, chat.id, s)
    if not uid:
        return await _r(update, context, "❌ Не знаю ID Ириса. Укажите: <code>/addadmin ID</code> или задайте IRIS_IDS в .env")
    rights = {k: bool(v) for k, v in (s["iris_saved_rights"] or {}).items() if k in utils.RIGHT_FIELDS}
    if not rights:
        rights = utils.full_rights(with_promote=True)
    err = await utils.grant_rights(bot, chat.id, uid, rights)
    if err:
        return await _r(update, context, f"❌ Не вышло: {esc(err)}")
    await _r(update, context, "✅ Права Ирису возвращены.")


async def cmd_unraid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    n = await guard.lift_raid(context.bot, update.effective_chat.id)
    await _r(update, context, f"✅ Режим рейда выключен. Размучено: {n}.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _manager(update, context):
        return
    s = store.get(update.effective_chat.id)
    on = lambda k: "✅" if s[k] else "❌"  # noqa: E731
    await _r(
        update, context,
        "<b>📊 Состояние</b>\n"
        f"{on('protect')} защита в целом\n"
        f"{on('iris_guard')} анти-снос Ирис (опасных фраз: {len(s['danger_cmds'])})\n"
        f"{on('ban_guard')} «бан» &gt;{s['ban_limit']} за {s['ban_window']}с → «{esc(s['perma_cmd'])} / {esc(s['ban_reason'])}»\n"
        f"{on('kick_guard')} «кик» &gt;{s['kick_limit']} за {s['kick_window']}с → «{esc(s['perma_cmd'])} / {esc(s['kick_reason'])}»\n"
        f"{on('media_guard')} медиа &gt;{s['media_limit']} за {s['media_window']}с → «{esc(s['flood_cmd'])}»\n"
        f"{on('dup_guard')} дубли &gt;{s['dup_limit']} за {s['dup_window']}с → «{esc(s['flood_cmd'])}»\n"
        f"{on('nuke_guard')} анти-снос админов  {on('raid_guard')} анти-рейд\n"
        f"{on('native_punish')} мут самим ботом  {on('iris_text')} команды для Ириса\n"
        f"👤 белый список: {len(s['whitelist'])}\n"
        f"🎉 веселье: {'вкл' if s['fun_enabled'] else 'выкл'}",
    )


def register(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    for name, fn in (
        ("settings", cmd_settings), ("set", cmd_set), ("reason", cmd_reason), ("trust", cmd_trust),
        ("untrust", cmd_untrust), ("setup", cmd_setup), ("addadmin", cmd_addadmin),
        ("restoreiris", cmd_restoreiris), ("unraid", cmd_unraid), ("status", cmd_status),
    ):
        app.add_handler(CommandHandler(name, fn, filters=GROUPS))
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^m:"))

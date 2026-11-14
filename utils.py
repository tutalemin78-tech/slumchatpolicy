"""Общие помощники: права, мут, кэш участников, лимитер, отправка."""
import asyncio
import html
import inspect
import logging
import time
from collections import defaultdict, deque

from telegram import Bot, ChatPermissions, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

import config
from storage import store

log = logging.getLogger("utils")

# ───────────────────────────── форматирование ─────────────────────────────


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def user_label(user) -> str:
    name = user.full_name or (f"@{user.username}" if user.username else str(user.id))
    return name


def mention_id(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name or user_id)}</a>'


def mention(user) -> str:
    return mention_id(user.id, user_label(user))


def norm(s: str) -> str:
    return " ".join((s or "").lower().replace("ё", "е").split())


# ───────────────────────────── лимитер ─────────────────────────────


class Rate:
    """Скользящее окно: hit(key, window) -> сколько событий за последние window секунд."""

    def __init__(self):
        self.d: dict = defaultdict(deque)

    def hit(self, key, window: float) -> int:
        now = time.monotonic()
        q = self.d[key]
        q.append(now)
        while q and now - q[0] > window:
            q.popleft()
        return len(q)

    def clear_where(self, pred) -> None:
        for k in [k for k in self.d if pred(k)]:
            del self.d[k]

    def purge(self, max_age: float = 300) -> None:
        now = time.monotonic()
        for k in [k for k, q in self.d.items() if not q or now - q[-1] > max_age]:
            del self.d[k]


# ───────────────────────────── участники ─────────────────────────────

_cache: dict = {}


async def get_member(bot: Bot, chat_id: int, user_id: int, fresh: bool = False, ttl: float = 45):
    key = (chat_id, user_id)
    now = time.monotonic()
    if not fresh:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramError as e:
        log.debug("get_chat_member failed: %s", e)
        return None
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = (now, member)
    return member


def invalidate(chat_id: int, user_id: int) -> None:
    _cache.pop((chat_id, user_id), None)


async def is_exempt(bot: Bot, chat_id: int, user_id: int, s: dict) -> bool:
    """Нельзя наказывать: бот, владелец бота, белый список, создатель чата."""
    if user_id == bot.id or user_id in config.OWNER_IDS or user_id in s["whitelist"]:
        return True
    m = await get_member(bot, chat_id, user_id)
    return bool(m and m.status == "creator")


async def is_manager(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Может менять настройки: создатель чата или OWNER_IDS."""
    if user_id in config.OWNER_IDS:
        return True
    m = await get_member(bot, chat_id, user_id)
    return bool(m and m.status == "creator")


def is_iris(user, s: dict) -> bool:
    if not user.is_bot:
        return False
    if (user.username or "").lower() in config.IRIS_USERNAMES:
        return True
    return user.id in config.IRIS_IDS or (s.get("iris_id") and user.id == s["iris_id"])


async def find_iris_id(bot: Bot, chat_id: int, s: dict):
    if s.get("iris_id"):
        return s["iris_id"]
    for i in config.IRIS_IDS:
        return i
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramError:
        return None
    for a in admins:
        if a.user.is_bot and (a.user.username or "").lower() in config.IRIS_USERNAMES:
            store.set(chat_id, "iris_id", a.user.id)
            return a.user.id
    return None


# ───────────────────────────── права админа ─────────────────────────────

_wanted = (
    "can_manage_chat",
    "can_delete_messages",
    "can_manage_video_chats",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_post_stories",
    "can_edit_stories",
    "can_delete_stories",
    "can_pin_messages",
    "can_manage_topics",
)
_params = inspect.signature(Bot.promote_chat_member).parameters
RIGHT_FIELDS = tuple(f for f in _wanted if f in _params)

STRIP_TEXT = {
    "ok": "✅ права сняты",
    "nochange": "✅ нужных прав уже нет",
    "not_admin": "➖ не админ",
    "creator": "➖ создатель чата (его нельзя тронуть)",
    "cant_edit": "❌ не смог: админа назначил не я (Telegram разрешает менять права только тем, кого назначил бот — см. /setup и /addadmin)",
    "error": "❌ не смог (ошибка Telegram)",
}


def strip_text(status: str) -> str:
    if status.startswith("error:"):
        return f"❌ не смог: {esc(status[6:])}"
    return STRIP_TEXT.get(status, status)


async def strip_rights(bot: Bot, chat_id: int, user_id: int, full: bool = True):
    """Снимает права админа. full=False — только бан/кик и назначение админов.
    Возвращает (статус, словарь_прежних_прав)."""
    m = await get_member(bot, chat_id, user_id, fresh=True)
    if m is None:
        return "error", {}
    if m.status == "creator":
        return "creator", {}
    if m.status != "administrator":
        return "not_admin", {}
    prev = {f: bool(getattr(m, f, False)) for f in RIGHT_FIELDS}
    if not getattr(m, "can_be_edited", False):
        return "cant_edit", prev
    if full:
        new = {f: False for f in RIGHT_FIELDS}
    else:
        new = dict(prev)
        for f in ("can_restrict_members", "can_promote_members"):
            if f in new:
                new[f] = False
    if new == prev:
        return "nochange", prev
    for attempt in range(2):
        try:
            await bot.promote_chat_member(chat_id, user_id, is_anonymous=False, **new)
            invalidate(chat_id, user_id)
            return "ok", prev
        except RetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 5) + 0.3)
        except TelegramError as e:
            invalidate(chat_id, user_id)
            return f"error:{e.message}", prev
    return "error", prev


async def grant_rights(bot: Bot, chat_id: int, user_id: int, rights: dict):
    try:
        await bot.promote_chat_member(chat_id, user_id, is_anonymous=False, **rights)
        invalidate(chat_id, user_id)
        return None
    except TelegramError as e:
        return e.message


def full_rights(with_promote: bool = True) -> dict:
    r = {f: True for f in RIGHT_FIELDS}
    if not with_promote:
        r["can_promote_members"] = False
    return r


# ───────────────────────────── мут ─────────────────────────────


async def punish_native(bot: Bot, chat_id: int, user_id: int, minutes: int, demote_admin: bool = True) -> str:
    """Мут самим ботом. minutes=0 — навсегда. Админа сначала разжалует (если можно)."""
    m = await get_member(bot, chat_id, user_id, fresh=True)
    if m is None:
        return "error"
    if m.status == "creator":
        return "creator"
    if m.status in ("left", "kicked"):
        return "gone"
    if m.status == "administrator":
        if not demote_admin:
            return "admin"
        st, _ = await strip_rights(bot, chat_id, user_id, full=True)
        if st != "ok":
            return "admin"
    until = None if minutes <= 0 else int(time.time() + minutes * 60)
    return await restrict_all(bot, chat_id, user_id, until)


async def restrict_all(bot: Bot, chat_id: int, user_id: int, until) -> str:
    for attempt in range(2):
        try:
            await bot.restrict_chat_member(
                chat_id, user_id, permissions=ChatPermissions.no_permissions(), until_date=until
            )
            invalidate(chat_id, user_id)
            return "ok"
        except RetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 5) + 0.3)
        except TelegramError as e:
            return f"error:{e.message}"
    return "error"


def mute_text(status: str) -> str:
    return {
        "ok": "✅ замучен",
        "admin": "❌ он админ, а снять права не вышло",
        "creator": "➖ создатель",
        "gone": "➖ уже не в чате",
        "error": "❌ ошибка",
    }.get(status, f"❌ {esc(status[6:]) if status.startswith('error:') else status}")


# ───────────────────────────── отправка ─────────────────────────────


async def say(bot: Bot, chat_id: int, text: str, reply_to: int | None = None, html_mode: bool = True):
    kw = {}
    if reply_to:
        kw["reply_parameters"] = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
    if html_mode:
        kw["parse_mode"] = ParseMode.HTML
    for attempt in range(2):
        try:
            return await bot.send_message(chat_id, text, **kw)
        except RetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 5) + 0.3)
        except TelegramError as e:
            log.warning("send_message failed in %s: %s", chat_id, e)
            return None
    return None


async def alert(bot: Bot, chat_id: int, s: dict, text: str):
    if s.get("alerts", True):
        await say(bot, chat_id, text)

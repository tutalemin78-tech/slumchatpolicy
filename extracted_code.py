"""
🛡 АНТИСНОС v8 — Защита от сноса + Моментальный Анти-Ирис + /adda с кнопками + /rev + /add + Триггеры + Справка
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("antisnos")

# ==================== КОНФИГУРАЦИЯ ====================
TOKEN = "9061771688:AAHW0RbE_PkIZdUbAkJfOeyqAWJrti4cq8g"
OWNER_ID = 7383168722
DATA_FILE = "bot_data.json"

KNOWN_IRIS_IDS = {5434504334}
KNOWN_IRIS_USERNAMES = {
    "iris_moon_bot", "iris_black_bot", "iris_cm_bot",
    "iris_red_bot", "iris_white_bot", "iris_bot", "iris_tg_bot"
}

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

BOT_ID: int = 0
BOT_USERNAME: str = ""

# База данных
db: Dict[str, Any] = {
    "settings": {},     # chat_id -> dict
    "chats": {},        # chat_id -> title
    "iris_ids": {},     # chat_id -> [iris_user_ids]
    "stats_daily": {},  # chat_id -> { "YYYY-MM-DD": { user_id: count } }
    "pidor_daily": {},  # chat_id -> { "date": "...", "user_id": 123, "name": "..." }
    "triggers": {},     # chat_id -> { "слово": "ответ" }
    "bad_words": {},    # chat_id -> [запрещенные слова]
}

# Скоростные трекеры
ban_tracker = defaultdict(lambda: defaultdict(list))
kick_tracker = defaultdict(lambda: defaultdict(list))
media_tracker = defaultdict(lambda: defaultdict(list))
flood_tracker = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

iris_alert = {}
admin_kicks = defaultdict(lambda: defaultdict(list))
admin_cache = {}

class FormStates(StatesGroup):
    waiting_for_spam_text = State()
    waiting_for_trigger_word = State()
    waiting_for_trigger_reply = State()
    waiting_for_bad_word = State()


# ==================== ДЕФОЛТЫ И СОХРАНЕНИЕ ====================
def default_settings() -> dict:
    return {
        "anti_raid": True,        # 3+ бан/кик за 1 мин -> снять права + мут + ответ @glicu
        "anti_iris": True,        # Моментальный съем прав у Ириса при .кик неактив
        "anti_spam": True,        # 10+ медиа за 10 сек -> мут + ответ @glicu
        "anti_flood": True,       # 10+ ПОЛНОСТЬЮ одинаковых сообщений за 10 сек -> мут + ответ @glicu
        "spam_text": "@glicu",    # Настраиваемый текст ответа при рейде/спаме/флуде
        "anti_links": False,      # Удаление t.me ссылок от не-админов
        "anti_bad_words": False,  # Удаление запрещенных слов
        "triggers_enabled": True, # Пользовательские триггеры
        "fun": True,              # Веселье
    }

def get_chat_cfg(chat_id: int) -> dict:
    cid = str(chat_id)
    if cid not in db["settings"]:
        db["settings"][cid] = default_settings()
    for k, v in default_settings().items():
        if k not in db["settings"][cid]:
            db["settings"][cid][k] = v
    return db["settings"][cid]

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Error saving data: {e}")

def load_data():
    global db
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                for k in ["settings", "chats", "iris_ids", "stats_daily", "pidor_daily", "triggers", "bad_words"]:
                    db[k] = loaded.get(k, {})
        except Exception as e:
            log.error(f"Error loading data: {e}")

def get_today_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")

async def notify_owner(text: str):
    try:
        await bot.send_message(OWNER_ID, text)
    except Exception as e:
        log.warning(f"Could not send log to owner {OWNER_ID}: {e}")


# ==================== УТИЛИТЫ АДМИНИСТРИРОВАНИЯ ====================
async def get_user_status(cid: int, uid: int):
    now = time.time()
    cached = admin_cache.get((cid, uid))
    if cached and now - cached[2] < 15:
        return cached[0], cached[1]
    
    try:
        m = await bot.get_chat_member(cid, uid)
        is_creator = m.status == ChatMemberStatus.CREATOR
        is_admin = is_creator or m.status == ChatMemberStatus.ADMINISTRATOR
    except Exception:
        is_admin = False
        is_creator = False
        
    admin_cache[(cid, uid)] = (is_admin, is_creator, now)
    return is_admin, is_creator

async def get_bot_admin_rights(cid: int) -> Dict[str, bool]:
    """Получает реальные права нашего бота в чате, чтобы не нарушать RIGHT_FORBIDDEN"""
    try:
        m = await bot.get_chat_member(cid, BOT_ID)
        if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return {
                "can_promote_members": getattr(m, "can_promote_members", True),
                "can_change_info": getattr(m, "can_change_info", True),
                "can_delete_messages": getattr(m, "can_delete_messages", True),
                "can_restrict_members": getattr(m, "can_restrict_members", True),
                "can_invite_users": getattr(m, "can_invite_users", True),
                "can_pin_messages": getattr(m, "can_pin_messages", True),
                "can_manage_video_chats": getattr(m, "can_manage_video_chats", True),
                "can_manage_topics": getattr(m, "can_manage_topics", True),
            }
    except Exception as e:
        log.warning(f"get_bot_admin_rights error: {e}")
    # Дефолт если creator
    return {
        "can_promote_members": True, "can_change_info": True,
        "can_delete_messages": True, "can_restrict_members": True,
        "can_invite_users": True, "can_pin_messages": True,
        "can_manage_video_chats": True, "can_manage_topics": True,
    }

async def demote_admin(cid: int, uid: int) -> bool:
    try:
        await bot.promote_chat_member(
            cid, uid,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            can_manage_chat=False,
            can_manage_topics=False,
        )
        try:
            await bot.set_chat_administrator_custom_title(cid, uid, "")
        except Exception:
            pass
        admin_cache.pop((cid, uid), None)
        return True
    except Exception as e:
        log.warning(f"demote failed for {uid} in {cid}: {e}")
        return False

MUTE_ALL_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_manage_topics=False,
)

async def mute_user_forever(cid: int, uid: int) -> bool:
    try:
        await bot.restrict_chat_member(cid, uid, permissions=MUTE_ALL_PERMS, until_date=0)
        return True
    except Exception as e:
        log.warning(f"mute failed for {uid} in {cid}: {e}")
        return False

def clean_timestamps(ts_list: list, window: float) -> list:
    cutoff = time.time() - window
    return [t for t in ts_list if t > cutoff]

async def scan_and_cache_iris(cid: int) -> List[int]:
    str_cid = str(cid)
    if str_cid not in db["iris_ids"]:
        db["iris_ids"][str_cid] = list(KNOWN_IRIS_IDS)
    else:
        for i_id in KNOWN_IRIS_IDS:
            if i_id not in db["iris_ids"][str_cid]:
                db["iris_ids"][str_cid].append(i_id)

    try:
        admins = await bot.get_chat_administrators(cid)
        for adm in admins:
            u = adm.user
            un = (u.username or "").lower()
            fn = (u.first_name or "").lower()
            if u.id in KNOWN_IRIS_IDS or un in KNOWN_IRIS_USERNAMES or "iris" in un or "ирис" in fn:
                if u.id not in db["iris_ids"][str_cid]:
                    db["iris_ids"][str_cid].append(u.id)
        save_data()
    except Exception as e:
        log.warning(f"scan iris error in {cid}: {e}")
    return db["iris_ids"][str_cid]

async def revoke_all_iris(cid: int) -> bool:
    iris_list = await scan_and_cache_iris(cid)
    any_success = False
    for iris_id in iris_list:
        ok = await demote_admin(cid, iris_id)
        if ok:
            any_success = True
            log.info(f"[{cid}] Сняты права у Ириса ID={iris_id}")
    return any_success

def is_user_iris(cid: int, uid: int, username: str = "", name: str = "") -> bool:
    str_cid = str(cid)
    if uid in KNOWN_IRIS_IDS:
        return True
    if uid in db.get("iris_ids", {}).get(str_cid, []):
        return True
    un = (username or "").lower()
    fn = (name or "").lower()
    if un in KNOWN_IRIS_USERNAMES or "iris" in un or "ирис" in fn:
        return True
    return False


# ==================== КЛАВИАТУРА ПРАВ АДМИНИСТРАТОРА (/adda) ====================
async def get_admin_perms_dict(cid: int, uid: int) -> Dict[str, bool]:
    """Получает текущие права целевого пользователя"""
    try:
        m = await bot.get_chat_member(cid, uid)
        if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return {
                "promote": getattr(m, "can_promote_members", False),
                "info": getattr(m, "can_change_info", False),
                "delete": getattr(m, "can_delete_messages", False),
                "restrict": getattr(m, "can_restrict_members", False),
                "invite": getattr(m, "can_invite_users", False),
                "pin": getattr(m, "can_pin_messages", False),
                "video": getattr(m, "can_manage_video_chats", False),
                "anon": getattr(m, "is_anonymous", False),
                "topics": getattr(m, "can_manage_topics", False),
            }
    except Exception:
        pass
    return {
        "promote": False, "info": False, "delete": False,
        "restrict": False, "invite": True, "pin": False,
        "video": False, "anon": False, "topics": False
    }

def kb_admin_panel(cid: int, uid: int, p: Dict[str, bool]) -> InlineKeyboardMarkup:
    """Интерфейс клавиатуры строго по скриншоту пользователя"""
    def sym(flag: bool) -> str:
        return "➕" if flag else "➖"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{sym(p['promote'])} Назначение админов", callback_data=f"adm_p:{cid}:{uid}:promote"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p['info'])} Профиль группы", callback_data=f"adm_p:{cid}:{uid}:info"),
            InlineKeyboardButton(text=f"{sym(p['delete'])} Удаление смс", callback_data=f"adm_p:{cid}:{uid}:delete"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p['restrict'])} Баны", callback_data=f"adm_p:{cid}:{uid}:restrict"),
            InlineKeyboardButton(text=f"{sym(p['invite'])} Инвайты", callback_data=f"adm_p:{cid}:{uid}:invite"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p['pin'])} Закрепы", callback_data=f"adm_p:{cid}:{uid}:pin"),
            InlineKeyboardButton(text=f"{sym(p['video'])} Трансляции", callback_data=f"adm_p:{cid}:{uid}:video"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p['anon'])} Анонимность", callback_data=f"adm_p:{cid}:{uid}:anon"),
            InlineKeyboardButton(text=f"{sym(p['topics'])} Теги", callback_data=f"adm_p:{cid}:{uid}:topics"),
        ],
        [
            InlineKeyboardButton(text="Снять", callback_data=f"adm_revoke:{cid}:{uid}"),
        ]
    ])


# ==================== КОМАНДЫ /adda, /add, /addg, /rev ====================
@router.message(Command("adda"))
async def cmd_adda_handler(m: Message):
    """Выдает префикс и открывает панель настройки прав с кнопками"""
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await m.reply("команда работает только в чате")
        return

    # ТОЛЬКО OWNER_ID
    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("ответь реплаем на того, кому выдать права:\n<code>/adda [префикс]</code>")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    # Парсим префикс
    raw = m.text.split(maxsplit=1)
    prefix = raw[1].strip()[:16] if len(raw) > 1 else "Админ"

    bot_rights = await get_bot_admin_rights(cid)

    try:
        # Назначаем минимальным админом (только инвайты)
        await bot.promote_chat_member(
            chat_id=cid,
            user_id=target_user.id,
            can_manage_chat=True,
            can_invite_users=bot_rights.get("can_invite_users", True),
            can_change_info=False,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            can_manage_topics=False,
        )

        # Ботам нельзя ставить custom_title
        if not target_user.is_bot:
            try:
                await bot.set_chat_administrator_custom_title(cid, target_user.id, prefix)
            except Exception as e:
                log.warning(f"custom_title error: {e}")

        admin_cache.pop((cid, target_user.id), None)
        current_perms = await get_admin_perms_dict(cid, target_user.id)

        # Отправляем сообщение строго в стиле скриншота:
        msg_text = f"🗓 <b>ТГ-права администратора {target_name}</b>"
        await m.reply(msg_text, reply_markup=kb_admin_panel(cid, target_user.id, current_perms), parse_mode="HTML")

        await notify_owner(
            f"👑 <b>ОТКРЫТА ПАНЕЛЬ ПРАВ /adda</b>\n"
            f"Чат: «{m.chat.title}»\n"
            f"Кому: {target_name} [ID: <code>{target_user.id}</code>]\n"
            f"Префикс: <b>[{prefix}]</b>"
        )

    except TelegramBadRequest as e:
        log.error(f"TelegramBadRequest in /adda: {e.message}")
        await m.reply(f"⚠️ ошибка Telegram API: {e.message}\n(Убедись, что наш бот стоит выше этого юзера/бота и имеет право 'Назначение администраторов')")
    except Exception as e:
        log.error(f"Error in /adda: {e}")
        await m.reply(f"⚠️ ошибка: {e}")


@router.callback_query(F.data.startswith("adm_p:"))
async def cb_toggle_admin_perm(cb: CallbackQuery):
    """Переключение отдельного права админа на инлайн-панели"""
    if cb.from_user.id != OWNER_ID:
        await cb.answer("только овнер может менять права", show_alert=True)
        return

    parts = cb.data.split(":")
    cid = int(parts[1])
    uid = int(parts[2])
    perm_key = parts[3]

    bot_rights = await get_bot_admin_rights(cid)
    current_p = await get_admin_perms_dict(cid, uid)

    # Инвертируем выбранное право
    new_val = not current_p.get(perm_key, False)

    # Проверяем: есть ли у самого бота это право?
    perm_map = {
        "promote": "can_promote_members",
        "info": "can_change_info",
        "delete": "can_delete_messages",
        "restrict": "can_restrict_members",
        "invite": "can_invite_users",
        "pin": "can_pin_messages",
        "video": "can_manage_video_chats",
        "topics": "can_manage_topics",
    }

    bot_field = perm_map.get(perm_key)
    if new_val and bot_field and not bot_rights.get(bot_field, False):
        await cb.answer(f"⚠️ У нашего бота нет права '{perm_key}' в чате, Telegram запрещает его выдавать!", show_alert=True)
        return

    current_p[perm_key] = new_val

    try:
        await bot.promote_chat_member(
            chat_id=cid,
            user_id=uid,
            is_anonymous=current_p["anon"],
            can_manage_chat=True,
            can_promote_members=current_p["promote"] if bot_rights.get("can_promote_members") else False,
            can_change_info=current_p["info"] if bot_rights.get("can_change_info") else False,
            can_delete_messages=current_p["delete"] if bot_rights.get("can_delete_messages") else False,
            can_restrict_members=current_p["restrict"] if bot_rights.get("can_restrict_members") else False,
            can_invite_users=current_p["invite"] if bot_rights.get("can_invite_users") else False,
            can_pin_messages=current_p["pin"] if bot_rights.get("can_pin_messages") else False,
            can_manage_video_chats=current_p["video"] if bot_rights.get("can_manage_video_chats") else False,
            can_manage_topics=current_p["topics"] if bot_rights.get("can_manage_topics") else False,
        )
        admin_cache.pop((cid, uid), None)
        await cb.message.edit_reply_markup(reply_markup=kb_admin_panel(cid, uid, current_p))
        await cb.answer(f"Право обновлено: {'вкл' if new_val else 'выкл'}")

    except TelegramBadRequest as e:
        log.error(f"TelegramBadRequest in perm toggle: {e.message}")
        await cb.answer(f"Ошибка Telegram: {e.message}", show_alert=True)
    except Exception as e:
        log.error(f"Error in perm toggle: {e}")
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("adm_revoke:"))
async def cb_admin_revoke(cb: CallbackQuery):
    """Кнопка 'Снять' на панели прав"""
    if cb.from_user.id != OWNER_ID:
        await cb.answer("только овнер", show_alert=True)
        return

    parts = cb.data.split(":")
    cid = int(parts[1])
    uid = int(parts[2])

    ok = await demote_admin(cid, uid)
    if ok:
        await cb.message.edit_text("✅ <b>Права администратора и префикс полностью сняты!</b>", parse_mode="HTML")
        await cb.answer("Снято!")
        await notify_owner(f"🗑 <b>ПРАВА СНЯТЫ КНОПКОЙ 'СНЯТЬ'</b>\nЧат ID: <code>{cid}</code>\nПользователь ID: <code>{uid}</code>")
    else:
        await cb.answer("Не удалось снять права (проверь иерархию бота)", show_alert=True)


@router.message(Command("rev", "разжаловать", "снять"))
async def cmd_revoke_handler(m: Message):
    """Команда /rev: забирает префикс и все права админа"""
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("ответь реплаем на того, у кого забрать права: <code>/rev</code>")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    ok = await demote_admin(cid, target_user.id)
    if ok:
        await m.reply(f"забрал префикс и все права у {target_name}")
        await notify_owner(
            f"🗑 <b>СНЯТЫ ВСЕ ПРАВА (/rev)</b>\n"
            f"Чат: «{m.chat.title}»\n"
            f"У кого: {target_name} [ID: <code>{target_user.id}</code>]"
        )
    else:
        await m.reply("⚠️ не смог снять права (убедись, что бот стоит выше этого юзера/бота в списке админов)")


@router.message(Command("add", "addg"))
async def handle_cmd_add_simple(m: Message):
    """Выдает серый префикс без прав (только инвайты)"""
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("ответь реплаем на того, кому выдать префикс:\n<code>/add [префикс]</code>")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    raw = m.text.split(maxsplit=1)
    prefix = raw[1].strip()[:16] if len(raw) > 1 else "говно"

    bot_rights = await get_bot_admin_rights(cid)

    try:
        await bot.promote_chat_member(
            chat_id=cid,
            user_id=target_user.id,
            can_manage_chat=True,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=bot_rights.get("can_invite_users", True),
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            can_manage_topics=False,
        )
        if not target_user.is_bot:
            await bot.set_chat_administrator_custom_title(cid, target_user.id, prefix)

        # СТРОГИЙ ОТВЕТ:
        await m.reply(f"выдал {target_name} префикс [{prefix}]")
        admin_cache.pop((cid, target_user.id), None)

        await notify_owner(
            f"👑 <b>ВЫДАН ПРЕФИКС БЕЗ ПРАВ</b>\n"
            f"Чат: «{m.chat.title}»\n"
            f"Кому: {target_name} [ID: <code>{target_user.id}</code>]\n"
            f"Префикс: <b>[{prefix}]</b>"
        )
    except TelegramBadRequest as e:
        log.error(f"TelegramBadRequest in /add: {e.message}")
        await m.reply(f"⚠️ ошибка Telegram API: {e.message}\n(Убедись, что наш бот стоит выше этого юзера/бота и имеет право 'Назначение администраторов')")
    except Exception as e:
        log.error(f"Error in /add: {e}")
        await m.reply(f"⚠️ ошибка: {e}")


# ==================== ГЛОБАЛЬНЫЙ МИДДЛВАРЬ ЗАЩИТЫ ====================
class GlobalShieldMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if isinstance(event, Message) and event.chat and event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            blocked = await self.process_group_message(event)
            if blocked:
                return
        return await handler(event, data)

    async def process_group_message(self, m: Message) -> bool:
        if not m.from_user or m.from_user.id == BOT_ID:
            return False

        cid = m.chat.id
        str_cid = str(cid)
        uid = m.from_user.id
        user_name = m.from_user.first_name or "Безымянный"
        username_str = f"@{m.from_user.username}" if m.from_user.username else f"id:{uid}"
        chat_title = m.chat.title or f"Чат {cid}"
        cfg = get_chat_cfg(cid)
        spam_reply_text = cfg.get("spam_text", "@glicu")

        if str_cid not in db["chats"] or db["chats"][str_cid] != chat_title:
            db["chats"][str_cid] = chat_title

        # Статистика тайперов за день
        today = get_today_str()
        if str_cid not in db["stats_daily"]:
            db["stats_daily"][str_cid] = {}
        if today not in db["stats_daily"][str_cid]:
            db["stats_daily"][str_cid][today] = {}
        str_uid = str(uid)
        db["stats_daily"][str_cid][today][str_uid] = db["stats_daily"][str_cid][today].get(str_uid, 0) + 1

        is_admin, is_creator = await get_user_status(cid, uid)

        # -------------------------------------------------------------
        # 1. МОМЕНТАЛЬНЫЙ АНТИ-ИРИС: .кик неактив / !кик неактив
        # -------------------------------------------------------------
        if cfg["anti_iris"] and m.text:
            text_clean = m.text.lower().strip()
            if (
                text_clean.startswith((".", "!", "/")) and "кик" in text_clean and "неактив" in text_clean
            ) or (
                "кик неактив" in text_clean
            ):
                success = await revoke_all_iris(cid)
                iris_alert[cid] = time.time()
                log.info(f"[{cid}] ОБНАРУЖЕНА КОМАНДА СНОСА: '{m.text}' -> Снятие прав с Ириса: {success}")
                if success:
                    try:
                        await m.reply("🛡 забрал права у ириса. снос неактива заблокирован на корню.")
                    except Exception:
                        pass
                await notify_owner(
                    f"🛑 <b>ПРЕДОТВРАЩЁН СНОС НЕАКТИВА!</b>\n"
                    f"Чат: «{chat_title}»\n"
                    f"Кто написал: {user_name} ({username_str})\n"
                    f"Команда: <code>{m.text}</code>\n"
                    f"Действие: С Ириса сняты все права администратора ({success})!"
                )
                return False

        # -------------------------------------------------------------
        # 2. АНТИРЕЙД: БАН / КИК (3+ раза за 1 минуту)
        # БОТ СНИМАЕТ ПРАВА, МУТИТ И ОТВЕЧАЕТ РЕПЛАЕМ: @glicu
        # -------------------------------------------------------------
        if cfg["anti_raid"] and m.text:
            text_low = m.text.lower()
            now = time.time()
            triggered = False
            word = ""

            if "бан" in text_low:
                count_ban = text_low.count("бан")
                ban_tracker[cid][uid].extend([now] * count_ban)
                ban_tracker[cid][uid] = clean_timestamps(ban_tracker[cid][uid], 60)
                if len(ban_tracker[cid][uid]) >= 3:
                    triggered = True
                    word = "бан"
                    ban_tracker[cid][uid].clear()

            if not triggered and "кик" in text_low:
                count_kick = text_low.count("кик")
                kick_tracker[cid][uid].extend([now] * count_kick)
                kick_tracker[cid][uid] = clean_timestamps(kick_tracker[cid][uid], 60)
                if len(kick_tracker[cid][uid]) >= 3:
                    triggered = True
                    word = "кик"
                    kick_tracker[cid][uid].clear()

            if triggered:
                if is_creator:
                    try:
                        await m.reply(f"{spam_reply_text}\n(ты создатель чата)")
                    except Exception:
                        pass
                    await notify_owner(
                        f"🚨 <b>ТЕСТ РЕЙДА (Создатель чата)</b>\n"
                        f"Чат: «{chat_title}»\n"
                        f"От: {user_name} ({username_str})\n"
                        f"Слово '{word}' 3+ раз за 1 мин."
                    )
                else:
                    await demote_admin(cid, uid)
                    await mute_user_forever(cid, uid)
                    try:
                        await m.reply(spam_reply_text)
                    except Exception:
                        pass
                    await notify_owner(
                        f"🚨 <b>ПРЕДОТВРАЩЁН РЕЙД (СНЯТ И ЗАМУЧЕН)</b>\n"
                        f"Чат: «{chat_title}»\n"
                        f"Нарушитель: {user_name} ({username_str}) [ID: <code>{uid}</code>]\n"
                        f"Триггер: 3+ раз слово '{word}' за 1 минуту\n"
                        f"Действие: Сняты все права + МУТ навсегда, ответ в чат: {spam_reply_text}"
                    )
                return True

        # -------------------------------------------------------------
        # 3. АНТИСПАМ: СТИКЕРЫ, ГИФКИ, ФОТО (10+ за 10 сек)
        # МУТИТ И ОТВЕЧАЕТ РЕПЛАЕМ: @glicu
        # -------------------------------------------------------------
        if cfg["anti_spam"] and (m.sticker or m.animation or m.photo):
            now = time.time()
            media_tracker[cid][uid].append(now)
            media_tracker[cid][uid] = clean_timestamps(media_tracker[cid][uid], 10)
            if len(media_tracker[cid][uid]) >= 10:
                media_tracker[cid][uid].clear()
                if not is_creator:
                    await mute_user_forever(cid, uid)
                    try:
                        await m.reply(spam_reply_text)
                    except Exception:
                        pass
                    await notify_owner(
                        f"📸 <b>СПАМ МЕДИА НАКАЗАН</b>\n"
                        f"Чат: «{chat_title}»\n"
                        f"Нарушитель: {user_name} ({username_str}) [ID: <code>{uid}</code>]\n"
                        f"Причина: 10+ стикеров/гифок/фото за 10 сек\n"
                        f"Действие: Выдан мут, ответ в чат: {spam_reply_text}"
                    )
                else:
                    try:
                        await m.reply(f"{spam_reply_text}\n(ты создатель)")
                    except Exception:
                        pass
                return True

        # -------------------------------------------------------------
        # 4. АНТИФЛУД: ТОЛЬКО ПОЛНОСТЬЮ ОДИНАКОВЫЕ СООБЩЕНИЯ (10+ за 10 сек)
        # РАЗНЫЕ СООБЩЕНИЯ НЕ МУТИТ НИКОГДА!
        # -------------------------------------------------------------
        if cfg["anti_flood"] and m.text:
            now = time.time()
            msg_exact = m.text.strip()
            msg_hash = hashlib.sha256(msg_exact.encode("utf-8")).hexdigest()
            flood_tracker[cid][uid][msg_hash].append(now)
            flood_tracker[cid][uid][msg_hash] = clean_timestamps(flood_tracker[cid][uid][msg_hash], 10)

            if len(flood_tracker[cid][uid][msg_hash]) >= 10:
                flood_tracker[cid][uid][msg_hash].clear()
                if not is_creator:
                    await mute_user_forever(cid, uid)
                    try:
                        await m.reply(spam_reply_text)
                    except Exception:
                        pass
                    await notify_owner(
                        f"🔄 <b>СПАМ ОДИНАКОВЫМИ СООБЩЕНИЯМИ НАКАЗАН</b>\n"
                        f"Чат: «{chat_title}»\n"
                        f"Нарушитель: {user_name} ({username_str}) [ID: <code>{uid}</code>]\n"
                        f"Текст спама: <code>{msg_exact[:60]}</code>\n"
                        f"Действие: Выдан мут, ответ в чат: {spam_reply_text}"
                    )
                else:
                    try:
                        await m.reply(f"{spam_reply_text}\n(ты создатель)")
                    except Exception:
                        pass
                return True

        # -------------------------------------------------------------
        # 5. АНТИ-ССЫЛКИ
        # -------------------------------------------------------------
        if cfg.get("anti_links") and m.text and not is_admin:
            lo = m.text.lower()
            if "t.me/" in lo or "telegram.me/" in lo:
                try:
                    await m.delete()
                except Exception:
                    pass
                return True

        # -------------------------------------------------------------
        # 6. ЗАПРЕЩЁННЫЕ СЛОВА
        # -------------------------------------------------------------
        if cfg.get("anti_bad_words") and m.text and not is_admin:
            bad_list = db.get("bad_words", {}).get(str_cid, [])
            lo = m.text.lower()
            for bw in bad_list:
                if bw.lower() in lo:
                    try:
                        await m.delete()
                    except Exception:
                        pass
                    return True

        # -------------------------------------------------------------
        # 7. ПОЛЬЗОВАТЕЛЬСКИЕ ТРИГГЕРЫ (АВТООТВЕТЫ)
        # -------------------------------------------------------------
        if cfg.get("triggers_enabled", True) and m.text:
            triggers = db.get("triggers", {}).get(str_cid, {})
            text_low = m.text.lower().strip()
            for trig_word, trig_reply in triggers.items():
                if trig_word.lower() in text_low:
                    try:
                        await m.reply(trig_reply)
                    except Exception:
                        pass
                    break

        return False

dp.message.outer_middleware(GlobalShieldMiddleware())


# ==================== ДЕТЕКТ СНОСА В CHAT MEMBER UPDATE ====================
@router.chat_member()
async def on_chat_member_updated(event: ChatMemberUpdated):
    cid = event.chat.id
    str_cid = str(cid)
    chat_title = event.chat.title or f"Чат {cid}"
    cfg = get_chat_cfg(cid)

    if not cfg["anti_iris"]:
        return

    new_status = event.new_chat_member.status
    if new_status not in (ChatMemberStatus.KICKED, ChatMemberStatus.RESTRICTED, ChatMemberStatus.LEFT):
        return

    actor = event.from_user
    if not actor or actor.id == BOT_ID:
        return

    iris_detected = is_user_iris(cid, actor.id, actor.username or "", actor.first_name or "")
    now = time.time()

    alert_time = iris_alert.get(cid, 0)
    alert_active = (now - alert_time) < 600

    admin_kicks[cid][actor.id].append(now)
    admin_kicks[cid][actor.id] = clean_timestamps(admin_kicks[cid][actor.id], 5)
    mass_kick = len(admin_kicks[cid][actor.id]) >= 2

    if (alert_active and iris_detected) or mass_kick:
        success = await demote_admin(cid, actor.id)
        iris_alert.pop(cid, None)

        actor_mention = f"@{actor.username}" if actor.username else actor.first_name
        if success:
            try:
                await bot.send_message(cid, f"🛡 снёс права у {actor_mention}. снос предотвращён.")
            except Exception:
                pass
            await notify_owner(
                f"🛑 <b>СНОС ЧАТА ПРЕДОТВРАЩЁН!</b>\n"
                f"Чат: «{chat_title}»\n"
                f"Бот/Админ: {actor_mention} [ID: <code>{actor.id}</code>]\n"
                f"Действие: Сняты все права администратора!"
            )


# ==================== ТИШИНА ПРИ ДОБАВЛЕНИИ В ЧАТ ====================
@router.my_chat_member()
async def on_my_chat_member_updated(event: ChatMemberUpdated):
    if event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        cid = event.chat.id
        str_cid = str(cid)
        if event.new_chat_member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
            db["chats"][str_cid] = event.chat.title or f"Chat {cid}"
            save_data()
            asyncio.create_task(scan_and_cache_iris(cid))


# ==================== НАСТРОЙКИ В ЛС (ТОЛЬКО ДЛЯ ОВНЕРА!) ====================
def kb_chat_list() -> InlineKeyboardMarkup:
    buttons = []
    for str_cid, title in db.get("chats", {}).items():
        buttons.append([InlineKeyboardButton(text=f"💬 {title}", callback_data=f"cfg_chat:{str_cid}")])
    buttons.append([InlineKeyboardButton(text="📖 Справка по всем функциям", callback_data="help_manual")])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="refresh_chats")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_chat_panel(chat_id: int) -> InlineKeyboardMarkup:
    cfg = get_chat_cfg(chat_id)
    str_cid = str(chat_id)

    def icon(val: bool) -> str:
        return "✅ ВКЛ" if val else "❌ ВЫКЛ"

    iris_count = len(db.get("iris_ids", {}).get(str_cid, []))
    trig_count = len(db.get("triggers", {}).get(str_cid, {}))
    bad_count = len(db.get("bad_words", {}).get(str_cid, []))

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"🛡 Рейд (бан/кик 3х): {icon(cfg['anti_raid'])}", callback_data=f"tog:{str_cid}:anti_raid"),
            InlineKeyboardButton(text=f"🛑 Анти-Ирис: {icon(cfg['anti_iris'])}", callback_data=f"tog:{str_cid}:anti_iris"),
        ],
        [
            InlineKeyboardButton(text=f"📸 Спам медиа: {icon(cfg['anti_spam'])}", callback_data=f"tog:{str_cid}:anti_spam"),
            InlineKeyboardButton(text=f"🔄 Антифлуд 10х: {icon(cfg['anti_flood'])}", callback_data=f"tog:{str_cid}:anti_flood"),
        ],
        [
            InlineKeyboardButton(text=f"🔗 Анти-ссылки: {icon(cfg.get('anti_links', False))}", callback_data=f"tog:{str_cid}:anti_links"),
            InlineKeyboardButton(text=f"🤬 Запретка: {icon(cfg.get('anti_bad_words', False))}", callback_data=f"tog:{str_cid}:anti_bad_words"),
        ],
        [
            InlineKeyboardButton(text=f"🎉 Веселье (игры/троллинг): {icon(cfg['fun'])}", callback_data=f"tog:{str_cid}:fun"),
        ],
        [
            InlineKeyboardButton(text=f"💬 Ответ при спаме: [{cfg.get('spam_text', '@glicu')}]", callback_data=f"set_spam_text:{str_cid}"),
        ],
        [
            InlineKeyboardButton(text=f"🎯 Триггеры ({trig_count} шт.): {icon(cfg.get('triggers_enabled', True))}", callback_data=f"menu_trig:{str_cid}"),
            InlineKeyboardButton(text=f"🚫 Запретные слова ({bad_count} шт.)", callback_data=f"menu_bad:{str_cid}"),
        ],
        [
            InlineKeyboardButton(text=f"🔍 Сканировать Ириса ({iris_count} в базе)", callback_data=f"scan_iris:{str_cid}"),
        ],
        [
            InlineKeyboardButton(text="📖 Справка", callback_data="help_manual"),
            InlineKeyboardButton(text="⬅️ К чатам", callback_data="back_to_chats"),
        ]
    ])

@router.message(CommandStart())
async def cmd_start_private(m: Message):
    if m.chat.type != ChatType.PRIVATE:
        await m.reply(f"настройки только в лс: @{BOT_USERNAME}")
        return

    if m.from_user.id != OWNER_ID:
        await m.reply("доступ запрещён. ты не овнер бота.")
        return

    chats = db.get("chats", {})
    if not chats:
        await m.reply("добавь бота в чат админом с полными правами, напиши в чате любое слово и пиши сюда /start")
        return

    await m.reply("выбирай чат для настройки:", reply_markup=kb_chat_list())

@router.callback_query(F.data == "back_to_chats")
async def cb_back_to_chats(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    await cb.message.edit_text("выбирай чат для настройки:", reply_markup=kb_chat_list())
    await cb.answer()

@router.callback_query(F.data == "refresh_chats")
async def cb_refresh_chats(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    await cb.message.edit_text("выбирай чат для настройки:", reply_markup=kb_chat_list())
    await cb.answer("обновлено")

@router.callback_query(F.data == "help_manual")
async def cb_help_manual(cb: CallbackQuery):
    manual = (
        "📖 <b>ПОЛНОЕ ОПИСАНИЕ ВСЕХ ВОЗМОЖНОСТЕЙ БОТА:</b>\n\n"
        "🛡 <b>1. Антирейд (бан/кик 3х):</b>\n"
        "— Если кто-то пишет 'бан' или 'кик' 3+ раза за 1 минуту: бот тихо снимает с него права админа, мутит навсегда и отвечает реплаем текстом (по умолчанию <code>@glicu</code>).\n\n"
        "🛑 <b>2. Анти-Ирис (кик неактив):</b>\n"
        "— Если кто-то пишет в чате <code>.кик неактив</code> или <code>!кик неактив</code>: бот МОМЕНТАЛЬНО снимает все права с Ириса до того, как он успеет кого-то кикнуть!\n\n"
        "📸 <b>3. Антиспам медиа:</b>\n"
        "— 10+ стикеров, гифок или фото за 10 секунд -> мут + ответ реплаем <code>@glicu</code>.\n\n"
        "🔄 <b>4. Антифлуд одинаковыми сообщениями:</b>\n"
        "— 10+ ПОЛНОСТЬЮ одинаковых сообщений за 10 сек -> мут + ответ реплаем <code>@glicu</code>.\n"
        "— <b>Разные сообщения не трогает вообще</b>!\n\n"
        "👑 <b>5. Команды назначения админов (ТОЛЬКО ДЛЯ ТЕБЯ):</b>\n"
        "— <code>/adda [префикс]</code> (реплаем) — открывает панель с кнопками настройки прав админа (нажми на нужные права в реальном времени)!\n"
        "— <code>/add [префикс]</code> или <code>/addg [префикс]</code> (реплаем) — выдает серый префикс без прав (только инвайты), ответ: <code>выдал Ник префикс [префикс]</code>.\n"
        "— <code>/rev</code> (реплаем) — моментально забирает префикс и ВСЕ права админа!\n\n"
        "🎉 <b>6. Функция «Веселье» (троллинг в чате):</b>\n"
        "— <code>/пидор</code> — выбор главного пидора дня из актива чата.\n"
        "— <code>/шип</code> — шипперит двоих участников чата.\n"
        "— <code>/гей</code> — % гея с радужным флагом.\n"
        "— <code>/шлюха</code> — % шлюхи.\n"
        "— <code>/айкью</code> — уровень IQ с дерзким комментом.\n"
        "— <code>/хуй</code> — замер агрегата.\n"
        "— <code>/дуэль</code> — шуточная дуэль реплаем.\n"
        "— <code>/казнь</code> — шуточная казнь участника реплаем.\n"
        "— <code>/кто [вопрос]</code> — находит виновного из чата.\n"
        "— <code>/инфа [текст]</code> — шанс вероятности от 0 до 100%.\n"
        "— <code>/шар [вопрос]</code> — дерзкий шар предсказаний.\n"
        "— <code>/typers</code> — статистика самых активных за сутки.\n\n"
        "🎯 <b>7. Триггеры автоответов:</b>\n"
        "— Задаешь слово и ответ. Когда кто-то пишет это слово — бот отвечает реплаем.\n\n"
        "🔗 <b>8. Анти-ссылки и Запретные слова:</b>\n"
        "— Удаление рекламных ссылок и запретных слов от обычных участников.\n\n"
        "📩 <b>9. Логи нарушений:</b>\n"
        "— Все отчеты по рейдерам, спамерам и попыткам сноса прилетают тебе в ЛС!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_chats")]
    ])
    await cb.message.edit_text(manual, reply_markup=kb, parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("cfg_chat:"))
async def cb_open_chat_cfg(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    str_cid = cb.data.split(":")[1]
    cid = int(str_cid)
    title = db.get("chats", {}).get(str_cid, f"Чат {cid}")
    await scan_and_cache_iris(cid)
    await cb.message.edit_text(
        f"⚙️ настройки для «{title}»:\nжми на кнопки для переключения:",
        reply_markup=kb_chat_panel(cid)
    )
    await cb.answer()

@router.callback_query(F.data.startswith("tog:"))
async def cb_toggle_option(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    _, str_cid, opt = cb.data.split(":")
    cid = int(str_cid)
    cfg = get_chat_cfg(cid)
    cfg[opt] = not cfg.get(opt, True)
    save_data()
    await cb.message.edit_reply_markup(reply_markup=kb_chat_panel(cid))
    await cb.answer("включено" if cfg[opt] else "выключено")

@router.callback_query(F.data.startswith("set_spam_text:"))
async def cb_set_spam_text(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    str_cid = cb.data.split(":")[1]
    await state.set_state(FormStates.waiting_for_spam_text)
    await state.update_data(target_cid=str_cid)
    current = get_chat_cfg(int(str_cid)).get("spam_text", "@glicu")
    await cb.message.edit_text(
        f"✏️ введи текст ответа при рейде и спаме (сейчас: <code>{current}</code>):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"cfg_chat:{str_cid}")]
        ]),
        parse_mode="HTML"
    )
    await cb.answer()

@router.message(FormStates.waiting_for_spam_text)
async def process_new_spam_text(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID:
        return
    data = await state.get_data()
    str_cid = data.get("target_cid")
    if not str_cid or not m.text:
        await m.reply("ошибка, пиши /start")
        await state.clear()
        return

    cid = int(str_cid)
    new_text = m.text.strip()
    cfg = get_chat_cfg(cid)
    cfg["spam_text"] = new_text
    save_data()
    await state.clear()

    title = db.get("chats", {}).get(str_cid, f"Чат {cid}")
    await m.reply(
        f"✅ текст ответа сохранён: «{new_text}»\n\n⚙️ настройки «{title}»:",
        reply_markup=kb_chat_panel(cid)
    )

@router.callback_query(F.data.startswith("scan_iris:"))
async def cb_scan_iris(cb: CallbackQuery):
    str_cid = cb.data.split(":")[1]
    cid = int(str_cid)
    iris_ids = await scan_and_cache_iris(cid)
    await cb.message.edit_reply_markup(reply_markup=kb_chat_panel(cid))
    await cb.answer(f"Ирисов в базе чата: {len(iris_ids)} шт.", show_alert=True)


# ==================== МЕНЮ ТРИГГЕРОВ ====================
def kb_triggers_menu(str_cid: str) -> InlineKeyboardMarkup:
    trigs = db.get("triggers", {}).get(str_cid, {})
    buttons = []
    for word in list(trigs.keys())[:8]:
        buttons.append([InlineKeyboardButton(text=f"❌ Удалить '{word}'", callback_data=f"del_trig:{str_cid}:{word}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить триггер", callback_data=f"add_trig:{str_cid}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data=f"cfg_chat:{str_cid}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(F.data.startswith("menu_trig:"))
async def cb_menu_triggers(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    str_cid = cb.data.split(":")[1]
    trigs = db.get("triggers", {}).get(str_cid, {})
    text = "🎯 <b>Пользовательские триггеры автоответов:</b>\n"
    if not trigs:
        text += "Пока нет добавленных триггеров.\nНажми 'Добавить', чтобы бот отвечал на нужные слова!"
    else:
        for w, r in trigs.items():
            text += f"• <code>{w}</code> ➡️ <i>{r}</i>\n"

    await cb.message.edit_text(text, reply_markup=kb_triggers_menu(str_cid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("add_trig:"))
async def cb_add_trigger_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    str_cid = cb.data.split(":")[1]
    await state.set_state(FormStates.waiting_for_trigger_word)
    await state.update_data(target_cid=str_cid)
    await cb.message.edit_text(
        "✏️ Шаг 1: Напиши <b>слово или фразу</b>, на которую бот должен реагировать в чате:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"menu_trig:{str_cid}")]
        ]),
        parse_mode="HTML"
    )
    await cb.answer()

@router.message(FormStates.waiting_for_trigger_word)
async def process_trigger_word(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID or not m.text:
        return
    word = m.text.strip().lower()
    await state.update_data(trigger_word=word)
    await state.set_state(FormStates.waiting_for_trigger_reply)
    await m.reply(
        f"✅ Слово: <code>{word}</code>\n\n✏️ Шаг 2: Теперь напиши <b>текст ответа</b>, который бот напишет в чат:",
        parse_mode="HTML"
    )

@router.message(FormStates.waiting_for_trigger_reply)
async def process_trigger_reply(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID or not m.text:
        return
    data = await state.get_data()
    str_cid = data.get("target_cid")
    word = data.get("trigger_word")
    reply_text = m.text.strip()

    if str_cid not in db["triggers"]:
        db["triggers"][str_cid] = {}
    db["triggers"][str_cid][word] = reply_text
    save_data()
    await state.clear()

    await m.reply(
        f"🎉 <b>Триггер сохранён!</b>\nСлово: <code>{word}</code> ➡️ <i>{reply_text}</i>",
        reply_markup=kb_triggers_menu(str_cid),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("del_trig:"))
async def cb_delete_trigger(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    parts = cb.data.split(":")
    str_cid = parts[1]
    word = parts[2]
    if str_cid in db["triggers"] and word in db["triggers"][str_cid]:
        del db["triggers"][str_cid][word]
        save_data()
    await cb_menu_triggers(cb)


# ==================== МЕНЮ ЗАПРЕЩЁННЫХ СЛОВ ====================
def kb_bad_menu(str_cid: str) -> InlineKeyboardMarkup:
    words = db.get("bad_words", {}).get(str_cid, [])
    buttons = []
    for w in words[:8]:
        buttons.append([InlineKeyboardButton(text=f"❌ Удалить '{w}'", callback_data=f"del_bad:{str_cid}:{w}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить запретное слово", callback_data=f"add_bad:{str_cid}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data=f"cfg_chat:{str_cid}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(F.data.startswith("menu_bad:"))
async def cb_menu_bad(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    str_cid = cb.data.split(":")[1]
    words = db.get("bad_words", {}).get(str_cid, [])
    text = "🚫 <b>Запрещённые слова (автоудаление сообщений):</b>\n"
    if not words:
        text += "Список пуст.\nНажми 'Добавить', чтобы запретить слова!"
    else:
        text += ", ".join([f"<code>{w}</code>" for w in words])

    await cb.message.edit_text(text, reply_markup=kb_bad_menu(str_cid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("add_bad:"))
async def cb_add_bad_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    str_cid = cb.data.split(":")[1]
    await state.set_state(FormStates.waiting_for_bad_word)
    await state.update_data(target_cid=str_cid)
    await cb.message.edit_text(
        "✏️ Напиши слово, которое бот должен удалять в чате:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"menu_bad:{str_cid}")]
        ])
    )
    await cb.answer()

@router.message(FormStates.waiting_for_bad_word)
async def process_bad_word(m: Message, state: FSMContext):
    if m.from_user.id != OWNER_ID or not m.text:
        return
    data = await state.get_data()
    str_cid = data.get("target_cid")
    word = m.text.strip().lower()

    if str_cid not in db["bad_words"]:
        db["bad_words"][str_cid] = []
    if word not in db["bad_words"][str_cid]:
        db["bad_words"][str_cid].append(word)
        save_data()
    await state.clear()

    await m.reply(
        f"✅ Слово <code>{word}</code> добавлено в запретный список!",
        reply_markup=kb_bad_menu(str_cid),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("del_bad:"))
async def cb_del_bad(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return
    parts = cb.data.split(":")
    str_cid = parts[1]
    word = parts[2]
    if str_cid in db["bad_words"] and word in db["bad_words"][str_cid]:
        db["bad_words"][str_cid].remove(word)
        save_data()
    await cb_menu_bad(cb)


# ==================== СТАТИСТИКА ТАЙПЕРОВ ====================
async def get_user_display_name(cid: int, uid: int) -> str:
    try:
        cm = await bot.get_chat_member(cid, uid)
        u = cm.user
        return u.first_name or f"id{uid}"
    except Exception:
        return f"id{uid}"

@router.message(Command("typers", "тайперы"))
async def cmd_typers(m: Message):
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    cid = m.chat.id
    str_cid = str(cid)
    today = get_today_str()

    stats = db.get("stats_daily", {}).get(str_cid, {}).get(today, {})
    if not stats:
        await m.reply("сегодня чат сдох, сообщений 0")
        return

    sorted_users = sorted(stats.items(), key=lambda item: item[1], reverse=True)[:15]
    lines = [f"📊 тайперы за сегодня ({today}):"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for rank, (str_uid, count) in enumerate(sorted_users, 1):
        uid = int(str_uid)
        name = await get_user_display_name(cid, uid)
        name = name.replace("\n", " ").strip()[:20]
        prefix = medals.get(rank, f"{rank}.")
        lines.append(f"{prefix} {name} — {count}")

    await m.reply("\n".join(lines))


# ==================== ТРОЛЛЬ-ВЕСЕЛЬЕ В ЧАТЕ ====================
def fun_allowed(m: Message) -> bool:
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    return get_chat_cfg(m.chat.id).get("fun", True)

def get_target_user(m: Message):
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user
    return m.from_user

def get_chat_active_uids(cid: int) -> List[int]:
    str_cid = str(cid)
    all_uids = set()
    for day, data in db.get("stats_daily", {}).get(str_cid, {}).items():
        all_uids.update(int(uid) for uid in data.keys())
    return list(all_uids)

@router.message(Command("пидор", "pidor"))
async def cmd_pidor(m: Message):
    if not fun_allowed(m):
        return
    cid = m.chat.id
    str_cid = str(cid)
    today = get_today_str()

    saved = db.get("pidor_daily", {}).get(str_cid, {})
    if saved.get("date") == today:
        await m.reply(f"лошара уже выбран: {saved.get('name', 'кто-то')} 🌈")
        return

    uids = get_chat_active_uids(cid)
    if not uids:
        await m.reply("тут даже выбрать не из кого, вы все мертвые")
        return

    chosen_uid = random.choice(uids)
    name = await get_user_display_name(cid, chosen_uid)
    db.setdefault("pidor_daily", {})[str_cid] = {
        "date": today,
        "user_id": chosen_uid,
        "name": name,
    }
    save_data()
    phrases = [
        f"🎯 детектор сработал!\nпидор дня: {name} 🌈 поздравляем лоха",
        f"🔍 сканирую биомусор...\nпидор дня найден: {name} 💅",
        f"👑 корона главного петуха уходит: {name}",
    ]
    await m.reply(random.choice(phrases))

@router.message(Command("шип", "ship"))
async def cmd_ship(m: Message):
    if not fun_allowed(m):
        return
    cid = m.chat.id
    uids = get_chat_active_uids(cid)
    if len(uids) < 2:
        await m.reply("народу мало, шипперить некого")
        return
    pair = random.sample(uids, 2)
    n1 = await get_user_display_name(cid, pair[0])
    n2 = await get_user_display_name(cid, pair[1])
    pct = random.randint(0, 100)
    hearts = "💔" if pct < 30 else ("❤️" * (pct // 20 + 1))
    await m.reply(f"💕 пара дня:\n{n1} + {n2} = {pct}% {hearts}")

@router.message(Command("гей", "gay"))
async def cmd_gay(m: Message):
    if not fun_allowed(m):
        return
    target = get_target_user(m)
    pct = random.randint(0, 100)
    await m.reply(f"{target.first_name} гей на {pct}% 🏳️‍🌈")

@router.message(Command("шлюха"))
async def cmd_slut(m: Message):
    if not fun_allowed(m):
        return
    target = get_target_user(m)
    pct = random.randint(0, 100)
    await m.reply(f"{target.first_name} шлюха на {pct}% 💅")

@router.message(Command("айкью", "iq"))
async def cmd_iq(m: Message):
    if not fun_allowed(m):
        return
    target = get_target_user(m)
    iq_val = random.randint(10, 190)
    comment = "земля пухом 💀" if iq_val < 70 else ("гений комнатный 🧠" if iq_val > 130 else "дефолтный овощ")
    await m.reply(f"{target.first_name} твой IQ: {iq_val} ({comment})")

@router.message(Command("хуй", "dick"))
async def cmd_dick(m: Message):
    if not fun_allowed(m):
        return
    target = get_target_user(m)
    size = random.randint(1, 35)
    bar = "=" * size
    await m.reply(f"🍆 {target.first_name}: 8{bar}D ({size} см)")

@router.message(Command("дуэль", "duel"))
async def cmd_duel(m: Message):
    if not fun_allowed(m):
        return
    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("реплаем ответь на жертву")
        return
    u1 = m.from_user.first_name
    u2 = m.reply_to_message.from_user.first_name
    if m.from_user.id == m.reply_to_message.from_user.id:
        await m.reply("ты с собой собрался стреляться, даун?")
        return
    winner, loser = (u1, u2) if random.random() > 0.5 else (u2, u1)
    wpn = random.choice(["ножа 🔪", "дробовика 💥", "базуки 💣", "пердежа 💨", "трактора 🚜"])
    await m.reply(f"⚔️ {winner} вынес {loser} с помощью {wpn}")

@router.message(Command("казнь"))
async def cmd_kill(m: Message):
    if not fun_allowed(m):
        return
    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("реплаем ответь на жертву казни")
        return
    victim = m.reply_to_message.from_user.first_name
    methods = [
        f"🪓 {victim} был публично обезглавлен на площади",
        f"⚡ {victim} посажен на электрический стул",
        f"🔥 {victim} сожжён на костре инквизиции",
        f"🕳 {victim} сброшен в яму со скорпионами",
        f"🚜 {victim} закатан в асфальт",
    ]
    await m.reply(random.choice(methods))

@router.message(Command("кто", "who"))
async def cmd_who(m: Message):
    if not fun_allowed(m):
        return
    parts = m.text.split(maxsplit=1)
    question = parts[1] if len(parts) > 1 else "лох"
    uids = get_chat_active_uids(m.chat.id)
    if not uids:
        await m.reply("хз, никого нет")
        return
    name = await get_user_display_name(m.chat.id, random.choice(uids))
    await m.reply(f"кто {question}? — {name}, без сомнений")

@router.message(Command("инфа"))
async def cmd_info(m: Message):
    if not fun_allowed(m):
        return
    parts = m.text.split(maxsplit=1)
    q = parts[1] if len(parts) > 1 else "это"
    pct = random.randint(0, 100)
    await m.reply(f"инфа {pct}% что {q}")

@router.message(Command("шар", "8ball"))
async def cmd_ball(m: Message):
    if not fun_allowed(m):
        return
    ans = random.choice([
        "да", "нет", "сто проц", "даже не надейся",
        "скорее да", "вряд ли", "пошел нахуй",
        "без шансов", "определенно", "забей вообще"
    ])
    await m.reply(f"🎱 {ans}")

@router.message(Command("help", "помощь"))
async def cmd_help(m: Message):
    if m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await m.reply(
            "🎮 команды:\n"
            "/adda [префикс] — панель настройки прав (кнопками)\n"
            "/add [префикс] — выдать префикс без прав\n"
            "/rev — снять префикс и все права\n"
            "/typers — топ активных за день\n"
            "/пидор /шип /гей /шлюха /айкью /хуй /дуэль /казнь /кто /инфа /шар\n\n"
            f"⚙️ настройки только в лс: @{BOT_USERNAME}"
        )
    else:
        await m.reply("напиши /start для настройки чатов")

@router.message()
async def dummy_message_handler(m: Message):
    pass


# ==================== ЗАПУСК ====================
async def background_periodic_save():
    while True:
        await asyncio.sleep(120)
        save_data()

async def main():
    global BOT_ID, BOT_USERNAME
    load_data()
    me = await bot.get_me()
    BOT_ID = me.id
    BOT_USERNAME = me.username or ""
    log.info(f"🚀 Бот запущен: @{BOT_USERNAME} [ID: {BOT_ID}]")

    try:
        await bot.send_message(
            OWNER_ID,
            f"🟢 <b>Бот @{BOT_USERNAME} v8 запущен!</b>\n"
            f"• Добавлена интерактивная панель /adda с кнопками настроек прав.\n"
            f"• Добавлена команда /rev для мгновенного снятия прав.\n"
            f"• Исправлен RIGHT_FORBIDDEN."
        )
    except Exception as e:
        log.warning(f"Could not send startup message to owner: {e}")

    asyncio.create_task(background_periodic_save())

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "chat_member",
            "my_chat_member",
            "callback_query",
        ],
    )

if __name__ == "__main__":
    asyncio.run(main())

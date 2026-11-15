"""
🛡 АНТИСНОС v11 — Защита от сноса + Моментальный Анти-Ирис + Скачивание TikTok + Список рангов /admins + Вечный сервер
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

import aiofiles
import aiohttp
from aiohttp import web

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
    FSInputFile,
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
        "anti_raid": True,        # 5+ бан/кик за 1 мин от админов -> снять права + мут + ответ @glicu
        "anti_iris": True,        # Моментальный съем прав у Ириса при .кик неактив
        "anti_spam": True,        # 10+ медиа за 10 сек -> мут + ответ @glicu
        "anti_flood": True,       # 10+ ПОЛНОСТЬЮ одинаковых сообщений за 10 сек -> мут + ответ @glicu
        "spam_text": "@glicu",    # Настраиваемый текст ответа при рейде/спаме/флуде
        "tiktok_dl": True,        # Автоскачивание TikTok видео
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
        return cached[0], cached[1], cached[3]
    
    try:
        m = await bot.get_chat_member(cid, uid)
        is_creator = m.status == ChatMemberStatus.CREATOR
        is_admin = is_creator or m.status == ChatMemberStatus.ADMINISTRATOR
        custom_title = getattr(m, "custom_title", "") or ""
    except Exception:
        is_admin = False
        is_creator = False
        custom_title = ""
        
    admin_cache[(cid, uid)] = (is_admin, is_creator, now, custom_title)
    return is_admin, is_creator, custom_title

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


# ==================== СКАЧИВАНИЕ TIKTOK ====================
TIKTOK_REGEX = re.compile(r"https?://(?:(?:www|vm|vt|m)\.)?tiktok\.com/[^\s]+")

async def download_tiktok(url: str) -> Optional[str]:
    api_url = "https://www.tikwm.com/api/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(api_url, data={"url": url, "hd": "1"}, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return None
                video_url = data.get("data", {}).get("hdplay") or data.get("data", {}).get("play")
                if not video_url:
                    return None
                vid_id = data.get("data", {}).get("id", str(int(time.time())))
                filename = f"tt_{vid_id}_{random.randint(1000, 9999)}.mp4"
                async with session.get(video_url, timeout=aiohttp.ClientTimeout(total=60)) as video_resp:
                    if video_resp.status != 200:
                        return None
                    async with aiofiles.open(filename, "wb") as f:
                        async for chunk in video_resp.content.iter_chunked(8192):
                            await f.write(chunk)
                return filename
    except Exception as e:
        log.error(f"Error downloading TikTok ({url}): {e}")
        return None


# ==================== КОМАНДА /admins — СПИСОК АДМИНОВ И РАНГОВ ====================
@router.message(Command("admins", "админы", "ранги"))
async def cmd_admins_list(m: Message):
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await m.reply("команда работает только в чате")
        return

    cid = m.chat.id
    try:
        admins = await bot.get_chat_administrators(cid)
    except Exception as e:
        await m.reply(f"⚠️ не смог получить список админов: {e}")
        return

    lines = ["👑 <b>Список администрации и рангов в чате:</b>\n"]
    for i, a in enumerate(admins, 1):
        u = a.user
        name = u.first_name or f"id{u.id}"
        un = f"@{u.username}" if u.username else f"id:{u.id}"
        
        # Определяем статус
        if a.status == ChatMemberStatus.CREATOR:
            rank = "👑 Создатель (Владелец чата)"
        else:
            title = getattr(a, "custom_title", "")
            if title:
                rank = f"⭐️ [{title}] (Старший админ / 4 ранг)"
            else:
                rank = "🛡 Администратор"

        bot_mark = " 🤖 [БОТ]" if u.is_bot else ""
        if u.id in KNOWN_IRIS_IDS or (u.username and "iris" in u.username.lower()):
            bot_mark = " 🤖 [ИРИС ЧАТ-МЕНЕДЖЕР]"
        elif u.id == BOT_ID:
            bot_mark = " 🛡 [НАШ БОТ-ЗАЩИТНИК]"

        lines.append(f"{i}. <b>{name}</b> ({un}){bot_mark}\n   └ Ранг: <code>{rank}</code>")

    lines.append("\n<i>Бот отслеживает всех старших админов (4 ранг) и готов снять права при 5+ спаме бан/кик.</i>")
    await m.reply("\n".join(lines), parse_mode="HTML")


# ==================== КЛАВИАТУРА ПРАВ АДМИНИСТРАТОРА (/adda) ====================
async def get_admin_perms_dict(cid: int, uid: int) -> Dict[str, bool]:
    try:
        m = await bot.get_chat_member(cid, uid)
        if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return {
                "promote": getattr(m, "can_promote_members", False),
                "info": getattr(m, "can_change_info", False),
                "delete": getattr(m, "can_delete_messages", False),
                "restrict": getattr(m, "can_restrict_members", False),
                "invite": getattr(m, "can_invite_users", True),
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
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await m.reply("команда работает только в чате")
        return

    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("ответь реплаем на того, кому выдать права:\n<code>/adda [префикс]</code>")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    raw = m.text.split(maxsplit=1)
    prefix = raw[1].strip()[:16] if len(raw) > 1 else "Админ"

    try:
        target_cm = await bot.get_chat_member(cid, target_user.id)
        if target_cm.status == ChatMemberStatus.CREATOR:
            await m.reply("⚠️ это Создатель чата, ему нельзя менять права через бота!")
            return

        await bot.promote_chat_member(
            chat_id=cid,
            user_id=target_user.id,
            can_manage_chat=True,
            can_invite_users=True,
            can_change_info=False,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            can_manage_topics=False,
        )

        if not target_user.is_bot:
            try:
                await bot.set_chat_administrator_custom_title(cid, target_user.id, prefix)
            except TelegramBadRequest as e:
                log.warning(f"custom_title error: {e.message}")
                if "BOT_NOT_PROMOTED_BY_BOT" in e.message or "NOT_ENOUGH_RIGHTS" in e.message:
                    await m.reply(
                        f"⚠️ Внимание: {target_name} ранее был назначен админом СОЗДАТЕЛЕМ чата!\n"
                        f"Чтобы наш бот мог менять ему префикс и права, Создателю нужно сначала снять с него админку в настройках чата, а затем написать <code>/adda {prefix}</code>."
                    )
                    return

        admin_cache.pop((cid, target_user.id), None)
        current_perms = await get_admin_perms_dict(cid, target_user.id)

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
        if "NOT_ENOUGH_RIGHTS" in e.message or "RIGHT_FORBIDDEN" in e.message or "BOT_NOT_PROMOTED_BY_BOT" in e.message:
            await m.reply(
                f"⚠️ Ошибка: Telegram не даёт изменить права этому пользователю.\n"
                f"<b>Причина:</b> Этот пользователь/бот был назначен Создателем чата.\n"
                f"👉 <b>Решение:</b> Сними с него админку в настройках группы вручную, затем напиши в чате <code>/adda {prefix}</code> — тогда наш бот станет его создателем и сможет менять права и снимать их!"
            )
        else:
            await m.reply(f"⚠️ ошибка Telegram: {e.message}")
    except Exception as e:
        log.error(f"Error in /adda: {e}")
        await m.reply(f"⚠️ ошибка: {e}")


@router.callback_query(F.data.startswith("adm_p:"))
async def cb_toggle_admin_perm(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        await cb.answer("только овнер может менять права", show_alert=True)
        return

    parts = cb.data.split(":")
    cid = int(parts[1])
    uid = int(parts[2])
    perm_key = parts[3]

    current_p = await get_admin_perms_dict(cid, uid)
    new_val = not current_p.get(perm_key, False)
    current_p[perm_key] = new_val

    try:
        kwargs = {
            "chat_id": cid,
            "user_id": uid,
            "can_manage_chat": True,
            "can_invite_users": current_p["invite"],
            "can_change_info": current_p["info"],
            "can_delete_messages": current_p["delete"],
            "can_restrict_members": current_p["restrict"],
            "can_pin_messages": current_p["pin"],
            "can_promote_members": current_p["promote"],
            "can_manage_video_chats": current_p["video"],
            "is_anonymous": current_p["anon"],
        }
        if current_p["topics"]:
            kwargs["can_manage_topics"] = True

        await bot.promote_chat_member(**kwargs)
        admin_cache.pop((cid, uid), None)

        try:
            await cb.message.edit_reply_markup(reply_markup=kb_admin_panel(cid, uid, current_p))
        except TelegramBadRequest as e:
            if "message is not modified" in e.message.lower():
                pass
            else:
                raise e

        await cb.answer(f"Право: {'вкл ➕' if new_val else 'выкл ➖'}")

    except TelegramBadRequest as e:
        log.warning(f"TelegramBadRequest in perm toggle: {e.message}")
        if "message is not modified" in e.message.lower():
            await cb.answer()
        elif "RIGHT_FORBIDDEN" in e.message or "NOT_ENOUGH_RIGHTS" in e.message:
            current_p[perm_key] = not new_val
            await cb.answer("⚠️ Telegram не даёт выдать это право (у нашего бота нет этой галочки)", show_alert=True)
        else:
            await cb.answer(f"Ошибка Telegram: {e.message}", show_alert=True)
    except Exception as e:
        log.error(f"Error in perm toggle: {e}")
        await cb.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("adm_revoke:"))
async def cb_admin_revoke(cb: CallbackQuery):
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
        await cb.answer("Не удалось снять права (этот юзер/бот был назначен создателем чата вручную)", show_alert=True)


@router.message(Command("rev", "разжаловать", "снять"))
async def cmd_revoke_handler(m: Message):
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
        await m.reply(
            f"⚠️ не смог снять права у {target_name}!\n"
            f"<b>Причина:</b> Он был назначен админом СОЗДАТЕЛЕМ чата вручную в Telegram. По законам Telegram снять его может только Создатель.\n"
            f"👉 Чтобы наш бот мог управлять им, сними с него админку один раз руками и назначь через <code>/adda</code>."
        )


@router.message(Command("add", "addg"))
async def handle_cmd_add_simple(m: Message):
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

    try:
        await bot.promote_chat_member(
            chat_id=cid,
            user_id=target_user.id,
            can_manage_chat=True,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=True,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            can_manage_topics=False,
        )
        if not target_user.is_bot:
            try:
                await bot.set_chat_administrator_custom_title(cid, target_user.id, prefix)
            except Exception:
                pass

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
        await m.reply(f"⚠️ ошибка Telegram: {e.message}\n(Если юзер уже был админом от Создателя — сними админку вручную и повтори)")
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

        is_admin, is_creator, custom_title = await get_user_status(cid, uid)

        # -------------------------------------------------------------
        # 1. МОМЕНТАЛЬНЫЙ АНТИ-ИРИС: .кик неактив / !кик неактив
        # Удаляет команду сноса + забирает права у Ириса
        # -------------------------------------------------------------
        if cfg["anti_iris"] and m.text:
            text_clean = m.text.lower().strip()
            if (
                text_clean.startswith((".", "!", "/")) and "кик" in text_clean and "неактив" in text_clean
            ) or (
                "кик неактив" in text_clean
            ):
                # Мгновенно удаляем саму команду сноса, чтобы Ирис её не прочитал!
                try:
                    await m.delete()
                except Exception:
                    pass

                # Моментально отбираем права у Ириса
                success = await revoke_all_iris(cid)
                iris_alert[cid] = time.time()
                log.info(f"[{cid}] ОБНАРУЖЕНА КОМАНДА СНОСА: '{m.text}' -> Снятие прав с Ириса: {success}")
                
                if success:
                    try:
                        await bot.send_message(cid, "🛡 команда сноса удалена! права у ириса сняты.")
                    except Exception:
                        pass
                else:
                    try:
                        await bot.send_message(cid, "🛡 команда сноса удалена! снос предотвращён.")
                    except Exception:
                        pass

                await notify_owner(
                    f"🛑 <b>ПРЕДОТВРАЩЁН СНОС НЕАКТИВА!</b>\n"
                    f"Чат: «{chat_title}»\n"
                    f"Кто написал: {user_name} ({username_str})\n"
                    f"Команда: <code>{m.text}</code> (удалена)\n"
                    f"Статус снятия прав с Ириса: {success}"
                )
                return True # блокируем дальнейшую обработку сообщения

        # -------------------------------------------------------------
        # 2. АНТИРЕЙД: БАН / КИК (5+ раз за 1 мин) ТОЛЬКО ОТ АДМИНОВ В ЧАТЕ!
        # Обычных участников не трогает!
        # -------------------------------------------------------------
        if cfg["anti_raid"] and m.text and is_admin:
            text_low = m.text.lower()
            now = time.time()
            triggered = False
            word = ""

            if "бан" in text_low:
                count_ban = text_low.count("бан")
                ban_tracker[cid][uid].extend([now] * count_ban)
                ban_tracker[cid][uid] = clean_timestamps(ban_tracker[cid][uid], 60)
                if len(ban_tracker[cid][uid]) >= 5:
                    triggered = True
                    word = "бан"
                    ban_tracker[cid][uid].clear()

            if not triggered and "кик" in text_low:
                count_kick = text_low.count("кик")
                kick_tracker[cid][uid].extend([now] * count_kick)
                kick_tracker[cid][uid] = clean_timestamps(kick_tracker[cid][uid], 60)
                if len(kick_tracker[cid][uid]) >= 5:
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
                        f"Слово '{word}' 5+ раз за 1 мин."
                    )
                else:
                    demote_ok = await demote_admin(cid, uid)
                    mute_ok = await mute_user_forever(cid, uid)
                    try:
                        await m.reply(spam_reply_text)
                    except Exception:
                        pass
                    await notify_owner(
                        f"🚨 <b>ПРЕДОТВРАЩЁН РЕЙД СТАРШЕГО АДМИНА!</b>\n"
                        f"Чат: «{chat_title}»\n"
                        f"Админ: {user_name} ({username_str}) [ID: <code>{uid}</code>]\n"
                        f"Ранг/Титул: {custom_title or 'Админ'}\n"
                        f"Триггер: 5+ раз слово '{word}' за 1 минуту\n"
                        f"Действие: Сняты все права ({demote_ok}) + МУТ навсегда ({mute_ok}), ответ в чат: {spam_reply_text}"
                    )
                return True

        # -------------------------------------------------------------
        # 3. АНТИСПАМ: СТИКЕРЫ, ГИФКИ, ФОТО (10+ за 10 сек)
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
        # 5. СКАЧИВАНИЕ TIKTOK ВИДЕО БЕЗ ВОДЯНОГО ЗНАКА
        # -------------------------------------------------------------
        if cfg.get("tiktok_dl", True) and m.text:
            match = TIKTOK_REGEX.search(m.text)
            if match:
                tt_url = match.group(0)
                asyncio.create_task(handle_tiktok_message(m, tt_url))

        # -------------------------------------------------------------
        # 6. АНТИ-ССЫЛКИ
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
        # 7. ЗАПРЕЩЁННЫЕ СЛОВА
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
        # 8. ПОЛЬЗОВАТЕЛЬСКИЕ ТРИГГЕРЫ (АВТООТВЕТЫ)
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


async def handle_tiktok_message(m: Message, url: str):
    """Скачивает и отправляет TikTok видео реплаем"""
    try:
        video_file = await download_tiktok(url)
        if video_file and os.path.exists(video_file):
            try:
                await m.reply_video(
                    video=FSInputFile(video_file),
                    caption="🎬 TikTok без водяного знака"
                )
            except Exception as e:
                log.error(f"Error sending tiktok video: {e}")
            finally:
                if os.path.exists(video_file):
                    os.remove(video_file)
    except Exception as e:
        log.error(f"handle_tiktok_message error: {e}")


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


# ==================== НАСТРОЙКИ В ЛС ====================
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
            InlineKeyboardButton(text=f"🛡 Рейд админов (5х): {icon(cfg['anti_raid'])}", callback_data=f"tog:{str_cid}:anti_raid"),
            InlineKeyboardButton(text=f"🛑 Анти-Ирис: {icon(cfg['anti_iris'])}", callback_data=f"tog:{str_cid}:anti_iris"),
        ],
        [
            InlineKeyboardButton(text=f"📸 Спам медиа: {icon(cfg['anti_spam'])}", callback_data=f"tog:{str_cid}:anti_spam"),
            InlineKeyboardButton(text=f"🔄 Антифлуд 10х: {icon(cfg['anti_flood'])}", callback_data=f"tog:{str_cid}:anti_flood"),
        ],
        [
            InlineKeyboardButton(text=f"🎬 Скачивание TikTok: {icon(cfg.get('tiktok_dl', True))}", callback_data=f"tog:{str_cid}:tiktok_dl"),
            InlineKeyboardButton(text=f"🎉 Веселье: {icon(cfg['fun'])}", callback_data=f"tog:{str_cid}:fun"),
        ],
        [
            InlineKeyboardButton(text=f"🔗 Анти-ссылки: {icon(cfg.get('anti_links', False))}", callback_data=f"tog:{str_cid}:anti_links"),
            InlineKeyboardButton(text=f"🤬 Запретка: {icon(cfg.get('anti_bad_words', False))}", callback_data=f"tog:{str_cid}:anti_bad_words"),
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
        "🛡 <b>1. Антирейд админов (бан/кик 5х):</b>\n"
        "— Если админ чата (старший админ/4 ранг Ириса) пишет 'бан' или 'кик' 5+ раз за 1 минуту: бот тихо снимает с него все права админа, мутит навсегда и отвечает реплаем <code>@glicu</code>.\n"
        "— Обычных участников эта проверка не трогает!\n\n"
        "🛑 <b>2. Анти-Ирис (кик неактив):</b>\n"
        "— При команде <code>.кик неактив</code> бот МОМЕНТАЛЬНО удаляет команду и снимает права с Ириса до того, как он успеет кого-то кикнуть!\n\n"
        "👑 <b>3. Список админов и рангов:</b>\n"
        "— Команда <code>/admins</code> (в чате) показывает всех админов, ранги и кастомные титулы.\n\n"
        "🎬 <b>4. Скачивание TikTok:</b>\n"
        "— Отправляешь ссылку на TikTok в чат — бот скачивает видео БЕЗ водяного знака и шлет видео файлом прямо в чат!\n\n"
        "📸 <b>5. Антиспам медиа:</b>\n"
        "— 10+ стикеров, гифок или фото за 10 сек -> мут + ответ реплаем <code>@glicu</code>.\n\n"
        "🔄 <b>6. Антифлуд одинаковыми:</b>\n"
        "— 10+ ПОЛНОСТЬЮ одинаковых сообщений за 10 сек -> мут + ответ <code>@glicu</code>. Разные сообщения не мутит!\n\n"
        "👑 <b>7. Команды админов (только для тебя):</b>\n"
        "— <code>/adda [префикс]</code> (реплаем) — интерактивная панель настройки прав кнопками.\n"
        "— <code>/add [префикс]</code> (реплаем) — серый префикс без прав.\n"
        "— <code>/rev</code> (реплаем) — моментально забрать все права и префикс.\n\n"
        "🎉 <b>8. Троллинг-команды в чате:</b>\n"
        "— /пидор, /шип, /гей, /шлюха, /айкью, /хуй, /дуэль, /казнь, /кто, /инфа, /шар, /typers."
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
            "/admins — список админов и рангов в чате\n"
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


# ==================== ВЕБ-СЕРВЕР ДЛЯ ВЕЧНОЙ ЖИЗНИ (RENDER ANTI-SLEEP) ====================
async def health_check_handler(request):
    return web.Response(text="Bot is ALIVE 24/7!", status=200)

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", health_check_handler)
    app.router.add_get("/health", health_check_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"🌐 Anti-Sleep Web Server запущен на порту {port}")


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
        await start_web_server()
    except Exception as e:
        log.warning(f"Could not start anti-sleep web server: {e}")

    try:
        await bot.send_message(
            OWNER_ID,
            f"🟢 <b>Бот @{BOT_USERNAME} v11 запущен и готов!</b>\n"
            f"• Добавлена команда /admins (ранги и админы чата).\n"
            f"• Моментальное удаление команды сноса неактива.\n"
            f"• Автоскачивание TikTok активно."
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

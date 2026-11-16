"""
🛡 АНТИСНОС v14 — Идеальная защита без единого бага и без ложных срабатываний
• ГИФКИ, ФОТО, ВИДЕО, КРУЖКИ: ПОЛНЫЙ ИММУНИТЕТ, БОТ ИХ НЕ ТРОГАЕТ ВООБЩЕ НИКОГДА!
• Мут ТОЛЬКО за:
  1) ОДИН И ТОТ ЖЕ ТЕКСТ (>10 раз за 10 сек) -> мут на 10 минут, реплай:
     замучен на 10 минут
     @
  2) ОДИН И ТОТ ЖЕ СТИКЕР (>10 раз за 10 сек) -> мут на 10 минут, реплай:
     замучен на 10 минут
• Разные сообщения и разные стикеры (хоть 100 в секунду) НЕ МУТЯТСЯ НИКОГДА!
• Рейд бан/кик (5+ раз за 1 мин) проверяет ТОЛЬКО самостоятельные слова у Старших админов (4 ранг Ириса).
• Автопарсинг админов Ириса (сообщения со ⭐⭐⭐⭐) + команда /admins.
• 100% скачивание TikTok (раскрытие vt/vm + gzip).
• Монотонные часы time.monotonic() + автоочистка памяти каждые 60 секунд (исключено любое суммирование из прошлого).
• Анти-сон веб-сервер для Render.
"""

import asyncio
import hashlib
import html
import json
import logging
import os
import random
import re
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
    ChatJoinRequest,
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
esc = html.escape  # экранирование всего, что вставляется в HTML-сообщения

# ==================== КОНФИГУРАЦИЯ ====================
# Секреты и пути — ТОЛЬКО из переменных окружения (токен в коде хранить нельзя!)
TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("Не задана переменная окружения BOT_TOKEN (токен от @BotFather)")
OWNER_ID = int(os.getenv("OWNER_ID", "7383168722"))
DATA_FILE = os.getenv("DATA_FILE", "bot_data.json")
RAID_REPLY = os.getenv("RAID_REPLY", "@glicu")  # что бот пишет при пресечении рейда админа
MASS_KICK_LIMIT = int(os.getenv("MASS_KICK_LIMIT", "3"))  # сколько киков/банов подряд = снос
MASS_KICK_WINDOW = float(os.getenv("MASS_KICK_WINDOW", "10"))  # за сколько секунд

ANON_ADMIN_ID = 1087968824  # GroupAnonymousBot
CHANNEL_FWD_ID = 777000     # автопересылка из канала

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
    "iris_ranks": {},   # chat_id -> { "5": [user_dict], "4": [user_dict], "3": [...], "2": [...], "1": [...] }
}

# ВЫСОКОТОЧНЫЕ ТРЕКЕРЫ (Используют time.monotonic() — защищены от сбоев системных часов)
# ТЕКСТ: cid -> uid -> text_hash -> [monotonic_timestamps] (ТОЛЬКО ОДИНАКОВЫЙ ТЕКСТ)
flood_text_tracker = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
# СТИКЕРЫ: cid -> uid -> file_unique_id -> [monotonic_timestamps] (ТОЛЬКО ОДИНАКОВЫЙ СТИКЕР)
flood_sticker_tracker = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

# Рейд трекеры (ТОЛЬКО отдельные слова "бан", "кик")
ban_word_tracker = defaultdict(lambda: defaultdict(list))
kick_word_tracker = defaultdict(lambda: defaultdict(list))

iris_alert = {}
admin_kicks = defaultdict(lambda: defaultdict(list))
admin_cache = {}
recent_demote: Dict[Tuple[int, int], float] = {}  # (chat, user) -> monotonic: не снимать права по 100 раз подряд
join_tracker = defaultdict(list)                  # chat -> [monotonic] заявки на вступление
join_pause_until: Dict[int, float] = {}           # chat -> monotonic: пауза автопринятия (щит)
notice_sent: Dict[Tuple[int, str], float] = {}    # антиспам служебных уведомлений владельцу
fun_cooldown: Dict[Tuple[int, int], float] = {}
trigger_cooldown: Dict[Tuple[int, str], float] = {}
tiktok_cooldown: Dict[int, float] = {}
name_cache: Dict[Tuple[int, int], Tuple[str, float]] = {}
_bg_tasks: set = set()
_tiktok_sem: Optional[asyncio.Semaphore] = None

JOIN_DELAYS = [0, 1, 3, 5, 10, 30]  # варианты задержки автопринятия (сек), листаются кнопкой
JOIN_SHIELD_LIMIT = 15              # столько заявок...
JOIN_SHIELD_WINDOW = 30             # ...за столько секунд = рейд заявок
JOIN_SHIELD_PAUSE = 600             # на сколько секунд ставим автопринятие на паузу

# Какие переключатели вообще разрешено менять кнопками
TOGGLE_KEYS = {
    "anti_raid", "anti_iris", "anti_flood_text", "anti_flood_sticker", "tiktok_dl", "fun",
    "anti_links", "anti_bad_words", "triggers_enabled",
    "auto_approve", "join_shield", "welcome", "clean_service", "notify_owner",
}


def spawn(coro) -> "asyncio.Task":
    """create_task с удержанием ссылки (иначе задачу может собрать GC на полпути)."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


def get_tiktok_sem() -> asyncio.Semaphore:
    global _tiktok_sem
    if _tiktok_sem is None:
        _tiktok_sem = asyncio.Semaphore(2)
    return _tiktok_sem


class FormStates(StatesGroup):
    waiting_for_spam_text = State()
    waiting_for_trigger_word = State()
    waiting_for_trigger_reply = State()
    waiting_for_bad_word = State()
    waiting_for_welcome_text = State()


# ==================== ДЕФОЛТЫ И СОХРАНЕНИЕ ====================
def default_settings() -> dict:
    return {
        "anti_raid": True,          # 5+ бан/кик за 1 мин от админов/4 ранга -> снять права + мут + ответ @glicu
        "anti_iris": True,          # Моментальный перехват и удаление .кик неактив
        "anti_flood_text": True,    # >10 ОДИНАКОВЫХ сообщений за 10 сек -> мут на 10 мин
        "anti_flood_sticker": True, # >10 ОДИНАКОВЫХ стикеров за 10 сек -> мут на 10 мин
        "tiktok_dl": True,          # Скачивание TikTok видео без водяного знака
        "anti_links": False,        # Удаление t.me ссылок от не-админов
        "anti_bad_words": False,    # Удаление запрещенных слов
        "triggers_enabled": True,   # Пользовательские триггеры
        "fun": True,                # Веселье
        "auto_approve": True,       # Автопринятие заявок на вступление
        "approve_delay": 1,         # Через сколько секунд принимать заявку (0 = сразу)
        "join_shield": True,        # Пауза автопринятия при наплыве заявок (рейд)
        "welcome": False,           # Приветствие новичков
        "welcome_text": "👋 Добро пожаловать, {name}!",
        "clean_service": False,     # Удалять служебные «вступил/вышел»
        "notify_owner": True,       # Присылать владельцу отчёты о срабатываниях
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
    """Атомарная запись: сначала во временный файл, потом замена (сбой посреди записи не убьёт базу)."""
    try:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        log.error(f"Error saving data: {e}")

def load_data():
    global db
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("корень JSON не объект")
        for k in ["settings", "chats", "iris_ids", "stats_daily", "pidor_daily", "triggers", "bad_words", "iris_ranks"]:
            db[k] = loaded.get(k, {})
    except Exception as e:
        log.error(f"Error loading data: {e}")
        try:  # битый файл не затираем молча — сохраняем копию
            shutil.copy(DATA_FILE, DATA_FILE + ".corrupt")
            log.error(f"Копия повреждённой базы: {DATA_FILE}.corrupt")
        except Exception:
            pass

def prune_stats(keep_days: int = 14):
    """Статистика по дням не должна расти бесконечно."""
    cutoff = (datetime.now(timezone.utc) + timedelta(hours=3) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    for days in db.get("stats_daily", {}).values():
        for day in [d for d in days if d < cutoff]:
            del days[day]

def get_today_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")

async def notify_owner(text: str):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="HTML")
    except Exception as e:
        log.warning(f"Could not send log to owner {OWNER_ID}: {e}")

async def notify_chat(cid: int, text: str):
    """Отчёт владельцу по конкретному чату — с учётом переключателя «Уведомления»."""
    if get_chat_cfg(cid).get("notify_owner", True):
        await notify_owner(text)

async def notify_once(cid: int, key: str, text: str, every: float = 3600.0):
    now = time.monotonic()
    if now - notice_sent.get((cid, key), -1e9) < every:
        return
    notice_sent[(cid, key)] = now
    await notify_chat(cid, text)

def render_welcome(cfg: dict, user) -> str:
    name = (user.first_name or "друг").strip() or "друг"
    tpl = cfg.get("welcome_text") or "👋 Добро пожаловать, {name}!"
    return tpl.replace("{name}", name)[:1000]  # replace, а не format: шаблон вводит человек


# ==================== УТИЛИТЫ АДМИНИСТРИРОВАНИЯ ====================
async def get_user_status(cid: int, uid: int) -> Tuple[bool, bool, str]:
    now = time.monotonic()
    cached = admin_cache.get((cid, uid))
    if cached and now - cached[2] < 20:
        return cached[0], cached[1], cached[3]

    try:
        m = await bot.get_chat_member(cid, uid)
    except Exception:
        return False, False, ""  # сбой API не кэшируем, иначе админа на 20 сек примем за обычного

    is_creator = m.status == ChatMemberStatus.CREATOR
    is_admin = is_creator or m.status == ChatMemberStatus.ADMINISTRATOR
    custom_title = getattr(m, "custom_title", "") or ""
    admin_cache[(cid, uid)] = (is_admin, is_creator, now, custom_title)
    return is_admin, is_creator, custom_title

def is_user_iris_rank4_or_admin(cid: int, uid: int, username: str = "", name: str = "", is_tg_admin: bool = False) -> bool:
    if is_tg_admin:
        return True
    ranks = db.get("iris_ranks", {}).get(str(cid), {})
    un_in = (username or "").lower().lstrip("@")
    nm_in = (name or "").strip().lower()
    for r in ("5", "4"):
        for item in ranks.get(r, []):
            if item.get("id") and item["id"] == uid:
                return True
            item_un = (item.get("username") or "").lower()
            if item_un:
                if un_in and item_un == un_in:
                    return True
            # по имени сверяем только записи без юзернейма (имя легко подделать)
            elif nm_in and (item.get("name") or "").strip().lower() == nm_in:
                return True
    return False

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

async def get_bot_admin_rights(cid: int) -> Dict[str, bool]:
    """Получает реальные права нашего бота в чате, чтобы не вызывать RIGHT_FORBIDDEN"""
    try:
        m = await bot.get_chat_member(cid, BOT_ID)
        if m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return {
                "can_promote_members": getattr(m, "can_promote_members", False),
                "can_change_info": getattr(m, "can_change_info", False),
                "can_delete_messages": getattr(m, "can_delete_messages", False),
                "can_restrict_members": getattr(m, "can_restrict_members", False),
                "can_invite_users": getattr(m, "can_invite_users", False),
                "can_pin_messages": getattr(m, "can_pin_messages", False),
                "can_manage_video_chats": getattr(m, "can_manage_video_chats", False),
                "can_manage_topics": getattr(m, "can_manage_topics", False),
            }
    except Exception as e:
        log.warning(f"get_bot_admin_rights error: {e}")
    return {
        "can_promote_members": False, "can_change_info": False,
        "can_delete_messages": False, "can_restrict_members": False,
        "can_invite_users": False, "can_pin_messages": False,
        "can_manage_video_chats": False, "can_manage_topics": False,
    }

async def get_admin_perms_dict(cid: int, uid: int) -> Dict[str, bool]:
    """Получает текущие права целевого пользователя в чате"""
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
    """Интерфейс панели прав администратора строго по скриншоту пользователя"""
    def sym(flag: bool) -> str:
        return "➕" if flag else "➖"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{sym(p.get('promote', False))} Назначение админов", callback_data=f"adm_p:{cid}:{uid}:promote"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p.get('info', False))} Профиль группы", callback_data=f"adm_p:{cid}:{uid}:info"),
            InlineKeyboardButton(text=f"{sym(p.get('delete', False))} Удаление смс", callback_data=f"adm_p:{cid}:{uid}:delete"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p.get('restrict', False))} Баны", callback_data=f"adm_p:{cid}:{uid}:restrict"),
            InlineKeyboardButton(text=f"{sym(p.get('invite', False))} Инвайты", callback_data=f"adm_p:{cid}:{uid}:invite"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p.get('pin', False))} Закрепы", callback_data=f"adm_p:{cid}:{uid}:pin"),
            InlineKeyboardButton(text=f"{sym(p.get('video', False))} Трансляции", callback_data=f"adm_p:{cid}:{uid}:video"),
        ],
        [
            InlineKeyboardButton(text=f"{sym(p.get('anon', False))} Анонимность", callback_data=f"adm_p:{cid}:{uid}:anon"),
            InlineKeyboardButton(text=f"{sym(p.get('topics', False))} Теги", callback_data=f"adm_p:{cid}:{uid}:topics"),
        ],
        [
            InlineKeyboardButton(text="Снять", callback_data=f"adm_revoke:{cid}:{uid}"),
        ]
    ])

PERM_NAMES_RU = {
    "promote": "Назначение админов",
    "info": "Профиль группы",
    "delete": "Удаление смс",
    "restrict": "Баны",
    "invite": "Инвайты",
    "pin": "Закрепы",
    "video": "Трансляции",
    "anon": "Анонимность",
    "topics": "Теги (Управление темами)",
}

PERM_BOT_FIELDS = {
    "promote": "can_promote_members",
    "info": "can_change_info",
    "delete": "can_delete_messages",
    "restrict": "can_restrict_members",
    "invite": "can_invite_users",
    "pin": "can_pin_messages",
    "video": "can_manage_video_chats",
    "topics": "can_manage_topics",
}

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

async def mute_user_minutes(cid: int, uid: int, minutes: int = 10) -> bool:
    try:
        until = int(time.time()) + (minutes * 60)
        await bot.restrict_chat_member(cid, uid, permissions=MUTE_ALL_PERMS, until_date=until)
        return True
    except Exception as e:
        log.warning(f"mute {minutes}m failed for {uid} in {cid}: {e}")
        return False

async def mute_user_forever(cid: int, uid: int) -> bool:
    try:
        await bot.restrict_chat_member(cid, uid, permissions=MUTE_ALL_PERMS, until_date=0)
        return True
    except Exception as e:
        log.warning(f"mute forever failed for {uid} in {cid}: {e}")
        return False

def _iris_like(uid: int, username: str, name: str, is_bot: bool) -> bool:
    """Ирис — это БОТ. Обычного человека с «iris» в нике/имени за Ириса не считаем."""
    if uid in KNOWN_IRIS_IDS:
        return True
    un = (username or "").lower().lstrip("@")
    if un in KNOWN_IRIS_USERNAMES:
        return True
    if is_bot:
        return "iris" in un or "ирис" in (name or "").lower()
    return False

async def scan_and_cache_iris(cid: int) -> List[int]:
    str_cid = str(cid)
    ids = db["iris_ids"].setdefault(str_cid, [])
    for i_id in KNOWN_IRIS_IDS:
        if i_id not in ids:
            ids.append(i_id)
    try:
        admins = await bot.get_chat_administrators(cid)
        for adm in admins:
            u = adm.user
            if _iris_like(u.id, u.username or "", u.first_name or "", u.is_bot) and u.id not in ids:
                ids.append(u.id)
        save_data()
    except Exception as e:
        log.warning(f"scan iris error in {cid}: {e}")
    return ids

async def revoke_all_iris(cid: int) -> bool:
    iris_list = await scan_and_cache_iris(cid)
    any_success = False
    for iris_id in iris_list:
        ok = await demote_admin(cid, iris_id)
        if ok:
            any_success = True
            log.info(f"[{cid}] Сняты права у Ириса ID={iris_id}")
    return any_success

def is_user_iris(cid: int, uid: int, username: str = "", name: str = "", is_bot: bool = False) -> bool:
    if uid in db.get("iris_ids", {}).get(str(cid), []):
        return True
    return _iris_like(uid, username, name, is_bot)


# ==================== АВТОПАРСИНГ СООБЩЕНИЙ ИРИСА (РАНГИ) ====================
def parse_iris_staff_message(cid: int, text: str):
    str_cid = str(cid)
    low = text.lower()
    # это должен быть именно список стаффа, а не любое сообщение со звездой
    if "⭐" not in text or not any(w in low for w in ("админ", "модер", "создател")):
        return False

    lines = text.split("\n")
    current_rank = None
    ranks_data = {"5": [], "4": [], "3": [], "2": [], "1": []}

    for line in lines:
        l = line.strip()
        if not l:
            continue

        if "⭐⭐⭐⭐⭐" in l or "создатель" in l.lower():
            current_rank = "5"
            continue
        elif "⭐⭐⭐⭐" in l or "старший админ" in l.lower():
            current_rank = "4"
            continue
        elif "⭐⭐⭐" in l or "младший админ" in l.lower():
            current_rank = "3"
            continue
        elif "⭐⭐" in l or "модератор" in l.lower():
            current_rank = "2"
            continue
        elif "⭐" in l or "младший модер" in l.lower():
            current_rank = "1"
            continue

        if current_rank:
            cleaned = re.sub(r"^[🎾🏐👑🔹🔸▫️•\-\s]+", "", l).strip()
            if not cleaned:
                continue

            un_match = re.search(r"@([A-Za-z0-9_]+)", cleaned)
            if not un_match:
                un_match = re.search(r"t\.me/([A-Za-z0-9_]+)", cleaned)
            
            un = un_match.group(1) if un_match else ""
            display_name = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
            display_name = re.sub(r"@([A-Za-z0-9_]+)", "", display_name).strip()
            if not display_name and un:
                display_name = un

            ranks_data[current_rank].append({
                "name": display_name[:30],
                "username": un,
                "id": None,
                "updated_at": int(time.time())
            })

    total_found = sum(len(v) for v in ranks_data.values())
    if total_found > 0:
        db.setdefault("iris_ranks", {})[str_cid] = ranks_data
        save_data()
        log.info(f"[{cid}] Автопарсинг Ириса успешно сохранил {total_found} админов!")
        return True
    return False


# ==================== 100% TIKTOK DOWNLOADER ====================
TIKTOK_REGEX = re.compile(r"https?://(?:(?:www|vm|vt|m)\.)?tiktok\.com/[^\s]+")
MAX_TT_BYTES = 49 * 1024 * 1024  # лимит Telegram для бота — 50 МБ

async def download_tiktok(url: str) -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Encoding": "gzip, deflate",
    }
    timeout = aiohttp.ClientTimeout(total=45)
    filename: Optional[str] = None
    ok = False

    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            target_url = url
            host = (urlparse(url).hostname or "").lower()
            if host in ("vt.tiktok.com", "vm.tiktok.com", "m.tiktok.com"):
                try:
                    async with session.get(url, allow_redirects=True) as head_resp:
                        target_url = str(head_resp.url)
                except Exception as e:
                    log.warning(f"Unshorten error: {e}")

            api_url = "https://www.tikwm.com/api/"
            async with session.post(api_url, data={"url": target_url, "hd": "1"}) as resp:
                data = await resp.json(content_type=None)

            if not isinstance(data, dict) or data.get("code") != 0:
                return None
            info = data.get("data") or {}
            video_url = info.get("hdplay") or info.get("play")
            if not video_url or not str(video_url).startswith("https://"):
                return None

            vid_id = re.sub(r"\D", "", str(info.get("id", ""))) or str(int(time.time()))
            filename = f"tt_{vid_id[:20]}_{random.randint(100, 999)}.mp4"

            async with session.get(video_url) as video_resp:
                if video_resp.status != 200:
                    return None
                if (video_resp.content_length or 0) > MAX_TT_BYTES:
                    return None
                total = 0
                async with aiofiles.open(filename, "wb") as f:
                    async for chunk in video_resp.content.iter_chunked(16384):
                        total += len(chunk)
                        if total > MAX_TT_BYTES:
                            return None
                        await f.write(chunk)
            ok = True
            return filename
    except Exception as e:
        log.error(f"Error in download_tiktok: {e}")
        return None
    finally:
        if not ok and filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                pass

async def handle_tiktok_message(m: Message, url: str):
    try:
        uid = m.from_user.id if m.from_user else 0
        now = time.monotonic()
        if now - tiktok_cooldown.get(uid, -1e9) < 10:
            return  # не даём одному человеку забить бота ссылками
        tiktok_cooldown[uid] = now

        async with get_tiktok_sem():
            video_file = await download_tiktok(url)
        if video_file and os.path.exists(video_file):
            try:
                await m.reply_video(
                    video=FSInputFile(video_file),
                    caption="🎬 <b>TikTok без водяного знака</b>",
                    parse_mode="HTML"
                )
            except Exception as e:
                log.error(f"Error sending tiktok video: {e}")
            finally:
                if os.path.exists(video_file):
                    os.remove(video_file)
    except Exception as e:
        log.error(f"handle_tiktok_message error: {e}")


# ==================== ГЛОБАЛЬНЫЙ МИДДЛВАРЬ ЗАЩИТЫ ====================
# Команда сноса — только в НАЧАЛЕ сообщения (так её и читает Ирис), а не «где-то в тексте»
ANTI_IRIS_RE = re.compile(r"^\s*(?:ирис\s*[,:]?\s*)?[.!/]?\s*(?:кик|kick)\s+неактив", re.IGNORECASE)
# Отдельное слово; «банан», «забавно», «.банально» — не считаются
BAN_RE = re.compile(r"(?<!\w)(?:бан|ban)(?!\w)", re.IGNORECASE)
KICK_RE = re.compile(r"(?<!\w)(?:кик|kick)(?!\w)", re.IGNORECASE)
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")


class GlobalShieldMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if isinstance(event, Message) and event.chat and event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            try:
                blocked = await self.process_group_message(event)
            except Exception:
                # сбой защиты не должен класть весь бот: логируем и пропускаем сообщение дальше
                log.exception("Ошибка в защите чата")
                blocked = False
            if blocked:
                return
        return await handler(event, data)

    async def _punish_flood(self, m: Message, cid: int, uid: int, is_creator: bool, chat_title: str,
                            user_name: str, username_str: str, kind: str, sample: str = ""):
        is_text = kind == "text"
        if is_creator:
            try:
                await m.reply("замучен на 10 минут\n@\n(ты создатель чата)" if is_text else "замучен на 10 минут\n(ты создатель)")
            except Exception:
                pass
            return

        muted = await mute_user_minutes(cid, uid, minutes=10)
        if muted:  # не врём «замучен», если мут не прошёл
            try:
                await m.reply("замучен на 10 минут\n@" if is_text else "замучен на 10 минут")
            except Exception:
                pass

        if is_text:
            head = "🔇 <b>МУТ НА 10 МИНУТ ЗА СПАМ ТЕКСТОМ</b>" if muted else "⚠️ <b>СПАМ ТЕКСТОМ — замутить не вышло</b>"
            reason = (f"<b>Текст спама:</b> <code>{esc(sample[:80])}</code>\n"
                      f"<b>Повторено:</b> >10 раз за 10 секунд")
        else:
            head = "📸 <b>МУТ НА 10 МИНУТ ЗА СПАМ СТИКЕРОМ</b>" if muted else "⚠️ <b>СПАМ СТИКЕРОМ — замутить не вышло</b>"
            reason = "<b>Причина:</b> один и тот же стикер отправлен >10 раз за 10 секунд"
        tail = "" if muted else "\n<i>Нарушитель — админ или у бота нет права банить.</i>"
        await notify_chat(
            cid,
            f"{head}\n"
            f"<b>Чат:</b> «{esc(chat_title)}»\n"
            f"<b>Нарушитель:</b> {esc(user_name)} ({esc(username_str)}) [ID: <code>{uid}</code>]\n"
            f"{reason}{tail}"
        )

    async def process_group_message(self, m: Message) -> bool:
        cid = m.chat.id
        str_cid = str(cid)
        chat_title = m.chat.title or f"Чат {cid}"
        cfg = get_chat_cfg(cid)

        if db["chats"].get(str_cid) != chat_title:
            db["chats"][str_cid] = chat_title

        # ---- служебные сообщения: приветствие новичков и уборка «вступил/вышел»
        if m.new_chat_members or m.left_chat_member:
            if cfg.get("welcome") and m.new_chat_members:
                for u in m.new_chat_members:
                    if u.is_bot:
                        continue
                    try:
                        await bot.send_message(cid, render_welcome(cfg, u))
                    except Exception as e:
                        log.warning(f"welcome error in {cid}: {e}")
            if cfg.get("clean_service"):
                try:
                    await m.delete()
                except Exception:
                    pass
                return True
            return False

        if not m.from_user or m.from_user.id == BOT_ID:
            return False

        uid = m.from_user.id
        user_name = m.from_user.first_name or "Безымянный"
        username_str = f"@{m.from_user.username}" if m.from_user.username else f"id:{uid}"
        is_iris = is_user_iris(cid, uid, m.from_user.username or "", user_name, m.from_user.is_bot)

        # ---- автопарсинг списка админов Ириса (⭐⭐⭐⭐) — только от настоящего Ириса
        if m.text and is_iris and "⭐" in m.text:
            parse_iris_staff_message(cid, m.text)

        # ---- боты и служебные аккаунты (анонимный админ, автопересылка канала) не трогаем
        if m.from_user.is_bot or uid in (ANON_ADMIN_ID, CHANNEL_FWD_ID):
            return False

        # ---- статистика тайперов за день
        today = get_today_str()
        day_stats = db["stats_daily"].setdefault(str_cid, {}).setdefault(today, {})
        str_uid = str(uid)
        day_stats[str_uid] = day_stats.get(str_uid, 0) + 1

        is_tg_admin, is_creator, _title = await get_user_status(cid, uid)
        is_owner = uid == OWNER_ID
        now = time.monotonic()

        # -----------------------------------------------------------------------------
        # ИММУНИТЕТ ДЛЯ ВСЕХ МЕДИА (гифки, фото, видео, кружочки, голосовые)
        # -----------------------------------------------------------------------------
        if m.animation or m.video or m.video_note or m.photo or m.document or m.voice or m.audio:
            return False

        # -------------------------------------------------------------
        # 1. МОМЕНТАЛЬНЫЙ АНТИ-ИРИС: «.кик неактив» / «!кик неактив» / «кик неактив»
        # Создателя чата и владельца бота не трогаем.
        # -------------------------------------------------------------
        if cfg.get("anti_iris", True) and m.text and not is_owner and not is_creator:
            if ANTI_IRIS_RE.match(ZERO_WIDTH_RE.sub("", m.text)):
                try:
                    await m.delete()
                except Exception:
                    pass

                success = await revoke_all_iris(cid)
                iris_alert[cid] = time.time()
                log.info(f"[{cid}] КОМАНДА СНОСА НЕАКТИВА -> удалена, права Ириса сняты: {success}")

                try:
                    note = " Права у Ириса отозваны." if success else ""
                    await bot.send_message(cid, f"🛡 <b>Команда сноса удалена!{note}</b>", parse_mode="HTML")
                except Exception:
                    pass

                await notify_chat(
                    cid,
                    f"🛑 <b>СНОС НЕАКТИВА ПРЕДОТВРАЩЁН</b>\n"
                    f"<b>Чат:</b> «{esc(chat_title)}»\n"
                    f"<b>Кто отправил:</b> {esc(user_name)} ({esc(username_str)}) [ID: <code>{uid}</code>]\n"
                    f"<b>Команда:</b> <code>{esc(m.text[:200])}</code> (удалена)\n"
                    f"<b>Снятие прав с Ириса:</b> {'Успешно' if success else 'не вышло (Ирис назначен Создателем)'}"
                )
                return True

        # -------------------------------------------------------------
        # 2. АНТИФЛУД ТЕКСТОМ: только ПОЛНОСТЬЮ ОДИНАКОВОЕ сообщение (>10 за 10 сек)
        # -------------------------------------------------------------
        if cfg.get("anti_flood_text", True) and m.text and not is_owner:
            msg_exact = m.text.strip()
            if msg_exact:
                msg_hash = hashlib.sha256(msg_exact.encode("utf-8")).hexdigest()
                cutoff_10 = now - 10.0
                valid_ts = [t for t in flood_text_tracker[cid][uid][msg_hash] if t > cutoff_10]
                valid_ts.append(now)
                flood_text_tracker[cid][uid][msg_hash] = valid_ts

                if len(valid_ts) > 10:
                    flood_text_tracker[cid][uid].clear()
                    await self._punish_flood(m, cid, uid, is_creator, chat_title, user_name, username_str,
                                             "text", msg_exact)
                    return True

        # -------------------------------------------------------------
        # 3. АНТИСПАМ СТИКЕРАМИ: только ОДИН И ТОТ ЖЕ стикер (>10 за 10 сек)
        # -------------------------------------------------------------
        if cfg.get("anti_flood_sticker", True) and m.sticker and not is_owner:
            stk_id = m.sticker.file_unique_id
            cutoff_10 = now - 10.0
            valid_stk = [t for t in flood_sticker_tracker[cid][uid][stk_id] if t > cutoff_10]
            valid_stk.append(now)
            flood_sticker_tracker[cid][uid][stk_id] = valid_stk

            if len(valid_stk) > 10:
                flood_sticker_tracker[cid][uid].clear()
                await self._punish_flood(m, cid, uid, is_creator, chat_title, user_name, username_str, "sticker")
                return True

        # -------------------------------------------------------------
        # 4. АНТИРЕЙД: «бан» / «кик» 5+ раз за минуту (отдельные слова!)
        # Следим за админами и за стаффом 4-5 ранга Ириса. Создатель и владелец бота — вне подозрений.
        # -------------------------------------------------------------
        if cfg.get("anti_raid", True) and m.text and not is_creator and not is_owner:
            if is_user_iris_rank4_or_admin(cid, uid, m.from_user.username or "", user_name, is_tg_admin):
                text_low = m.text.lower()
                ban_words = BAN_RE.findall(text_low)
                kick_words = KICK_RE.findall(text_low)

                triggered = False
                action_word = ""
                cutoff_60 = now - 60.0

                if ban_words:
                    valid_bans = [t for t in ban_word_tracker[cid][uid] if t > cutoff_60]
                    valid_bans.extend([now] * len(ban_words))
                    ban_word_tracker[cid][uid] = valid_bans
                    if len(valid_bans) >= 5:
                        triggered = True
                        action_word = "бан"
                        ban_word_tracker[cid][uid].clear()

                if not triggered and kick_words:
                    valid_kicks = [t for t in kick_word_tracker[cid][uid] if t > cutoff_60]
                    valid_kicks.extend([now] * len(kick_words))
                    kick_word_tracker[cid][uid] = valid_kicks
                    if len(valid_kicks) >= 5:
                        triggered = True
                        action_word = "кик"
                        kick_word_tracker[cid][uid].clear()

                if triggered:
                    demote_ok = await demote_admin(cid, uid)
                    mute_ok = await mute_user_forever(cid, uid)
                    try:
                        await m.reply(RAID_REPLY)
                    except Exception:
                        pass
                    await notify_chat(
                        cid,
                        f"🚨 <b>ПРЕДОТВРАЩЁН РЕЙД АДМИНИСТРАТОРА</b>\n"
                        f"<b>Чат:</b> «{esc(chat_title)}»\n"
                        f"<b>Нарушитель:</b> {esc(user_name)} ({esc(username_str)}) [ID: <code>{uid}</code>]\n"
                        f"<b>Триггер:</b> 5+ раз слово '{action_word}' за 1 минуту\n"
                        f"<b>Права сняты:</b> {'да' if demote_ok else 'нет'} | <b>Мут навсегда:</b> {'да' if mute_ok else 'нет'}"
                    )
                    return True

        # -------------------------------------------------------------
        # 5. СКАЧИВАНИЕ TIKTOK БЕЗ ВОДЯНОГО ЗНАКА
        # -------------------------------------------------------------
        if cfg.get("tiktok_dl", True) and m.text:
            tt_match = TIKTOK_REGEX.search(m.text)
            if tt_match:
                spawn(handle_tiktok_message(m, tt_match.group(0)))

        # -------------------------------------------------------------
        # 6. АНТИ-ССЫЛКИ (не трогаем админов и владельца)
        # -------------------------------------------------------------
        if cfg.get("anti_links") and m.text and not is_tg_admin and not is_owner:
            lo = m.text.lower()
            if "t.me/" in lo or "telegram.me/" in lo or "telegram.dog/" in lo:
                try:
                    await m.delete()
                except Exception:
                    pass
                return True

        # -------------------------------------------------------------
        # 7. ЗАПРЕЩЁННЫЕ СЛОВА
        # -------------------------------------------------------------
        if cfg.get("anti_bad_words") and m.text and not is_tg_admin and not is_owner:
            lo = m.text.lower()
            for bw in db.get("bad_words", {}).get(str_cid, []):
                if bw.lower() in lo:
                    try:
                        await m.delete()
                    except Exception:
                        pass
                    return True

        # -------------------------------------------------------------
        # 8. ПОЛЬЗОВАТЕЛЬСКИЕ ТРИГГЕРЫ (автоответы, не чаще раза в 3 сек на триггер)
        # -------------------------------------------------------------
        if cfg.get("triggers_enabled", True) and m.text:
            triggers = db.get("triggers", {}).get(str_cid, {})
            text_low = m.text.lower().strip()
            for trig_word, trig_reply in triggers.items():
                if trig_word.lower() in text_low:
                    key = (cid, trig_word)
                    if now - trigger_cooldown.get(key, -1e9) >= 3:
                        trigger_cooldown[key] = now
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
    chat_title = event.chat.title or f"Чат {cid}"
    cfg = get_chat_cfg(cid)

    if not cfg.get("anti_iris", True):
        return

    target = event.new_chat_member
    # Снос = кики/баны. Муты (RESTRICTED) сносом не считаем.
    if target.status not in (ChatMemberStatus.KICKED, ChatMemberStatus.LEFT):
        return

    actor = event.from_user
    # сам вышел / бот / владелец бота — не наш случай
    if not actor or actor.id in (BOT_ID, OWNER_ID, target.user.id):
        return

    iris_detected = is_user_iris(cid, actor.id, actor.username or "", actor.first_name or "", actor.is_bot)
    alert_active = (time.time() - iris_alert.get(cid, 0)) < 600

    mono = time.monotonic()
    kicks = [t for t in admin_kicks[cid][actor.id] if t > mono - MASS_KICK_WINDOW]
    kicks.append(mono)
    admin_kicks[cid][actor.id] = kicks
    mass_kick = len(kicks) >= MASS_KICK_LIMIT

    if not ((alert_active and iris_detected) or mass_kick):
        return
    # при сносе событий сотни — реагируем один раз, а не на каждый кик
    if mono - recent_demote.get((cid, actor.id), -1e9) < 30:
        return

    _, actor_is_creator, _ = await get_user_status(cid, actor.id)
    if actor_is_creator:
        return

    recent_demote[(cid, actor.id)] = mono
    admin_kicks[cid][actor.id] = []
    iris_alert.pop(cid, None)
    success = await demote_admin(cid, actor.id)

    actor_mention = f"@{actor.username}" if actor.username else (actor.first_name or f"id{actor.id}")
    if success:
        try:
            await bot.send_message(cid, f"🛡 снёс права у {actor_mention}. снос предотвращён.")
        except Exception:
            pass
        await notify_chat(
            cid,
            f"🛑 <b>СНОС ЧАТА ПРЕДОТВРАЩЁН!</b>\n"
            f"<b>Чат:</b> «{esc(chat_title)}»\n"
            f"<b>Нарушитель:</b> {esc(actor_mention)} [ID: <code>{actor.id}</code>]\n"
            f"<b>Действие:</b> Сняты все права администратора!"
        )
    else:
        await notify_chat(
            cid,
            f"🚨 <b>ПОХОЖЕ НА СНОС, но права снять НЕ УДАЛОСЬ</b>\n"
            f"<b>Чат:</b> «{esc(chat_title)}»\n"
            f"<b>Кто:</b> {esc(actor_mention)} [ID: <code>{actor.id}</code>]\n"
            f"Бот снимает права только с тех, кого назначил сам. Сними вручную!"
        )


# ==================== АВТОПРИНЯТИЕ ЗАЯВОК НА ВСТУПЛЕНИЕ ====================
@router.chat_join_request()
async def on_join_request(event: ChatJoinRequest):
    cid = event.chat.id
    cfg = get_chat_cfg(cid)
    chat_title = event.chat.title or f"Чат {cid}"
    if db["chats"].get(str(cid)) != chat_title:
        db["chats"][str(cid)] = chat_title  # чат появится в списке настроек

    if not cfg.get("auto_approve", True):
        return  # автопринятие выключено — заявки решают админы вручную

    now = time.monotonic()

    # щит: наплыв заявок = рейд, ставим автопринятие на паузу
    if cfg.get("join_shield", True):
        recent = [t for t in join_tracker[cid] if t > now - JOIN_SHIELD_WINDOW]
        recent.append(now)
        join_tracker[cid] = recent
        if len(recent) >= JOIN_SHIELD_LIMIT and join_pause_until.get(cid, 0) <= now:
            join_pause_until[cid] = now + JOIN_SHIELD_PAUSE
            log.warning(f"[{cid}] наплыв заявок — автопринятие на паузе")
            await notify_chat(
                cid,
                f"🛑 <b>РЕЙД ЗАЯВОК</b>\n"
                f"<b>Чат:</b> «{esc(chat_title)}»\n"
                f"{len(recent)} заявок за {JOIN_SHIELD_WINDOW} сек. Автопринятие на паузе "
                f"{JOIN_SHIELD_PAUSE // 60} мин, заявки висят — реши вручную.\n"
                f"Снять паузу раньше: /start → чат → «Снять паузу»."
            )

    if join_pause_until.get(cid, 0) > now:
        return

    try:
        delay = max(0, min(int(cfg.get("approve_delay", 1)), 60))
    except (TypeError, ValueError):
        delay = 1
    if delay:
        await asyncio.sleep(delay)

    # пока ждали, могли выключить автопринятие или включиться пауза
    if not get_chat_cfg(cid).get("auto_approve", True) or join_pause_until.get(cid, 0) > time.monotonic():
        return

    try:
        await bot.approve_chat_join_request(chat_id=cid, user_id=event.from_user.id)
        log.info(f"[{cid}] заявка принята: {event.from_user.id}")
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "already_participant" in msg or "hide_requester_missing" in msg:
            log.info(f"[{cid}] заявка {event.from_user.id} уже обработана")
        else:
            log.warning(f"[{cid}] не удалось принять заявку {event.from_user.id}: {e}")
            await notify_once(
                cid, "approve_fail",
                f"⚠️ <b>Не могу принять заявку</b> в «{esc(chat_title)}»\n"
                f"<code>{esc(getattr(e, 'message', str(e)))}</code>\n"
                f"Проверь: бот — админ с правом «Приглашение пользователей»."
            )
    except Exception:
        log.exception(f"[{cid}] ошибка автопринятия заявки {event.from_user.id}")


# ==================== КОМАНДА /admins — ВЫВОД ВСЕХ АДМИНОВ И РАНГОВ ====================
@router.message(Command("admins", "админы", "ранги", "staff", "стафф"))
async def cmd_admins_list(m: Message):
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await m.reply("команда работает только в чате")
        return

    cid = m.chat.id
    str_cid = str(cid)

    tg_admins_dict = {}
    try:
        tg_admins = await bot.get_chat_administrators(cid)
        for a in tg_admins:
            tg_admins_dict[a.user.id] = a
    except Exception as e:
        log.warning(f"get_chat_administrators error: {e}")

    iris_ranks = db.get("iris_ranks", {}).get(str_cid, {})
    lines = ["👑 <b>СПИСОК АДМИНИСТРАЦИИ И РАНГОВ В ЧАТЕ:</b>\n"]

    rank_titles = {
        "5": "⭐⭐⭐⭐⭐ Создатель",
        "4": "⭐⭐⭐⭐ Старший админ (4 ранг) [ОТСЛЕЖИВАЕТСЯ]",
        "3": "⭐⭐⭐ Младший админ",
        "2": "⭐⭐ Модератор",
        "1": "⭐ Младший модер"
    }

    found_any = False
    for r in ["5", "4", "3", "2", "1"]:
        users_in_rank = iris_ranks.get(r, [])
        if users_in_rank:
            found_any = True
            lines.append(f"<b>{rank_titles[r]}:</b>")
            for u in users_in_rank:
                un_str = f" (@{esc(u['username'])})" if u.get("username") else ""
                lines.append(f"• <b>{esc(u['name'])}</b>{un_str}")
            lines.append("")

    if not found_any:
        lines.append("<i>В базе бота пока нет синхронизированных рангов Ириса.</i>")
        lines.append("<b>Администраторы Telegram:</b>")
        for u_id, adm in tg_admins_dict.items():
            u = adm.user
            nm = esc(u.first_name or f"id{u.id}")
            un = f" (@{esc(u.username)})" if u.username else ""
            status_text = esc("Создатель" if adm.status == ChatMemberStatus.CREATOR else (getattr(adm, "custom_title", "") or "Администратор"))
            bot_tag = " [ИРИС]" if u.id in KNOWN_IRIS_IDS or (u.username and "iris" in u.username.lower()) else ""
            lines.append(f"• <b>{nm}</b>{un} — <code>{status_text}</code>{bot_tag}")

        lines.append("\n💡 <i>Напиши в чате команду <code>админы</code> или <code>стафф</code> (команда Ириса), и наш бот автоматически считает и сохранит все ранги Ириса!</i>")
    else:
        lines.append("💡 <i>Ранги обновляются автоматически, когда в чате пишется команда 'админы' для Ириса.</i>")

    await m.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("setrank", "ранг"))
async def cmd_set_rank_manual(m: Message):
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or m.from_user.id != OWNER_ID:
        return

    parts = m.text.split()
    if len(parts) < 3:
        await m.reply("Использование: <code>/setrank @username 4</code> (где 4 — Старший админ)", parse_mode="HTML")
        return

    target = parts[1].replace("@", "").strip()
    rank_num = parts[2].strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", target):
        await m.reply("Некорректный юзернейм (латиница, цифры, _; 3–32 символа).")
        return
    if rank_num not in ["1", "2", "3", "4", "5"]:
        await m.reply("Ранг должен быть от 1 до 5!")
        return

    str_cid = str(m.chat.id)
    ranks = db.setdefault("iris_ranks", {}).setdefault(str_cid, {"5": [], "4": [], "3": [], "2": [], "1": []})
    
    for r in ranks:
        ranks[r] = [x for x in ranks[r] if x.get("username", "").lower() != target.lower()]

    ranks[rank_num].append({
        "name": target,
        "username": target,
        "id": None,
        "updated_at": int(time.time())
    })
    save_data()
    await m.reply(f"✅ Пользователю @{target} успешно присвоен <b>{rank_num} ранг</b> в базе бота!", parse_mode="HTML")


# ==================== УПРАВЛЕНИЕ АДМИНАМИ: /adda, /add, /addg, /rev ====================
@router.message(Command("adda"))
async def cmd_adda_handler(m: Message):
    """Выдает префикс и открывает интерактивную панель настройки прав с кнопками"""
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await m.reply("Команда работает только в группе.")
        return

    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("Ответь реплаем на сообщение того, кому выдать права:\n<code>/adda [префикс]</code>", parse_mode="HTML")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    # Парсим и очищаем префикс (без эмодзи, макс 16 символов — требование Telegram API)
    raw = m.text.split(maxsplit=1)
    prefix = raw[1].strip() if len(raw) > 1 else "Админ"
    clean_prefix = re.sub(r'[\r\n\t]', ' ', prefix).strip()
    clean_prefix = "".join(ch for ch in clean_prefix if ord(ch) <= 0xFFFF)
    clean_prefix = clean_prefix[:16].strip()
    if not clean_prefix:
        clean_prefix = "Админ"

    bot_rights = await get_bot_admin_rights(cid)
    if not bot_rights.get("can_promote_members"):
        await m.reply("⚠️ У нашего бота нет права 'Назначение администраторов'! Включи это право нашему боту в настройках админов группы.")
        return

    try:
        # 1. Назначаем базовым админом с минимальными правами (инвайты)
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

        # 2. Ставим кастомный титул (префикс). В Telegram префикс можно ставить только пользователям, ботам нельзя!
        if not target_user.is_bot:
            await asyncio.sleep(0.3)
            try:
                await bot.set_chat_administrator_custom_title(cid, target_user.id, clean_prefix)
            except Exception as e:
                log.warning(f"set_chat_administrator_custom_title error: {e}")

        admin_cache.pop((cid, target_user.id), None)
        current_perms = await get_admin_perms_dict(cid, target_user.id)

        now_time = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M:%S")
        msg_text = f"🗓 <b>ТГ-права администратора {esc(target_name)}</b> {now_time}"
        await m.reply(msg_text, reply_markup=kb_admin_panel(cid, target_user.id, current_perms), parse_mode="HTML")

        await notify_owner(
            f"👑 <b>ОТКРЫТА ПАНЕЛЬ ПРАВ /adda</b>\n"
            f"<b>Чат:</b> «{esc(m.chat.title or "")}»\n"
            f"<b>Кому:</b> {esc(target_name)} [ID: <code>{target_user.id}</code>]\n"
            f"<b>Префикс:</b> <code>[{clean_prefix}]</code>"
        )
    except TelegramBadRequest as e:
        log.error(f"TelegramBadRequest in /adda: {e.message}")
        await m.reply(
            f"⚠️ Ошибка Telegram API: <code>{esc(e.message)}</code>\n\n"
            f"<b>Подсказка:</b>\n"
            f"1. Убедись, что наш бот стоит выше этого пользователя в списке администраторов.\n"
            f"2. Если ты пытаешься выдать права Ирису, а он уже назначен Создателем — сначала сними Ириса с админки в Telegram, а затем назначь через бота!",
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"Error in /adda: {e}")
        await m.reply(f"⚠️ Ошибка: {e}")


@router.callback_query(F.data.startswith("adm_p:"))
async def cb_toggle_admin_perm(cb: CallbackQuery):
    """Переключение отдельного права админа на инлайн-панели без багов и с чистыми алертами"""
    if cb.from_user.id != OWNER_ID:
        await cb.answer("Только создатель бота может менять права", show_alert=True)
        return

    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer()
        return

    try:
        cid = int(parts[1])
        uid = int(parts[2])
    except ValueError:
        await cb.answer()
        return
    perm_key = parts[3]
    if perm_key not in PERM_NAMES_RU:
        await cb.answer()
        return

    bot_rights = await get_bot_admin_rights(cid)
    current_p = await get_admin_perms_dict(cid, uid)

    new_val = not current_p.get(perm_key, False)
    ru_label = PERM_NAMES_RU.get(perm_key, perm_key)

    # Проверка прав у самого бота в Telegram
    bot_field = PERM_BOT_FIELDS.get(perm_key)
    if new_val and bot_field and not bot_rights.get(bot_field, False):
        await cb.answer(
            f"⚠️ У нашего бота нет права «{ru_label}» в этом чате!\n"
            f"Telegram запрещает выдавать права, которых нет у самого бота.",
            show_alert=True
        )
        return

    current_p[perm_key] = new_val

    try:
        await bot.promote_chat_member(
            chat_id=cid,
            user_id=uid,
            is_anonymous=current_p.get("anon", False),
            can_manage_chat=True,
            can_promote_members=current_p.get("promote", False) if bot_rights.get("can_promote_members") else False,
            can_change_info=current_p.get("info", False) if bot_rights.get("can_change_info") else False,
            can_delete_messages=current_p.get("delete", False) if bot_rights.get("can_delete_messages") else False,
            can_restrict_members=current_p.get("restrict", False) if bot_rights.get("can_restrict_members") else False,
            can_invite_users=current_p.get("invite", False) if bot_rights.get("can_invite_users") else False,
            can_pin_messages=current_p.get("pin", False) if bot_rights.get("can_pin_messages") else False,
            can_manage_video_chats=current_p.get("video", False) if bot_rights.get("can_manage_video_chats") else False,
            can_manage_topics=current_p.get("topics", False) if bot_rights.get("can_manage_topics") else False,
        )
        admin_cache.pop((cid, uid), None)
    except TelegramBadRequest as e:
        log.error(f"TelegramBadRequest in perm toggle: {e.message}")
        await cb.answer(f"⚠️ Ошибка Telegram: {e.message}", show_alert=True)
        return
    except Exception as e:
        log.error(f"Error in perm toggle: {e}")
        await cb.answer(f"⚠️ Ошибка: {e}", show_alert=True)
        return

    new_kb = kb_admin_panel(cid, uid, current_p)
    try:
        await cb.message.edit_reply_markup(reply_markup=new_kb)
    except TelegramBadRequest as e:
        # Игнорируем silent 'message is not modified', чтобы не было модального окна ошибки
        if "message is not modified" not in str(e).lower():
            log.warning(f"edit_reply_markup error: {e}")
    except Exception as e:
        log.warning(f"edit_reply_markup unknown error: {e}")

    await cb.answer(f"{'Включено' if new_val else 'Выключено'}: {ru_label}")


@router.callback_query(F.data.startswith("adm_revoke:"))
async def cb_admin_revoke(cb: CallbackQuery):
    """Кнопка 'Снять' на панели прав"""
    if cb.from_user.id != OWNER_ID:
        await cb.answer("Только создатель бота", show_alert=True)
        return

    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer()
        return

    try:
        cid = int(parts[1])
        uid = int(parts[2])
    except ValueError:
        await cb.answer()
        return

    ok = await demote_admin(cid, uid)
    if ok:
        try:
            await cb.message.edit_text("✅ <b>Права администратора и префикс полностью сняты!</b>", parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await cb.answer("Снято!")
        await notify_owner(f"🗑 <b>ПРАВА СНЯТЫ КНОПКОЙ 'СНЯТЬ'</b>\nЧат ID: <code>{cid}</code>\nПользователь ID: <code>{uid}</code>")
    else:
        await cb.answer(
            "⚠️ Не удалось снять права!\n"
            "Telegram разрешает снимать права ТОЛЬКО с тех, кого назначил сам бот.",
            show_alert=True
        )


@router.message(Command("rev", "разжаловать", "снять"))
async def cmd_revoke_handler(m: Message):
    """Команда /rev: забирает префикс и все права админа"""
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("Ответь реплаем на того, у кого забрать права: <code>/rev</code>", parse_mode="HTML")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    ok = await demote_admin(cid, target_user.id)
    if ok:
        await m.reply(f"забрал префикс и все права у {target_name}")
        await notify_owner(
            f"🗑 <b>СНЯТЫ ВСЕ ПРАВА (/rev)</b>\n"
            f"<b>Чат:</b> «{esc(m.chat.title or "")}»\n"
            f"<b>У кого:</b> {esc(target_name)} [ID: <code>{target_user.id}</code>]"
        )
    else:
        await m.reply(
            f"⚠️ Не смог снять права у {target_name}!\n\n"
            f"<b>Причина Telegram API:</b> бот может снимать права ТОЛЬКО с тех админов, кого он назначил сам.\n"
            f"Если этого админа (или Ириса) назначил Создатель чата напрямую в Telegram — бот бессилен (правило безопасности Telegram)."
        )


@router.message(Command("add", "addg"))
async def handle_cmd_add_simple(m: Message):
    """Выдает серый префикс без прав (только инвайты) и пишет строгое сообщение"""
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    if m.from_user.id != OWNER_ID:
        return

    if not m.reply_to_message or not m.reply_to_message.from_user:
        await m.reply("Ответь реплаем на того, кому выдать префикс:\n<code>/add [префикс]</code>", parse_mode="HTML")
        return

    target_user = m.reply_to_message.from_user
    cid = m.chat.id
    target_name = target_user.first_name or f"id{target_user.id}"

    raw = m.text.split(maxsplit=1)
    prefix = raw[1].strip() if len(raw) > 1 else "говно"
    clean_prefix = re.sub(r'[\r\n\t]', ' ', prefix).strip()
    clean_prefix = "".join(ch for ch in clean_prefix if ord(ch) <= 0xFFFF)
    clean_prefix = clean_prefix[:16].strip()
    if not clean_prefix:
        clean_prefix = "говно"

    if target_user.is_bot:
        await m.reply("⚠️ Ботам в Telegram нельзя ставить префиксы (ограничение Telegram API)!")
        return

    bot_rights = await get_bot_admin_rights(cid)
    if not bot_rights.get("can_promote_members"):
        await m.reply("⚠️ У нашего бота нет права 'Назначение администраторов'!")
        return

    try:
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
        await asyncio.sleep(0.3)
        try:
            await bot.set_chat_administrator_custom_title(cid, target_user.id, clean_prefix)
        except Exception as e:
            log.warning(f"set_chat_administrator_custom_title error: {e}")

        # СТРОГИЙ ФОРМАТ ПО ТРЕБОВАНИЮ ПОЛЬЗОВАТЕЛЯ:
        await m.reply(f"выдал {target_name} префикс [{clean_prefix}]")
        admin_cache.pop((cid, target_user.id), None)

        await notify_owner(
            f"👑 <b>ВЫДАН ПРЕФИКС БЕЗ ПРАВ</b>\n"
            f"<b>Чат:</b> «{esc(m.chat.title or "")}»\n"
            f"<b>Кому:</b> {esc(target_name)} [ID: <code>{target_user.id}</code>]\n"
            f"<b>Префикс:</b> <code>[{clean_prefix}]</code>"
        )
    except TelegramBadRequest as e:
        log.error(f"TelegramBadRequest in /add: {e.message}")
        await m.reply(f"⚠️ Ошибка Telegram API: <code>{esc(e.message)}</code>\n(Убедись, что наш бот стоит выше этого юзера в списке админов)", parse_mode="HTML")
    except Exception as e:
        log.error(f"Error in /add: {e}")
        await m.reply(f"⚠️ Ошибка: {e}")



# ==================== ТИШИНА ПРИ ДОБАВЛЕНИИ В ЧАТ ====================
@router.my_chat_member()
async def on_my_chat_member_updated(event: ChatMemberUpdated):
    if event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        cid = event.chat.id
        str_cid = str(cid)
        status = event.new_chat_member.status
        if status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
            db["chats"][str_cid] = event.chat.title or f"Chat {cid}"
            save_data()
            spawn(scan_and_cache_iris(cid))
        elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
            db["chats"].pop(str_cid, None)  # убрали из списка (настройки остаются в базе)
            save_data()


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

    try:
        delay = int(cfg.get("approve_delay", 1))
    except (TypeError, ValueError):
        delay = 1
    delay_label = "сразу" if delay == 0 else f"{delay} сек"

    rows = [
        [
            InlineKeyboardButton(text=f"🛡 Рейд админов (5х): {icon(cfg['anti_raid'])}", callback_data=f"tog:{str_cid}:anti_raid"),
            InlineKeyboardButton(text=f"🛑 Анти-Ирис: {icon(cfg['anti_iris'])}", callback_data=f"tog:{str_cid}:anti_iris"),
        ],
        [
            InlineKeyboardButton(text=f"🔄 Флуд текстом (10х): {icon(cfg.get('anti_flood_text', True))}", callback_data=f"tog:{str_cid}:anti_flood_text"),
            InlineKeyboardButton(text=f"📸 Флуд стикерами (10х): {icon(cfg.get('anti_flood_sticker', True))}", callback_data=f"tog:{str_cid}:anti_flood_sticker"),
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
            InlineKeyboardButton(text=f"🚪 Автопринятие заявок: {icon(cfg.get('auto_approve', True))}", callback_data=f"tog:{str_cid}:auto_approve"),
            InlineKeyboardButton(text=f"⏱ Задержка: {delay_label}", callback_data=f"cyc:{str_cid}:approve_delay"),
        ],
        [
            InlineKeyboardButton(text=f"🛑 Щит от рейда заявок: {icon(cfg.get('join_shield', True))}", callback_data=f"tog:{str_cid}:join_shield"),
            InlineKeyboardButton(text=f"🔔 Отчёты мне: {icon(cfg.get('notify_owner', True))}", callback_data=f"tog:{str_cid}:notify_owner"),
        ],
        [
            InlineKeyboardButton(text=f"👋 Приветствие: {icon(cfg.get('welcome', False))}", callback_data=f"tog:{str_cid}:welcome"),
            InlineKeyboardButton(text="✏️ Текст приветствия", callback_data=f"welcome_edit:{str_cid}"),
        ],
        [
            InlineKeyboardButton(text=f"🧹 Убирать «зашёл/вышел»: {icon(cfg.get('clean_service', False))}", callback_data=f"tog:{str_cid}:clean_service"),
        ],
    ]
    if join_pause_until.get(chat_id, 0) > time.monotonic():
        rows.append([InlineKeyboardButton(text="▶️ Снять паузу автопринятия (был рейд заявок)", callback_data=f"join_resume:{str_cid}")])
    rows += [
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
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- общие помощники для панели ----------
async def owner_only(cb: CallbackQuery) -> bool:
    if cb.from_user.id != OWNER_ID:
        await cb.answer("не овнер", show_alert=True)
        return False
    return True

def parse_cid(data: str, idx: int = 1) -> Optional[int]:
    try:
        return int(data.split(":")[idx])
    except (IndexError, ValueError):
        return None

async def safe_edit(cb: CallbackQuery, text: Optional[str] = None, markup: Optional[InlineKeyboardMarkup] = None,
                    parse_mode: Optional[str] = None):
    """Редактирование сообщения без падений: «message is not modified» и недоступные сообщения игнорируем."""
    msg = cb.message
    if not isinstance(msg, Message):
        return
    try:
        if text is None:
            await msg.edit_reply_markup(reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning(f"edit error: {e}")


@router.message(CommandStart())
async def cmd_start_private(m: Message):
    if m.chat.type != ChatType.PRIVATE:
        await m.reply(f"настройки только в лс: @{BOT_USERNAME}")
        return

    if not m.from_user or m.from_user.id != OWNER_ID:
        await m.reply("доступ запрещён. ты не овнер бота.")
        return

    chats = db.get("chats", {})
    if not chats:
        await m.reply("добавь бота в чат админом с полными правами, напиши в чате любое слово и пиши сюда /start")
        return

    await m.reply("выбирай чат для настройки:", reply_markup=kb_chat_list())

@router.callback_query(F.data == "back_to_chats")
async def cb_back_to_chats(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    await safe_edit(cb, "выбирай чат для настройки:", kb_chat_list())
    await cb.answer()

@router.callback_query(F.data == "refresh_chats")
async def cb_refresh_chats(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    await safe_edit(cb, "выбирай чат для настройки:", kb_chat_list())
    await cb.answer("обновлено")

@router.callback_query(F.data == "help_manual")
async def cb_help_manual(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    manual = (
        "📖 <b>ПОЛНОЕ ОПИСАНИЕ ЗАЩИТЫ БОТА:</b>\n\n"
        "🔄 <b>1. Антифлуд одинаковым текстом:</b>\n"
        "— Мутит ТОЛЬКО если человек отправил ОДНО И ТО ЖЕ сообщение >10 раз за 10 сек!\n"
        "— Наказание: мут на 10 минут, реплай: <code>замучен на 10 минут\n@</code>.\n"
        "— <b>Разные сообщения не трогает вообще.</b>\n\n"
        "📸 <b>2. Антифлуд одинаковыми стикерами:</b>\n"
        "— Мутит ТОЛЬКО если спамят ОДИН И ТОТ ЖЕ стикер >10 раз за 10 сек.\n\n"
        "🛡 <b>3. Иммунитет для гифок, фото и видео:</b>\n"
        "— Бот НИКОГДА не мутит за гифки, фото, видео и кружочки.\n\n"
        "🛡 <b>4. Антирейд админов (бан/кик 5х):</b>\n"
        "— Считает только самостоятельные слова 'бан' / 'кик'. При 5+ за минуту у админа или стаффа 4-5 ранга: "
        "снятие прав + мут навсегда + реплай.\n\n"
        "🛑 <b>5. Анти-Ирис (кик неактив):</b>\n"
        "— Удаляет команду <code>.кик неактив</code> и снимает права у Ириса. Создателя чата и тебя не трогает.\n\n"
        "👑 <b>6. Ранги Ириса (/admins):</b>\n"
        "— Ранги обновляются, когда Ирис присылает список стаффа.\n\n"
        "🎬 <b>7. Скачивание TikTok:</b>\n"
        "— Ссылка TikTok в чате → видео без водяного знака (не чаще раза в 10 сек от человека, до 50 МБ).\n\n"
        "🚪 <b>8. Автопринятие заявок:</b>\n"
        "— Бот принимает заявки на вступление через заданную задержку (по умолчанию 1 сек, кнопка «Задержка» листает 0/1/3/5/10/30). "
        "Нужны права админа «Приглашение пользователей». Выключи — заявки решают админы.\n\n"
        "🛑 <b>9. Щит от рейда заявок:</b>\n"
        "— Если за 30 сек пришло 15+ заявок, автопринятие встаёт на паузу на 10 мин, заявки не принимаются. "
        "Паузу можно снять кнопкой.\n\n"
        "👋 <b>10. Приветствие</b> (текст меняется, <code>{name}</code> — имя новичка) и "
        "🧹 <b>уборка «зашёл/вышел»</b> — каждое отдельно включается и выключается.\n\n"
        "🔔 <b>11. Отчёты мне:</b> выключает уведомления в личку по этому чату."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_chats")]
    ])
    await safe_edit(cb, manual, kb, parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("cfg_chat:"))
async def cb_open_chat_cfg(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    cid = parse_cid(cb.data)
    if cid is None:
        await cb.answer()
        return
    title = db.get("chats", {}).get(str(cid), f"Чат {cid}")
    await scan_and_cache_iris(cid)
    await safe_edit(cb, f"⚙️ настройки для «{title}»:\nжми на кнопки для переключения:", kb_chat_panel(cid))
    await cb.answer()

@router.callback_query(F.data.startswith("tog:"))
async def cb_toggle_option(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    parts = cb.data.split(":")
    cid = parse_cid(cb.data)
    opt = parts[2] if len(parts) > 2 else ""
    if cid is None or opt not in TOGGLE_KEYS:  # произвольные ключи в настройки не пускаем
        await cb.answer("неизвестная опция")
        return
    cfg = get_chat_cfg(cid)
    cfg[opt] = not cfg.get(opt, default_settings().get(opt, True))
    save_data()
    await safe_edit(cb, markup=kb_chat_panel(cid))
    await cb.answer("включено" if cfg[opt] else "выключено")

@router.callback_query(F.data.startswith("cyc:"))
async def cb_cycle_option(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    parts = cb.data.split(":")
    cid = parse_cid(cb.data)
    if cid is None or len(parts) < 3 or parts[2] != "approve_delay":
        await cb.answer()
        return
    cfg = get_chat_cfg(cid)
    try:
        cur = int(cfg.get("approve_delay", 1))
    except (TypeError, ValueError):
        cur = 1
    nxt = JOIN_DELAYS[(JOIN_DELAYS.index(cur) + 1) % len(JOIN_DELAYS)] if cur in JOIN_DELAYS else 1
    cfg["approve_delay"] = nxt
    save_data()
    await safe_edit(cb, markup=kb_chat_panel(cid))
    await cb.answer("принимаю сразу" if nxt == 0 else f"задержка {nxt} сек")

@router.callback_query(F.data.startswith("join_resume:"))
async def cb_join_resume(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    cid = parse_cid(cb.data)
    if cid is None:
        await cb.answer()
        return
    join_pause_until.pop(cid, None)
    join_tracker.pop(cid, None)
    await safe_edit(cb, markup=kb_chat_panel(cid))
    await cb.answer("Пауза снята. Заявки, что висели, прими вручную.", show_alert=True)

@router.callback_query(F.data.startswith("welcome_edit:"))
async def cb_welcome_edit(cb: CallbackQuery, state: FSMContext):
    if not await owner_only(cb):
        return
    cid = parse_cid(cb.data)
    if cid is None:
        await cb.answer()
        return
    await state.set_state(FormStates.waiting_for_welcome_text)
    await state.update_data(target_cid=str(cid))
    cur = get_chat_cfg(cid).get("welcome_text", "")
    await safe_edit(
        cb,
        f"✏️ Пришли новый текст приветствия (до 500 символов).\n"
        f"<code>{{name}}</code> заменится на имя новичка.\n\nСейчас:\n<i>{esc(cur)}</i>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"cfg_chat:{cid}")]
        ]),
        parse_mode="HTML",
    )
    await cb.answer()

@router.message(FormStates.waiting_for_welcome_text)
async def process_welcome_text(m: Message, state: FSMContext):
    if not m.from_user or m.from_user.id != OWNER_ID or not m.text:
        return
    text = m.text.strip()
    if not text or len(text) > 500:
        await m.reply("Текст должен быть от 1 до 500 символов. Пришли ещё раз.")
        return
    data = await state.get_data()
    try:
        cid = int(data.get("target_cid"))
    except (TypeError, ValueError):
        await state.clear()
        return
    get_chat_cfg(cid)["welcome_text"] = text
    save_data()
    await state.clear()
    await m.reply(
        "✅ Текст приветствия сохранён. Не забудь включить «Приветствие».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К настройкам чата", callback_data=f"cfg_chat:{cid}")]
        ]),
    )

@router.callback_query(F.data.startswith("scan_iris:"))
async def cb_scan_iris(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    cid = parse_cid(cb.data)
    if cid is None:
        await cb.answer()
        return
    iris_ids = await scan_and_cache_iris(cid)
    await safe_edit(cb, markup=kb_chat_panel(cid))
    await cb.answer(f"Ирисов в базе чата: {len(iris_ids)} шт.", show_alert=True)


# ==================== МЕНЮ ТРИГГЕРОВ ====================
# В callback_data кладём ИНДЕКС, а не слово: лимит Telegram 64 байта, кириллица занимает по 2,
# а двоеточие в слове ломало разбор. Раньше длинное слово вешало всё меню.
def kb_triggers_menu(str_cid: str) -> InlineKeyboardMarkup:
    trigs = list(db.get("triggers", {}).get(str_cid, {}).keys())
    buttons = []
    for i, word in enumerate(trigs[:8]):
        buttons.append([InlineKeyboardButton(text=f"❌ Удалить '{word[:30]}'", callback_data=f"del_trig:{str_cid}:{i}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить триггер", callback_data=f"add_trig:{str_cid}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data=f"cfg_chat:{str_cid}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(F.data.startswith("menu_trig:"))
async def cb_menu_triggers(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    str_cid = cb.data.split(":")[1]
    trigs = db.get("triggers", {}).get(str_cid, {})
    text = "🎯 <b>Пользовательские триггеры автоответов:</b>\n"
    if not trigs:
        text += "Пока нет добавленных триггеров.\nНажми 'Добавить', чтобы бот отвечал на нужные слова!"
    else:
        for w, r in trigs.items():
            text += f"• <code>{esc(w)}</code> ➡️ <i>{esc(r[:100])}</i>\n"
            if len(text) > 3500:
                text += "…"
                break

    await safe_edit(cb, text, kb_triggers_menu(str_cid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("add_trig:"))
async def cb_add_trigger_start(cb: CallbackQuery, state: FSMContext):
    if not await owner_only(cb):
        return
    str_cid = cb.data.split(":")[1]
    await state.set_state(FormStates.waiting_for_trigger_word)
    await state.update_data(target_cid=str_cid)
    await safe_edit(
        cb,
        "✏️ Шаг 1: Напиши <b>слово или фразу</b>, на которую бот должен реагировать в чате:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"menu_trig:{str_cid}")]
        ]),
        parse_mode="HTML",
    )
    await cb.answer()

@router.message(FormStates.waiting_for_trigger_word)
async def process_trigger_word(m: Message, state: FSMContext):
    if not m.from_user or m.from_user.id != OWNER_ID or not m.text:
        return
    word = m.text.strip().lower()
    if not word or len(word) > 64:
        await m.reply("Слово должно быть от 1 до 64 символов. Пришли ещё раз.")
        return
    await state.update_data(trigger_word=word)
    await state.set_state(FormStates.waiting_for_trigger_reply)
    await m.reply(
        f"✅ Слово: <code>{esc(word)}</code>\n\n✏️ Шаг 2: Теперь напиши <b>текст ответа</b>, который бот напишет в чат:",
        parse_mode="HTML"
    )

@router.message(FormStates.waiting_for_trigger_reply)
async def process_trigger_reply(m: Message, state: FSMContext):
    if not m.from_user or m.from_user.id != OWNER_ID or not m.text:
        return
    data = await state.get_data()
    str_cid = data.get("target_cid")
    word = data.get("trigger_word")
    reply_text = m.text.strip()
    if not str_cid or not word:
        await state.clear()
        return
    if not reply_text or len(reply_text) > 1000:
        await m.reply("Ответ должен быть от 1 до 1000 символов. Пришли ещё раз.")
        return

    db["triggers"].setdefault(str_cid, {})[word] = reply_text
    save_data()
    await state.clear()

    await m.reply(
        f"🎉 <b>Триггер сохранён!</b>\nСлово: <code>{esc(word)}</code> ➡️ <i>{esc(reply_text[:200])}</i>",
        reply_markup=kb_triggers_menu(str_cid),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("del_trig:"))
async def cb_delete_trigger(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    parts = cb.data.split(":")
    str_cid = parts[1]
    try:
        idx = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    trigs = db.get("triggers", {}).get(str_cid, {})
    keys = list(trigs.keys())
    if 0 <= idx < len(keys):
        del trigs[keys[idx]]
        save_data()
    await cb_menu_triggers(cb)


# ==================== МЕНЮ ЗАПРЕЩЁННЫХ СЛОВ ====================
def kb_bad_menu(str_cid: str) -> InlineKeyboardMarkup:
    words = db.get("bad_words", {}).get(str_cid, [])
    buttons = []
    for i, w in enumerate(words[:8]):
        buttons.append([InlineKeyboardButton(text=f"❌ Удалить '{w[:30]}'", callback_data=f"del_bad:{str_cid}:{i}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить запретное слово", callback_data=f"add_bad:{str_cid}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data=f"cfg_chat:{str_cid}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.callback_query(F.data.startswith("menu_bad:"))
async def cb_menu_bad(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    str_cid = cb.data.split(":")[1]
    words = db.get("bad_words", {}).get(str_cid, [])
    text = "🚫 <b>Запрещённые слова (автоудаление сообщений):</b>\n"
    if not words:
        text += "Список пуст.\nНажми 'Добавить', чтобы запретить слова!"
    else:
        text += ", ".join(f"<code>{esc(w)}</code>" for w in words)
        if len(text) > 3500:
            text = text[:3500] + "…"

    await safe_edit(cb, text, kb_bad_menu(str_cid), parse_mode="HTML")
    await cb.answer()

@router.callback_query(F.data.startswith("add_bad:"))
async def cb_add_bad_start(cb: CallbackQuery, state: FSMContext):
    if not await owner_only(cb):
        return
    str_cid = cb.data.split(":")[1]
    await state.set_state(FormStates.waiting_for_bad_word)
    await state.update_data(target_cid=str_cid)
    await safe_edit(
        cb,
        "✏️ Напиши слово, которое бот должен удалять в чате:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data=f"menu_bad:{str_cid}")]
        ]),
    )
    await cb.answer()

@router.message(FormStates.waiting_for_bad_word)
async def process_bad_word(m: Message, state: FSMContext):
    if not m.from_user or m.from_user.id != OWNER_ID or not m.text:
        return
    data = await state.get_data()
    str_cid = data.get("target_cid")
    word = m.text.strip().lower()
    if not str_cid:
        await state.clear()
        return
    if not word or len(word) > 64:
        await m.reply("Слово должно быть от 1 до 64 символов. Пришли ещё раз.")
        return

    words = db["bad_words"].setdefault(str_cid, [])
    if word not in words:
        words.append(word)
        save_data()
    await state.clear()

    await m.reply(
        f"✅ Слово <code>{esc(word)}</code> добавлено в запретный список!",
        reply_markup=kb_bad_menu(str_cid),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("del_bad:"))
async def cb_del_bad(cb: CallbackQuery):
    if not await owner_only(cb):
        return
    parts = cb.data.split(":")
    str_cid = parts[1]
    try:
        idx = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    words = db.get("bad_words", {}).get(str_cid, [])
    if 0 <= idx < len(words):
        words.pop(idx)
        save_data()
    await cb_menu_bad(cb)


# ==================== СТАТИСТИКА ТАЙПЕРОВ ====================
async def get_user_display_name(cid: int, uid: int) -> str:
    key = (cid, uid)
    now = time.monotonic()
    hit = name_cache.get(key)
    if hit and now - hit[1] < 3600:
        return hit[0]
    try:
        cm = await bot.get_chat_member(cid, uid)
        name = cm.user.first_name or f"id{uid}"
    except Exception:
        return f"id{uid}"  # сбой не кэшируем
    name_cache[key] = (name, now)
    return name

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
    if not get_chat_cfg(m.chat.id).get("fun", True):
        return False
    # не чаще раза в 3 сек на человека — иначе бот упрётся во flood-limit Telegram
    key = (m.chat.id, m.from_user.id if m.from_user else 0)
    now = time.monotonic()
    if now - fun_cooldown.get(key, -1e9) < 3:
        return False
    fun_cooldown[key] = now
    return True

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
            "🎮 <b>Команды бота:</b>\n"
            "/admins — список админов и рангов Ириса\n"
            "/typers — топ активных писателей за день\n"
            "/adda [префикс] — выдать права с кнопками (реплаем)\n"
            "/add [префикс] — серый префикс без прав (реплаем)\n"
            "/rev — снять все права и префикс (реплаем)\n"
            "/пидор /шип /гей /шлюха /айкью /хуй /дуэль /казнь /кто /инфа /шар\n\n"
            f"⚙️ настройки только в лс: @{BOT_USERNAME}",
            parse_mode="HTML"
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
    return runner


# ==================== АВТООЧИСТКА ПАМЯТИ ТРЕКЕРОВ РАЗ В 60 СЕК ====================
def _prune_lists(tree: dict, cutoff: float, depth: int):
    """Чистит вложенные словари со списками меток времени. depth = число уровней словарей минус 1."""
    for k in list(tree.keys()):
        v = tree[k]
        if depth == 0:
            nv = [t for t in v if t > cutoff]
            if nv:
                tree[k] = nv
            else:
                del tree[k]
        else:
            _prune_lists(v, cutoff, depth - 1)
            if not v:
                del tree[k]

async def cleanup_memory_task():
    """Раз в минуту выбрасывает всё устаревшее, чтобы память не росла со временем."""
    while True:
        await asyncio.sleep(60)
        try:
            now = time.monotonic()
            _prune_lists(flood_text_tracker, now - 15, 2)
            _prune_lists(flood_sticker_tracker, now - 15, 2)
            _prune_lists(ban_word_tracker, now - 70, 1)
            _prune_lists(kick_word_tracker, now - 70, 1)
            _prune_lists(admin_kicks, now - MASS_KICK_WINDOW - 5, 1)
            _prune_lists(join_tracker, now - JOIN_SHIELD_WINDOW - 5, 0)

            for k in [k for k, v in admin_cache.items() if now - v[2] > 3600]:
                del admin_cache[k]
            for k in [k for k, v in name_cache.items() if now - v[1] > 3600]:
                del name_cache[k]
            for d, ttl in ((recent_demote, 60), (fun_cooldown, 60), (trigger_cooldown, 60),
                           (tiktok_cooldown, 120), (notice_sent, 7200)):
                for k in [k for k, t in d.items() if now - t > ttl]:
                    del d[k]
            for k in [k for k, t in join_pause_until.items() if t <= now]:
                del join_pause_until[k]
        except Exception as e:
            log.warning(f"cleanup_memory_task error: {e}")


# ==================== ЗАПУСК ====================
async def background_periodic_save():
    while True:
        await asyncio.sleep(120)
        prune_stats()
        save_data()

async def main():
    global BOT_ID, BOT_USERNAME
    load_data()
    prune_stats()
    me = await bot.get_me()
    BOT_ID = me.id
    BOT_USERNAME = me.username or ""
    log.info(f"🚀 Бот запущен: @{BOT_USERNAME} [ID: {BOT_ID}]")

    runner = None
    try:
        runner = await start_web_server()
    except Exception as e:
        log.warning(f"Could not start anti-sleep web server: {e}")

    try:
        await bot.send_message(OWNER_ID, f"🟢 <b>Бот @{BOT_USERNAME} v14 запущен!</b>\n/start — настройки чатов", parse_mode="HTML")
    except Exception as e:
        log.warning(f"Could not send startup message to owner: {e}")

    spawn(background_periodic_save())
    spawn(cleanup_memory_task())

    try:
        # типы апдейтов собираются из зарегистрированных обработчиков
        # (message, chat_member, my_chat_member, callback_query, chat_join_request)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        save_data()
        if runner is not None:
            await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

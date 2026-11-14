"""Загрузка .env и общие константы."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:  # python-dotenv не обязателен, если переменные заданы в системе
    pass


def _ids(name: str) -> set[int]:
    out: set[int] = set()
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Telegram ID людей, которые могут управлять ботом в ЛЮБОМ чате (кроме создателя чата)
OWNER_IDS = _ids("OWNER_IDS")

# Как опознаём Ирис: по юзернейму и/или по ID
IRIS_USERNAMES = {
    x.strip().lstrip("@").lower()
    for x in os.getenv("IRIS_USERNAMES", "iris_moon_bot").split(",")
    if x.strip()
}
IRIS_IDS = _ids("IRIS_IDS")

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).with_name("bot.db")))

# Служебные «пользователи» Telegram, которых нельзя наказывать
ANON_ADMIN_ID = 1087968824  # GroupAnonymousBot
CHANNEL_FWD_ID = 777000  # автопересылка из канала

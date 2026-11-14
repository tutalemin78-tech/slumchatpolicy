"""Хранилище: настройки каждого чата (JSON в SQLite), статистика, kv."""
import copy
import json
import sqlite3
import time

import config

# ──────────────────────────── Настройки по умолчанию ────────────────────────────
DEFAULTS: dict = {
    # --- общий выключатель
    "protect": True,
    "alerts": True,  # писать отчёты в чат
    # --- анти-снос через Ирис («кик неактив сутки» и т.п.)
    "iris_guard": True,
    "danger_cmds": [
        "кик неактив",
        "кик молчун",
        "кик всех",
        "кик все",
        "бан всех",
        "бан все",
        "снос",
    ],
    "cmd_window": 180,  # сек: сколько после опасной команды считаем кик «её последствием»
    "iris_mass_limit": 4,  # если команды не видели: сколько киков Ириса за окно = атака
    "iris_mass_window": 60,
    "iris_strip": True,  # снять у Ириса права бана/кика после первого кика
    "iris_full_demote": False,  # False = снять только бан/кик/назначение админов, True = все права
    "iris_punish_issuer": True,  # снять права и замутить автора команды
    "cmd_instant": False,  # наказать автора опасной команды СРАЗУ (не дожидаясь кика)
    "cmd_delete": False,  # удалять опасную команду
    "restore_kicked": True,  # разбанить кикнутых и дать ссылку на возврат
    "dm_victims": True,  # пробовать написать кикнутым ссылку в личку
    # --- флуд командами «бан» / «кик»
    "ban_guard": True,
    "ban_words": ["бан", "ban"],
    "ban_limit": 3,
    "ban_window": 60,
    "ban_reason": "слив",
    "kick_guard": True,
    "kick_words": ["кик", "kick"],
    "kick_limit": 3,
    "kick_window": 60,
    "kick_reason": "слив",
    "words_anywhere": False,  # считать слово в любом месте сообщения, а не только в начале
    "perma_cmd": "мут навсегда",  # команда для Ириса при сливе
    # --- флуд медиа и одинаковыми сообщениями
    "media_guard": True,
    "media_limit": 10,
    "media_window": 10,
    "dup_guard": True,
    "dup_limit": 15,
    "dup_window": 10,
    "flood_cmd": "мут",  # команда для Ириса при флуде
    "flood_mute_minutes": 30,  # мут от самого бота при флуде (0 = навсегда)
    "flood_demote_admins": True,  # если флудит админ — снять права и замутить
    # --- как именно наказываем
    "iris_text": True,  # писать команду («мут …») текстом в ответ
    "native_punish": True,  # дополнительно мутить самим ботом (работает всегда)
    # --- анти-снос от обычных админов-людей
    "nuke_guard": True,
    "nuke_limit": 3,
    "nuke_window": 60,
    # --- анти-рейд
    "raid_guard": True,
    "raid_limit": 8,
    "raid_window": 30,
    "raid_minutes": 10,
    # --- прочая защита
    "link_guard": False,
    "word_guard": False,
    "bad_words": [],
    "welcome": False,
    "welcome_text": "👋 Добро пожаловать, {name}!",
    # --- веселье
    "fun_enabled": True,
    "fun_xp": True,
    # --- служебное (не редактируется через /set)
    "iris_id": 0,
    "iris_saved_rights": {},
    "whitelist": [],
}

# (ключ, название) — в таком порядке показываются в меню
PROTECT_TOGGLES = [
    ("protect", "🛡 ЗАЩИТА ЧАТА (общий выключатель)"),
    ("iris_guard", "🔥 Анти-снос через Ирис"),
    ("iris_strip", "   ↳ снимать у Ириса права бана/кика"),
    ("iris_full_demote", "   ↳ снимать у Ириса ВСЕ права"),
    ("iris_punish_issuer", "   ↳ наказывать автора команды"),
    ("cmd_instant", "   ↳ наказывать автора СРАЗУ (до кика)"),
    ("cmd_delete", "   ↳ удалять опасную команду"),
    ("restore_kicked", "   ↳ возвращать кикнутых"),
    ("ban_guard", "🔨 Слив командой «бан»"),
    ("kick_guard", "👢 Слив командой «кик»"),
    ("media_guard", "🖼 Флуд стикерами/гифками/фото"),
    ("dup_guard", "📋 Одинаковые сообщения"),
    ("nuke_guard", "💣 Анти-снос от админов-людей"),
    ("raid_guard", "🚨 Анти-рейд (массовые заходы)"),
    ("link_guard", "🔗 Удалять ссылки от обычных"),
    ("word_guard", "🤬 Фильтр запрещённых слов"),
    ("iris_text", "💬 Писать «мут…» текстом для Ириса"),
    ("native_punish", "🔇 Мутить самим ботом (надёжно)"),
    ("alerts", "📣 Отчёты в чат"),
    ("welcome", "👋 Приветствие новичков"),
]

FUN_TITLES = {
    "ball": "🎱 /ball — шар судьбы",
    "dice": "🎲 /dice — кубик",
    "slots": "🎰 /slots — слоты",
    "coin": "🪙 /coin — монетка",
    "choose": "🤔 /choose — выбор",
    "rate": "📊 /rate — оценка",
    "who": "🕵️ /who — кто из чата…",
    "joke": "😂 /joke — шутка",
    "roulette": "🔫 /roulette — рулетка",
    "compat": "💞 /compat — совместимость",
    "truth": "🗣 /truth — вопрос",
    "dare": "😈 /dare — задание",
    "hero": "👑 /hero — герой дня",
    "duel": "⚔️ /duel — дуэль",
    "iq": "🧠 /iq — IQ дня",
    "fact": "📚 /fact — факт",
    "predict": "🔮 /predict — предсказание",
    "me": "👤 /me — профиль",
    "top": "🏆 /top — топ актива",
}
for _k in FUN_TITLES:
    DEFAULTS["fun_" + _k] = True

INTERNAL_KEYS = {"iris_id", "iris_saved_rights", "whitelist"}
EDITABLE_KEYS = [k for k in DEFAULTS if k not in INTERNAL_KEYS]


class Storage:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                settings TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                msgs INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                last_seen INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS kv (
                chat_id INTEGER NOT NULL,
                k TEXT NOT NULL,
                v TEXT NOT NULL,
                PRIMARY KEY (chat_id, k)
            );
            """
        )
        self.conn.commit()
        self._cache: dict[int, dict] = {}

    # ---------- настройки ----------
    def get(self, chat_id: int) -> dict:
        s = self._cache.get(chat_id)
        if s is None:
            row = self.conn.execute(
                "SELECT settings FROM chats WHERE chat_id=?", (chat_id,)
            ).fetchone()
            try:
                data = json.loads(row[0]) if row else {}
            except (ValueError, TypeError):
                data = {}
            s = copy.deepcopy(DEFAULTS)
            s.update(data)
            self._cache[chat_id] = s
        return s

    def save(self, chat_id: int) -> None:
        s = self.get(chat_id)
        self.conn.execute(
            "INSERT INTO chats(chat_id, settings) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET settings=excluded.settings",
            (chat_id, json.dumps(s, ensure_ascii=False)),
        )
        self.conn.commit()

    def set(self, chat_id: int, key: str, value) -> None:
        self.get(chat_id)[key] = value
        self.save(chat_id)

    # ---------- kv ----------
    def kv_get(self, chat_id: int, key: str):
        row = self.conn.execute(
            "SELECT v FROM kv WHERE chat_id=? AND k=?", (chat_id, key)
        ).fetchone()
        return row[0] if row else None

    def kv_set(self, chat_id: int, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv(chat_id,k,v) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,k) DO UPDATE SET v=excluded.v",
            (chat_id, key, value),
        )
        self.conn.commit()

    # ---------- статистика ----------
    def add_stats(self, rows) -> None:
        """rows: iterable of (chat_id, user_id, name, msgs, xp)"""
        now = int(time.time())
        self.conn.executemany(
            "INSERT INTO stats(chat_id,user_id,name,msgs,xp,last_seen) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET "
            "msgs=msgs+excluded.msgs, xp=xp+excluded.xp, name=excluded.name, last_seen=excluded.last_seen",
            [(c, u, n, m, x, now) for c, u, n, m, x in rows],
        )
        self.conn.commit()

    def top(self, chat_id: int, limit: int = 10):
        return self.conn.execute(
            "SELECT user_id,name,msgs,xp FROM stats WHERE chat_id=? ORDER BY xp DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()

    def user_stats(self, chat_id: int, user_id: int):
        row = self.conn.execute(
            "SELECT msgs,xp FROM stats WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ).fetchone()
        if not row:
            return None
        rank = self.conn.execute(
            "SELECT COUNT(*)+1 FROM stats WHERE chat_id=? AND xp>?", (chat_id, row[1])
        ).fetchone()[0]
        return row[0], row[1], rank

    def recent_users(self, chat_id: int, limit: int = 300):
        return self.conn.execute(
            "SELECT user_id,name FROM stats WHERE chat_id=? ORDER BY last_seen DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()


store = Storage(config.DB_PATH)

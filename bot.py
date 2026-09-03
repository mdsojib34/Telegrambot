import asyncio
import base64
import io
import hashlib
import hmac
import json
import logging
import os
import re
import time
import secrets
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qsl

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("viral-video-bot")

# V18 roles
# MINI_BOT_* = launcher + Mini App + central admin control
# BOT_*      = notification/community bot (broadcast + package notifications + join/leave)
# VIDEO_BOT_* = protected video delivery bot
MINI_BOT_TOKEN = os.environ["MINI_BOT_TOKEN"]
MINI_BOT_USERNAME = os.getenv("MINI_BOT_USERNAME", "").lstrip("@")
BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.getenv("BOT_USERNAME", "Bangladesh_vairal_videobot").lstrip("@")
VIDEO_BOT_TOKEN = os.environ["VIDEO_BOT_TOKEN"]
VIDEO_BOT_USERNAME = os.getenv("VIDEO_BOT_USERNAME", "Viral_video99_bot").lstrip("@")
OWNER_ID = int(os.environ["OWNER_ID"])
STORAGE_CHANNEL_ID = int(os.environ["STORAGE_CHANNEL_ID"])
DEFAULT_MINI_APP_URL = (os.getenv("MINI_APP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
DEFAULT_MENU_BUTTON_TEXT = os.getenv("MENU_BUTTON_TEXT", "🎬 Video open").strip() or "🎬 Video open"
POLL_SECONDS = int(os.getenv("BROADCAST_POLL_SECONDS", "15"))
MENU_SYNC_SECONDS = int(os.getenv("MENU_SYNC_SECONDS", "60"))
PORT = int(os.getenv("PORT", "8080"))

DATABASE_URL = os.environ["DATABASE_URL"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

mini_bot = Bot(MINI_BOT_TOKEN)
bot = Bot(BOT_TOKEN)
video_bot = Bot(VIDEO_BOT_TOKEN)
mini_dp = Dispatcher()
dp = Dispatcher()
video_dp = Dispatcher()
db_pool = None
VIDEO_CODE_RE = re.compile(r"(?:start=)?(video_[A-Za-z0-9_-]+)")
ADMIN_CACHE = {OWNER_ID}
ADMIN_PERMS = {}


def utcnow_sql():
    return datetime.now(timezone.utc)


async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    if os.path.exists(schema_path):
        async with db_pool.acquire() as conn:
            await conn.execute(open(schema_path, "r", encoding="utf-8").read())
            # V20.1 text-control migration: additive only, safe for existing databases.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tutorial_last_messages (
                    bot_role TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (bot_role, user_id)
                )
            """)
            for ddl in (
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS video_access_title TEXT DEFAULT '🎁 Video Access'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS video_access_instruction TEXT DEFAULT 'ভিডিওটি দেখার জন্য প্রয়োজনীয় Ad গুলো দেখুন'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS ad_progress_seen_text TEXT DEFAULT 'আপনার {completed}টি Ad দেখা হয়েছে ✅'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS ad_progress_remaining_text TEXT DEFAULT 'আরও {remaining}টি Ad বাকি — ভিডিও দেখতে বাকি Ad গুলো দেখুন।'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS ad_watch_button_text TEXT DEFAULT '📢 Ad দেখুন'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS all_ads_done_text TEXT DEFAULT '✅ সব Ad সম্পন্ন হয়েছে\nআপনার ভিডিও আনলক হয়েছে'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS watch_video_button_text TEXT DEFAULT '🎬 ভিডিও দেখুন'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS video_access_secure_text TEXT DEFAULT '🛡️ 100% নিরাপদ • আপনার তথ্য গোপন রাখা হয়'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS mini_bot_package_message TEXT DEFAULT '🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}\n\nনতুন ভিডিও দেখতে নিচের বাটনে ক্লিক করুন 👇'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS mini_bot_package_button_text TEXT DEFAULT '🎬 ভিডিও দেখুন'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS video_bot_package_message TEXT DEFAULT '🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}\n\nনতুন ভিডিও দেখতে নিচের বাটনে ক্লিক করুন 👇'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS video_bot_package_button_text TEXT DEFAULT '🎬 ভিডিও দেখুন'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS notification_bot_package_message TEXT DEFAULT '🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}\n\nনতুন ভিডিও দেখতে নিচের বাটনে ক্লিক করুন 👇'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS notification_bot_package_button_text TEXT DEFAULT '🎬 ভিডিও দেখুন'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS channel_package_message TEXT DEFAULT '🔥 New Video Package\n\nPackage Name: {package_name}\n\nTotal Video: {total_video}'",
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS channel_package_button_text TEXT DEFAULT 'ভিডিও দেখুন'"
            ):
                await conn.execute(ddl)
    await refresh_admin_cache()
    log.info("PostgreSQL connected and schema ready")


def _pg_sql(sql: str) -> str:
    # Convert MySQL-style %s placeholders used by the app to asyncpg $1, $2...
    i = 0
    while "%s" in sql:
        i += 1
        sql = sql.replace("%s", f"${i}", 1)
    return sql


async def db_fetchone(sql, args=()):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(_pg_sql(sql), *args)
        return dict(row) if row else None


async def db_fetchall(sql, args=()):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(_pg_sql(sql), *args)
        return [dict(r) for r in rows]


async def db_execute(sql, args=()):
    async with db_pool.acquire() as conn:
        result = await conn.execute(_pg_sql(sql), *args)
        try:
            return int(result.split()[-1])
        except Exception:
            return 0


async def refresh_admin_cache():
    global ADMIN_CACHE, ADMIN_PERMS
    ADMIN_CACHE = {OWNER_ID}
    ADMIN_PERMS = {OWNER_ID: {"role":"owner","can_manage_content":True,"can_manage_settings":True,"can_broadcast":True,"can_manage_users":True,"can_manage_admins":True,"can_manage_packages":True,"can_manage_withdraw":True,"can_manage_support":True,"can_manage_tasks":True,"can_manage_notifications":True,"can_view_analytics":True}}
    try:
        rows = await db_fetchall("SELECT * FROM admin_users")
        for r in rows:
            uid = int(r["user_id"])
            ADMIN_CACHE.add(uid)
            ADMIN_PERMS[uid] = dict(r)
    except Exception:
        log.exception("admin cache refresh failed")


def is_admin_id(user_id):
    try: return int(user_id) in ADMIN_CACHE
    except Exception: return False


def has_perm(user_id, perm):
    uid = int(user_id)
    if uid == OWNER_ID: return True
    p = ADMIN_PERMS.get(uid) or {}
    return bool(p.get(perm, False))


async def current_storage_channel_id():
    try:
        st = await get_settings()
        val = st.get("storage_channel_id")
        return int(val) if val else STORAGE_CHANNEL_ID
    except Exception:
        return STORAGE_CHANNEL_ID


async def get_settings():
    try:
        row = await db_fetchone("SELECT * FROM app_settings WHERE id='main' LIMIT 1")
        if row:
            for key in ("protect_content", "maintenance_mode", "show_online", "tutorial_enabled", "comments_enabled", "reactions_enabled", "favorites_enabled", "profile_stats_enabled", "adsgram_enabled", "monetag_enabled", "welcome_manager_enabled", "join_request_welcome_enabled", "direct_join_welcome_enabled", "leave_inbox_enabled", "auto_approve_join_requests", "welcome_video_button_enabled", "welcome_start_button_enabled", "welcome_rejoin_button_enabled"):
                if key in row:
                    row[key] = bool(row[key])
            return row
    except Exception:
        log.exception("settings load failed")
    return {
        "auto_delete_minutes": 20,
        "protect_content": True,
        "broadcast_button_text": "▶ ভিডিও ওপেন করুন",
        "maintenance_mode": False,
        "brand_name": "Bangladesh Viral Video",
        "web_app_url": DEFAULT_MINI_APP_URL,
        "bot_menu_button_text": DEFAULT_MENU_BUTTON_TEXT,
        "welcome_text": "👋 স্বাগতম! নিচের বাটন থেকে ভিডিও অ্যাপ খুলুন।",
        "show_online": True,
        "tutorial_enabled": True,
        "tutorial_video_code": None,
        "tutorial_caption": "🎓 ভিডিও কীভাবে দেখবেন\n\nএই ছোট ভিডিওটি দেখে নিন। তারপর নিচের বাটন থেকে ভিডিও অ্যাপ খুলে আপনার পছন্দের ভিডিও দেখুন।",
        "tutorial_button_text": "🎬 ভিডিও দেখতে শুরু করুন",
        "storage_channel_id": STORAGE_CHANNEL_ID,
        "maintenance_message": "⚙️ সিস্টেমটি এখন Maintenance Mode-এ আছে। পরে আবার চেষ্টা করুন।",
        "support_url": None, "join_channel_url": None, "start_button_text": "🎬 ভিডিও দেখতে শুরু করুন",
        "comments_enabled": True, "reactions_enabled": True, "favorites_enabled": True, "profile_stats_enabled": True,
        "adsgram_enabled": True, "adsgram_block_id": "int-45179", "required_ads_default": 1,
        "ad_button_text": "📢 Ad দেখুন", "ad_unlock_text": "🔓 ভিডিও আনলক করুন",
        "monetization_mode": "both", "monetag_enabled": True, "monetag_zone_id": "11404425",
        "monetag_required_default": 1, "monetag_button_text": "💰 Monetag Ad দেখুন", "monetag_wait_seconds": 20,
        "welcome_manager_enabled": True,
        "join_request_welcome_enabled": True,
        "direct_join_welcome_enabled": True,
        "leave_inbox_enabled": True,
        "auto_approve_join_requests": False,
        "join_welcome_text": "👋 স্বাগতম! আমাদের ভিডিও কমিউনিটিতে আপনাকে স্বাগতম। নিচের বাটন থেকে ভিডিও অ্যাপ খুলুন।",
        "leave_inbox_text": "😢 আপনি আমাদের গ্রুপ/চ্যানেল থেকে বের হয়ে গেছেন। নতুন ভিডিও মিস না করতে আবার যুক্ত হতে পারেন।",
        "welcome_video_button_text": "🎬 ভিডিও ওপেন করুন",
        "welcome_video_button_url": None,
        "welcome_video_button_enabled": True,
        "welcome_start_button_text": "🚀 Start Bot",
        "welcome_start_button_url": None,
        "welcome_start_button_enabled": True,
        "welcome_rejoin_button_text": "🔄 আবার Join করুন",
        "welcome_rejoin_button_url": None,
        "welcome_rejoin_button_enabled": True,
        "video_access_title": "🎁 Video Access",
        "video_access_instruction": "ভিডিওটি দেখার জন্য প্রয়োজনীয় Ad গুলো দেখুন",
        "ad_progress_seen_text": "আপনার {completed}টি Ad দেখা হয়েছে ✅",
        "ad_progress_remaining_text": "আরও {remaining}টি Ad বাকি — ভিডিও দেখতে বাকি Ad গুলো দেখুন।",
        "ad_watch_button_text": "📢 Ad দেখুন",
        "all_ads_done_text": "✅ সব Ad সম্পন্ন হয়েছে\nআপনার ভিডিও আনলক হয়েছে",
        "watch_video_button_text": "🎬 ভিডিও দেখুন",
        "video_access_secure_text": "🛡️ 100% নিরাপদ • আপনার তথ্য গোপন রাখা হয়",
        "mini_bot_package_message": "🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}\n\nনতুন ভিডিও দেখতে নিচের বাটনে ক্লিক করুন 👇",
        "mini_bot_package_button_text": "🎬 ভিডিও দেখুন",
        "video_bot_package_message": "🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}\n\nনতুন ভিডিও দেখতে নিচের বাটনে ক্লিক করুন 👇",
        "video_bot_package_button_text": "🎬 ভিডিও দেখুন",
        "notification_bot_package_message": "🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}\n\nনতুন ভিডিও দেখতে নিচের বাটনে ক্লিক করুন 👇",
        "notification_bot_package_button_text": "🎬 ভিডিও দেখুন",
        "channel_package_message": "🔥 New Video Package\n\nPackage Name: {package_name}\n\nTotal Video: {total_video}",
        "channel_package_button_text": "ভিডিও দেখুন",
    }


async def save_mini_bot_user(message: Message):
    u = message.from_user
    if not u:
        return
    try:
        await db_execute(
            """INSERT INTO mini_bot_users(user_id,username,first_name,last_name,is_active,last_seen_at)
               VALUES(%s,%s,%s,%s,TRUE,%s)
               ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,
               last_name=EXCLUDED.last_name,is_active=TRUE,last_seen_at=EXCLUDED.last_seen_at""",
            (u.id, u.username, u.first_name, u.last_name, utcnow_sql()),
        )
    except Exception:
        log.exception("save mini bot user failed")


async def save_user(message: Message):
    u = message.from_user
    if not u:
        return
    try:
        await db_execute(
            """INSERT INTO bot_users(user_id,username,first_name,last_name,is_active,last_seen_at)
               VALUES(%s,%s,%s,%s,TRUE,%s)
               ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,
               last_name=EXCLUDED.last_name,is_active=TRUE,last_seen_at=EXCLUDED.last_seen_at""",
            (u.id, u.username, u.first_name, u.last_name, utcnow_sql()),
        )
    except Exception:
        log.exception("save user failed")


async def save_video_bot_user(message: Message):
    u = message.from_user
    if not u:
        return
    try:
        await db_execute(
            """INSERT INTO video_bot_users(user_id,username,first_name,last_name,is_active,last_seen_at)
               VALUES(%s,%s,%s,%s,TRUE,%s)
               ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,
               last_name=EXCLUDED.last_name,is_active=TRUE,last_seen_at=EXCLUDED.last_seen_at""",
            (u.id, u.username, u.first_name, u.last_name, utcnow_sql()),
        )
    except Exception:
        log.exception("save video bot user failed")


async def lookup_video(code: str):
    """Resolve a storage mapping. V8 also recovers historical video_N links on demand.

    Older builds could generate a deep link before/without persisting video_storage.
    Since video_N is derived from the Telegram channel message id, we can safely
    reconstruct that mapping against the configured private storage channel.
    """
    try:
        rec = await db_fetchone("SELECT * FROM video_storage WHERE video_code=%s LIMIT 1", (code,))
        if rec:
            return rec
        m = re.fullmatch(r"video_(\d+)", code or "")
        if not m:
            return None
        msg_id = int(m.group(1))
        if msg_id <= 0:
            return None
        return {"video_code": code, "channel_id": await current_storage_channel_id(), "message_id": msg_id, "recovered": True}
    except Exception:
        log.exception("video lookup failed")
        return None


def valid_webapp_url(url: str) -> bool:
    return bool(url and url.lower().startswith("https://"))


async def sync_menu_button(force_log: bool = False):
    settings = await get_settings()
    url = (settings.get("web_app_url") or DEFAULT_MINI_APP_URL or "").strip()
    text = (settings.get("bot_menu_button_text") or DEFAULT_MENU_BUTTON_TEXT or "🎬 Video open").strip()[:64]
    if not valid_webapp_url(url):
        if force_log:
            log.warning("Menu button not set: MINI_APP_URL/web_app_url must be HTTPS")
        return False
    try:
        await mini_bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=text, web_app=WebAppInfo(url=url)))
        await mini_bot.set_my_commands([BotCommand(command="start", description="Mini App খুলুন")])
        if force_log:
            log.info("Telegram menu button set: %s -> %s", text, url)
        return True
    except Exception:
        log.exception("menu button sync failed")
        return False


async def webapp_keyboard(settings=None):
    """CTA used by notification/community bot. Always route users to the Mini Bot.
    A WebAppInfo button from this bot would sign initData with the wrong bot token.
    """
    label = ((settings or {}).get("bot_menu_button_text") if settings else None) or DEFAULT_MENU_BUTTON_TEXT
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=str(label)[:64], url=f"https://t.me/{MINI_BOT_USERNAME}?start=home")
    ]])


async def mini_webapp_keyboard(settings=None):
    settings = settings or await get_settings()
    url = (settings.get("web_app_url") or DEFAULT_MINI_APP_URL or "").strip()
    label = (settings.get("bot_menu_button_text") or DEFAULT_MENU_BUTTON_TEXT or "🎬 Video open").strip()[:64]
    if valid_webapp_url(url):
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))]])
    return None

async def delete_later(chat_id: int, message_id: int, minutes: int):
    await asyncio.sleep(max(1, minutes) * 60)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        log.warning("auto delete failed chat=%s msg=%s", chat_id, message_id)



async def reset_expired_video_access(user_id: int, video_code: str, settings=None) -> bool:
    """Expire an old delivery cycle and make the Mini App package locked again.

    When the latest delivered copy is older than auto_delete_minutes:
      - old delivered rows become delivered=FALSE
      - AdsGram/Monetag completion rows for that package are cleared
      - unfinished ad sessions are cleared
    The package itself remains published, so the user can watch ads and unlock again.
    """
    settings = settings or await get_settings()
    mins = max(1, int(settings.get("auto_delete_minutes") or 20))
    latest = await db_fetchone(
        "SELECT created_at FROM video_requests WHERE user_id=%s AND video_code=%s "
        "AND delivered=TRUE ORDER BY created_at DESC LIMIT 1",
        (int(user_id), str(video_code)),
    )
    if not latest or not latest.get("created_at"):
        return False
    if latest["created_at"] > utcnow_sql() - timedelta(minutes=mins):
        return False

    pub = await db_fetchone("SELECT id FROM videos WHERE video_code=%s LIMIT 1", (str(video_code),))
    await db_execute(
        "UPDATE video_requests SET delivered=FALSE WHERE user_id=%s AND video_code=%s AND delivered=TRUE",
        (int(user_id), str(video_code)),
    )
    if pub:
        await db_execute(
            "DELETE FROM ad_completions WHERE user_id=%s AND video_id=%s",
            (int(user_id), pub["id"]),
        )
        await db_execute(
            "DELETE FROM ad_sessions WHERE user_id=%s AND video_id=%s",
            (int(user_id), pub["id"]),
        )
        await db_execute(
            "INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) "
            "VALUES(%s,%s,%s,'access_expired_relocked',%s)",
            (int(user_id), pub["id"], str(video_code), utcnow_sql()),
        )
    return True


async def reset_all_expired_access_for_user(user_id: int, settings=None):
    """Relock every package whose latest delivered copy has expired."""
    settings = settings or await get_settings()
    mins = max(1, int(settings.get("auto_delete_minutes") or 20))
    cutoff = utcnow_sql() - timedelta(minutes=mins)
    rows = await db_fetchall(
        """SELECT video_code, MAX(created_at) AS last_delivered
           FROM video_requests
           WHERE user_id=%s AND delivered=TRUE
           GROUP BY video_code
           HAVING MAX(created_at) <= %s""",
        (int(user_id), cutoff),
    )
    for row in rows:
        await reset_expired_video_access(int(user_id), row["video_code"], settings)


async def deliver_video(message: Message, code: str):
    settings = await get_settings()
    if settings.get("maintenance_mode") and not is_admin_id(message.from_user.id):
        await message.answer(settings.get("maintenance_message") or "⚙️ সিস্টেমটি এখন Maintenance Mode-এ আছে। পরে আবার চেষ্টা করুন।")
        return

    rec = await lookup_video(code)
    if not rec:
        await message.answer("❌ এই ভিডিওটি পাওয়া যায়নি বা সরানো হয়েছে।")
        return
    try:
        if await reset_expired_video_access(message.from_user.id, code, settings):
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔒 আবার Ad দেখে ভিডিও আনলক করুন",
                    url=f"https://t.me/{MINI_BOT_USERNAME}?start=home"
                )
            ]])
            await message.answer(
                "🔒 আগের ভিডিও Access-এর সময় শেষ হয়েছে।\n\nMini Bot-এ ভিডিওটি আবার Lock করা হয়েছে। "
                "আবার দেখতে নিচের বাটনে গিয়ে Ad সম্পন্ন করে Unlock করুন।",
                reply_markup=kb
            )
            return
    except Exception:
        log.exception("V20 expired-video check failed")

    try:
        sent = await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=int(rec["channel_id"]),
            message_id=int(rec["message_id"]),
            protect_content=bool(settings.get("protect_content", True)),
        )
        if rec.get("recovered"):
            await db_execute(
                "INSERT INTO video_storage(video_code,channel_id,message_id,created_at) VALUES(%s,%s,%s,%s) ON CONFLICT (video_code) DO NOTHING",
                (code, int(rec["channel_id"]), int(rec["message_id"]), utcnow_sql()),
            )
        minutes = int(settings.get("auto_delete_minutes") or 20)
        notice = await message.answer(f"✅ ভিডিও পাঠানো হয়েছে।\n⏳ {minutes} মিনিট পরে ভিডিওটি অটোমেটিক ডিলিট হবে।")
        delete_at = utcnow_sql() + timedelta(minutes=max(1, minutes))
        # Durable queue: survives Render restart/sleep and is processed again on startup.
        await db_execute(
            "INSERT INTO delete_queue(chat_id,message_id,delete_at,status,created_at) VALUES(%s,%s,%s,'pending',%s)",
            (message.chat.id, sent.message_id, delete_at, utcnow_sql()),
        )
        await db_execute(
            "INSERT INTO delete_queue(chat_id,message_id,delete_at,status,created_at) VALUES(%s,%s,%s,'pending',%s)",
            (message.chat.id, notice.message_id, delete_at, utcnow_sql()),
        )
        await db_execute(
            "INSERT INTO video_requests(user_id,video_code,delivered,created_at) VALUES(%s,%s,TRUE,%s)",
            (message.from_user.id, code, utcnow_sql()),
        )
        pub = await db_fetchone("SELECT id FROM videos WHERE video_code=%s LIMIT 1", (code,))
        await db_execute(
            "INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) VALUES(%s,%s,%s,'delivered',%s)",
            (message.from_user.id, pub["id"] if pub else None, code, utcnow_sql()),
        )
        try:
            vcfg = await v20_video_buttons()
            cmsg = (vcfg.get("custom_message") or "").strip()
            if cmsg:
                extra = await message.answer(cmsg, reply_markup=v20_button_keyboard(vcfg, False))
                await db_execute("INSERT INTO delete_queue(chat_id,message_id,delete_at,status,created_at) VALUES(%s,%s,%s,'pending',%s)", (message.chat.id, extra.message_id, delete_at, utcnow_sql()))
        except Exception:
            log.exception("V20 custom video message failed")
    except Exception:
        log.exception("copy video failed")
        await message.answer("❌ ভিডিও পাঠানো যায়নি। Storage Channel permission চেক করুন।")


async def deliver_video_from_video_bot(message: Message, code: str):
    settings = await get_settings()
    rec = await lookup_video(code)
    if not rec:
        await message.answer("❌ এই ভিডিওটি পাওয়া যায়নি বা সরানো হয়েছে।")
        return
    try:
        if await reset_expired_video_access(message.from_user.id, code, settings):
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔒 আবার Ad দেখে ভিডিও আনলক করুন",
                    url=f"https://t.me/{MINI_BOT_USERNAME}?start=home"
                )
            ]])
            await video_bot.send_message(
                message.chat.id,
                "🔒 আগের ভিডিও Access-এর সময় শেষ হয়েছে।\n\nMini Bot-এ ভিডিওটি আবার Lock করা হয়েছে। "
                "আবার দেখতে নিচের বাটনে গিয়ে Ad সম্পন্ন করে Unlock করুন।",
                reply_markup=kb,
                protect_content=True
            )
            return
    except Exception:
        log.exception("V20 video-bot expired-video check failed")
    try:
        sent = await video_bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=int(rec["channel_id"]),
            message_id=int(rec["message_id"]),
            protect_content=bool(settings.get("protect_content", True)),
        )
        minutes = int(settings.get("auto_delete_minutes") or 20)
        notice = await video_bot.send_message(message.chat.id, f"✅ ভিডিও পাঠানো হয়েছে।\n⏳ {minutes} মিনিট পরে ভিডিওটি অটোমেটিক ডিলিট হবে।")
        delete_at = utcnow_sql() + timedelta(minutes=max(1, minutes))
        for mid in (sent.message_id, notice.message_id):
            await db_execute(
                "INSERT INTO delete_queue(bot_kind,chat_id,message_id,delete_at,status,created_at) VALUES('video',%s,%s,%s,'pending',%s)",
                (message.chat.id, mid, delete_at, utcnow_sql()),
            )
        await db_execute(
            "INSERT INTO video_requests(user_id,video_code,delivered,created_at) VALUES(%s,%s,TRUE,%s)",
            (message.from_user.id, code, utcnow_sql()),
        )
        pub = await db_fetchone("SELECT id FROM videos WHERE video_code=%s LIMIT 1", (code,))
        await db_execute(
            "INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) VALUES(%s,%s,%s,'delivered_video_bot',%s)",
            (message.from_user.id, pub["id"] if pub else None, code, utcnow_sql()),
        )
        try:
            vcfg = await v20_video_buttons()
            cmsg = (vcfg.get("custom_message") or "").strip()
            if cmsg:
                extra = await video_bot.send_message(message.chat.id, cmsg, reply_markup=v20_button_keyboard(vcfg, False), protect_content=True)
                await db_execute("INSERT INTO delete_queue(bot_kind,chat_id,message_id,delete_at,status,created_at) VALUES('video',%s,%s,%s,'pending',%s)", (message.chat.id, extra.message_id, delete_at, utcnow_sql()))
        except Exception:
            log.exception("V20 video-bot custom message failed")
    except Exception:
        log.exception("video bot copy failed")
        await message.answer("❌ ভিডিও পাঠানো যায়নি। Video Bot-কে Storage Channel-এর Admin করুন।")


async def _delete_previous_tutorial(bot_instance, bot_role: str, user_id: int):
    """Delete only the previous tutorial card sent by this bot to this user."""
    try:
        old = await db_fetchone(
            "SELECT chat_id,message_id FROM tutorial_last_messages WHERE bot_role=%s AND user_id=%s",
            (bot_role, int(user_id)),
        )
        if old:
            try:
                await bot_instance.delete_message(int(old["chat_id"]), int(old["message_id"]))
            except Exception:
                # Already deleted / too old / unavailable is fine.
                pass
            await db_execute(
                "DELETE FROM tutorial_last_messages WHERE bot_role=%s AND user_id=%s",
                (bot_role, int(user_id)),
            )
    except Exception:
        log.exception("%s previous tutorial cleanup failed", bot_role)


def _tutorial_video_code(value) -> str:
    """Accept video_105 OR a full Telegram deep link containing start=video_105."""
    raw = str(value or "").strip()
    m = re.search(r"(video_[A-Za-z0-9_-]+)", raw)
    return m.group(1) if m else ""


async def send_start_video(
    bot_instance,
    message,
    settings,
    bot_role="start",
    reply_markup=None,
    fallback_caption=None,
):
    """Send ONE clean Tutorial card: video + editable text + button.

    Runs on /start for Mini Bot, Notification Bot and Video Bot.
    Before sending, the previous tutorial card from the same bot/user is deleted.
    """
    try:
        cfg = await db_fetchone("SELECT * FROM tutorial_settings WHERE id='main'") or {}
        if not cfg.get("enabled", True):
            return False

        code = _tutorial_video_code(cfg.get("video_code"))
        if not code:
            return False
        rec = await lookup_video(code)
        if not rec:
            log.warning("%s tutorial mapping missing: %s", bot_role, code)
            return False

        title = str(cfg.get("title") or "").strip()
        description = str(cfg.get("description") or "").strip()
        caption = "\n\n".join(x for x in (title, description) if x).strip()
        if not caption:
            caption = str(fallback_caption or "").strip() or "🎬 ভিডিও দেখার নিয়ম"

        button_text = str(cfg.get("button_text") or "🏠 Home Page").strip()[:64]

        # Keep the destination appropriate for each bot, but never put Ad controls in Video Bot.
        if bot_role == "video":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=button_text,
                    url=f"https://t.me/{MINI_BOT_USERNAME}?start=home"
                )
            ]])
        else:
            kb = reply_markup
            if kb and button_text:
                try:
                    rows = []
                    for row in kb.inline_keyboard:
                        new_row = []
                        for b in row:
                            data = b.model_dump(exclude_none=True)
                            data["text"] = button_text
                            new_row.append(InlineKeyboardButton(**data))
                        rows.append(new_row)
                    kb = InlineKeyboardMarkup(inline_keyboard=rows)
                except Exception:
                    kb = reply_markup

        await _delete_previous_tutorial(
            bot_instance, bot_role, int(message.from_user.id)
        )

        sent = await bot_instance.copy_message(
            chat_id=message.chat.id,
            from_chat_id=int(rec["channel_id"]),
            message_id=int(rec["message_id"]),
            caption=caption,
            reply_markup=kb,
            protect_content=True,
        )
        sent_id = int(getattr(sent, "message_id", 0) or 0)
        if sent_id:
            await db_execute(
                """INSERT INTO tutorial_last_messages(bot_role,user_id,chat_id,message_id,updated_at)
                   VALUES(%s,%s,%s,%s,CURRENT_TIMESTAMP)
                   ON CONFLICT(bot_role,user_id) DO UPDATE SET
                   chat_id=EXCLUDED.chat_id,message_id=EXCLUDED.message_id,
                   updated_at=CURRENT_TIMESTAMP""",
                (bot_role, int(message.from_user.id), int(message.chat.id), sent_id),
            )
        return True
    except Exception:
        log.exception("%s tutorial send failed", bot_role)
        return False


@mini_dp.message(CommandStart())
async def mini_bot_start_handler(message: Message):
    await save_mini_bot_user(message)
    settings = await get_settings()
    if settings.get("maintenance_mode") and not is_admin_id(message.from_user.id):
        await message.answer(settings.get("maintenance_message") or "⚙️ সিস্টেমটি Maintenance Mode-এ আছে।")
        return
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    welcome = settings.get("welcome_text") or "👋 স্বাগতম! নিচের বাটন থেকে Premium Video Gallery খুলুন।"
    if payload.startswith("admin") and is_admin_id(message.from_user.id):
        welcome = "🛠 Central Control Center প্রস্তুত। নিচের বাটন থেকে Admin Panel খুলুন।"
    elif payload.startswith("welcome"):
        welcome = settings.get("join_welcome_text") or welcome
    kb = await mini_webapp_keyboard(settings)
    # V21.1: Every Mini Bot /start refreshes the Tutorial card.
    sent = await send_start_video(mini_bot, message, settings, "mini", reply_markup=kb, fallback_caption=welcome)
    if sent:
        return
    await message.answer(welcome, reply_markup=kb)


@mini_dp.message(F.chat.type == "private")
async def mini_bot_private_handler(message: Message):
    await save_mini_bot_user(message)
    if (message.text or "").strip().lower() in {"menu", "app", "video", "ভিডিও"}:
        await message.answer("🎬 Premium Video Gallery খুলুন", reply_markup=await mini_webapp_keyboard())


@video_dp.message(CommandStart())
async def video_bot_start_handler(message: Message):
    await save_video_bot_user(message)
    settings = await get_settings()
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    # Always refresh the clean Tutorial card first.
    await send_start_video(
        video_bot,
        message,
        settings,
        "video",
        fallback_caption="🎬 ভিডিও দেখার নিয়ম"
    )

    # A deep link such as https://t.me/Viral_video99_bot?start=video_105
    # opens the protected video directly in Video Bot. No Ad UI is shown here.
    code = _tutorial_video_code(payload)
    if code:
        await deliver_video_from_video_bot(message, code)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🏠 Mini Bot খুলুন",
            url=f"https://t.me/{MINI_BOT_USERNAME}?start=home"
        )
    ]])
    # If tutorial is disabled/missing, keep a minimal fallback.
    cfg = await db_fetchone("SELECT enabled,video_code FROM tutorial_settings WHERE id='main'") or {}
    if not cfg.get("enabled", True) or not _tutorial_video_code(cfg.get("video_code")):
        await message.answer(
            "👋 এই Bot protected video delivery-এর জন্য।",
            reply_markup=kb
        )


@video_dp.message(F.chat.type == "private")
async def video_bot_private_handler(message: Message):
    await save_video_bot_user(message)
    text = (message.text or "").strip()
    code = _tutorial_video_code(text)
    if code:
        await deliver_video_from_video_bot(message, code)


async def send_start_tutorial(message: Message, settings):
    """Send the admin-selected tutorial video on normal /start.

    The tutorial video is stored in the same private storage channel and selected
    by its video_code (for example video_145).
    """
    if not settings.get("tutorial_enabled", True):
        return False
    code = (settings.get("tutorial_video_code") or "").strip()
    if not code:
        return False
    rec = await lookup_video(code)
    if not rec:
        log.warning("tutorial video mapping missing: %s", code)
        return False
    kb = await webapp_keyboard(settings)
    if kb:
        # Notification Bot must route to Mini Bot; Mini App initData is signed by Mini Bot.
        label = (settings.get("tutorial_button_text") or "🎬 ভিডিও দেখতে শুরু করুন").strip()[:64]
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, url=f"https://t.me/{MINI_BOT_USERNAME}?start=home")]])
    caption = settings.get("tutorial_caption") or "🎓 ভিডিও কীভাবে দেখবেন"
    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=int(rec["channel_id"]),
            message_id=int(rec["message_id"]),
            caption=caption,
            reply_markup=kb,
            protect_content=True,
        )
        return True
    except Exception:
        log.exception("tutorial send failed")
        return False


@dp.message(CommandStart())
async def start_handler(message: Message):
    await save_user(message)
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload.startswith("video_"):
        await deliver_video(message, payload)
        return

    settings = await get_settings()
    if settings.get("maintenance_mode") and not is_admin_id(message.from_user.id):
        await message.answer(settings.get("maintenance_message") or "⚙️ সিস্টেমটি এখন Maintenance Mode-এ আছে। পরে আবার চেষ্টা করুন।")
        return

    # V20.7: Notification Bot normal /start = Start Video + existing Welcome Text + existing Buttons.
    # Video Bot remains unchanged and never receives this Start Video.
    welcome = settings.get("welcome_text") or (
        f"👋 স্বাগতম {settings.get('brand_name','Bangladesh Viral Video')} Bot-এ।\n\n"
        "নিচের বাটন থেকে Mini App খুলুন এবং আপনার পছন্দের ভিডিও দেখুন।"
    )
    kb = await webapp_keyboard(settings)
    sent = await send_start_video(bot, message, settings, "notification", reply_markup=kb, fallback_caption=welcome)
    if sent:
        return
    await message.answer(welcome, reply_markup=kb)


@dp.message(F.chat.type == "private")
async def private_text_handler(message: Message):
    await save_user(message)
    text = (message.text or "").strip()
    m = VIDEO_CODE_RE.search(text)
    if m:
        await deliver_video(message, m.group(1))
        return
    if message.from_user and is_admin_id(message.from_user.id) and text == "/adminhelp":
        await message.answer(
            "Storage Channel-এ video upload করুন → Bot deep link তৈরি করে Owner inbox-এ পাঠাবে।\n"
            "Mini App URL ও Menu Button নাম Admin Settings থেকে পরিবর্তন করা যায়।"
        )


async def _telegram_thumb_data_uri(message: Message):
    """Return Telegram-generated media thumbnail as a compact data URI when available."""
    media = message.video or message.animation or message.document
    thumb = getattr(media, "thumbnail", None) if media else None
    if not thumb:
        return ""
    try:
        f = await bot.get_file(thumb.file_id)
        buf = io.BytesIO()
        await bot.download_file(f.file_path, destination=buf)
        raw = buf.getvalue()
        if not raw:
            return ""
        mime = "image/jpeg"
        if raw.startswith(b"\x89PNG"):
            mime = "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception:
        log.exception("auto thumbnail download failed")
        return ""


def _draft_title_and_overlay(caption: str):
    caption = (caption or "").strip()
    if not caption:
        return "নতুন ভিডিও", ""
    clean = re.sub(r"\s+", " ", caption).strip()
    title = clean[:180]
    overlay = clean[:110]
    return title, overlay


def _channel_uploader_label(message: Message) -> str:
    """Best-effort uploader label. Telegram channel posts only expose the admin name when signatures are enabled."""
    author = (getattr(message, "author_signature", None) or "").strip()
    if author:
        return author[:255]
    sender_chat = getattr(message, "sender_chat", None)
    title = (getattr(sender_chat, "title", None) or "").strip() if sender_chat else ""
    return title[:255] if title else "Channel Upload"


async def _notify_all_admins_new_video(*, code: str, deep_link: str, caption: str, auto_thumb: str, channel_id: int, uploader_label: str):
    """Send the same new-video card to Owner + every configured admin."""
    settings = await get_settings()
    web_url = (settings.get("web_app_url") or DEFAULT_MINI_APP_URL or "").strip()
    rows = []
    try:
        rows = await db_fetchall("SELECT user_id,display_name FROM admin_users")
    except Exception:
        log.exception("admin notification target load failed")
    targets = {OWNER_ID: "Owner"}
    for r in rows:
        try:
            targets[int(r["user_id"])] = r.get("display_name") or "Admin"
        except Exception:
            pass

    note = (
        f"✅ <b>নতুন ভিডিও detect হয়েছে</b>\n\n"
        f"🆔 Video Code: <code>{code}</code>\n"
        f"📦 Storage Channel: <code>{channel_id}</code>\n"
        f"👤 Uploaded by: <b>{uploader_label}</b>\n"
        f"🤖 Video Bot Link:\n<code>{deep_link}</code>\n\n"
        "🖼️ Full video থেকে Telegram-এর auto preview frame নিয়ে 16:9 thumbnail draft তৈরি হয়েছে।\n"
        "✍️ Channel caption thumbnail-এর premium colored overlay হিসেবে থাকবে।\n"
        "⚙️ Admin → ভিডিও ম্যানেজ থেকে Auto Draft ব্যবহার করুন; চাইলে Manual Thumbnail দিয়ে replace করতে পারবেন।\n\n"
        f"📝 Caption: {caption[:500]}"
    )

    for uid in targets:
        try:
            buttons = [[InlineKeyboardButton(text="🔗 Video Bot Link খুলুন", url=deep_link)]]
            if valid_webapp_url(web_url):
                buttons.append([InlineKeyboardButton(text="⚙️ Admin Panel খুলুন", url=f"https://t.me/{MINI_BOT_USERNAME}?start=admin")])
            # V20: tutorial action is visible only to the Owner.
            if int(uid) == OWNER_ID:
                buttons.append([InlineKeyboardButton(text="🎓 Tutorial Video হিসেবে সেট করুন", callback_data=f"set_tutorial:{code}")])
            kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            if auto_thumb:
                header, encoded = auto_thumb.split(",", 1)
                photo = BufferedInputFile(base64.b64decode(encoded), filename=f"{code}_thumb.jpg")
                await bot.send_photo(uid, photo=photo, caption=note, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await bot.send_message(uid, note, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            log.exception("new video admin notification failed uid=%s", uid)



async def get_managed_chat(chat_id: int):
    try:
        return await db_fetchone("SELECT * FROM managed_chats WHERE chat_id=%s AND enabled=TRUE LIMIT 1", (int(chat_id),))
    except Exception:
        log.exception("managed chat lookup failed chat=%s", chat_id)
        return None


def _normalize_button_url(value, fallback=None):
    """Normalize an Admin-entered Telegram/HTTP button URL.

    Empty values use the supplied fallback. A bare t.me/... value is upgraded to
    https:// automatically so Admins do not need to remember the scheme.
    """
    value = str(value or "").strip()
    if not value:
        value = str(fallback or "").strip()
    if not value:
        return None
    if value.startswith("t.me/") or value.startswith("www.t.me/"):
        value = "https://" + value
    if value.startswith(("https://", "http://", "tg://")):
        return value
    return None


async def welcome_keyboard(settings, chat_row=None, include_rejoin=False):
    rows = []
    default_mini_start = f"https://t.me/{MINI_BOT_USERNAME}?start=welcome"

    if settings.get("welcome_video_button_enabled", True):
        video_text = (settings.get("welcome_video_button_text") or "🎬 ভিডিও ওপেন করুন").strip()[:64]
        video_url = _normalize_button_url(settings.get("welcome_video_button_url"), default_mini_start)
        if video_url:
            rows.append([InlineKeyboardButton(text=video_text, url=video_url)])

    if settings.get("welcome_start_button_enabled", True):
        start_text = (settings.get("welcome_start_button_text") or "🚀 Start Bot").strip()[:64]
        start_url = _normalize_button_url(settings.get("welcome_start_button_url"), default_mini_start)
        if start_url:
            rows.append([InlineKeyboardButton(text=start_text, url=start_url)])

    if include_rejoin and settings.get("welcome_rejoin_button_enabled", True):
        rejoin_text = (settings.get("welcome_rejoin_button_text") or "🔄 আবার Join করুন").strip()[:64]
        # Global Admin URL wins; if blank, use this managed chat's Join/Invite URL.
        chat_join_url = (chat_row or {}).get("join_url") if chat_row else None
        rejoin_url = _normalize_button_url(settings.get("welcome_rejoin_button_url"), chat_join_url)
        if rejoin_url:
            rows.append([InlineKeyboardButton(text=rejoin_text, url=rejoin_url)])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def log_join_leave_event(chat_id, user_id, event_type, sent=False, error=None):
    try:
        await db_execute(
            "INSERT INTO join_leave_events(chat_id,user_id,event_type,inbox_sent,error_text,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (int(chat_id), int(user_id), event_type, bool(sent), (str(error)[:1000] if error else None), utcnow_sql()),
        )
    except Exception:
        log.exception("join/leave event log failed")


async def send_join_leave_inbox(target_chat_id: int, user_id: int, chat_row, event_type: str):
    settings = await get_settings()
    if not settings.get("welcome_manager_enabled", True):
        return False
    if event_type == "join_request" and (not settings.get("join_request_welcome_enabled", True) or chat_row.get("join_request_welcome") is False):
        return False
    if event_type == "join" and (not settings.get("direct_join_welcome_enabled", True) or chat_row.get("direct_join_welcome") is False):
        return False
    if event_type == "leave" and (not settings.get("leave_inbox_enabled", True) or chat_row.get("leave_welcome") is False):
        return False
    text = settings.get("leave_inbox_text") if event_type == "leave" else settings.get("join_welcome_text")
    if not text:
        text = "👋 স্বাগতম!" if event_type != "leave" else "😢 আপনি গ্রুপ/চ্যানেল থেকে বের হয়েছেন।"
    try:
        text = str(text).replace("{chat_title}", str(chat_row.get("title") or "আমাদের কমিউনিটি"))
    except Exception:
        pass
    kb = await welcome_keyboard(settings, chat_row, include_rejoin=(event_type == "leave"))
    try:
        await bot.send_message(int(target_chat_id), text, reply_markup=kb)
        await log_join_leave_event(chat_row["chat_id"], user_id, event_type, True, None)
        return True
    except Exception as e:
        # For normal join/leave Telegram may reject the DM if the user never started the bot.
        log.info("join/leave inbox not sent event=%s user=%s chat=%s error=%s", event_type, user_id, chat_row.get("chat_id"), e)
        await log_join_leave_event(chat_row["chat_id"], user_id, event_type, False, e)
        return False


@dp.chat_join_request()
async def join_request_handler(req: ChatJoinRequest):
    chat_row = await get_managed_chat(req.chat.id)
    if not chat_row:
        return
    settings = await get_settings()
    # user_chat_id can be used for a short period while the join request is pending.
    target_chat_id = int(getattr(req, "user_chat_id", 0) or req.from_user.id)
    await send_join_leave_inbox(target_chat_id, req.from_user.id, chat_row, "join_request")
    auto_approve = chat_row.get("auto_approve")
    if auto_approve is None:
        auto_approve = settings.get("auto_approve_join_requests", False)
    if auto_approve:
        try:
            await bot.approve_chat_join_request(req.chat.id, req.from_user.id)
        except Exception:
            log.exception("auto approve join request failed chat=%s user=%s", req.chat.id, req.from_user.id)


@dp.chat_member()
async def chat_member_handler(event: ChatMemberUpdated):
    user_obj = getattr(event.new_chat_member, "user", None)
    if not user_obj or user_obj.is_bot:
        return
    chat_row = await get_managed_chat(event.chat.id)
    if not chat_row:
        return
    old_status = getattr(getattr(event.old_chat_member, "status", None), "value", str(getattr(event.old_chat_member, "status", "")))
    new_status = getattr(getattr(event.new_chat_member, "status", None), "value", str(getattr(event.new_chat_member, "status", "")))
    active = {"member", "administrator", "creator", "restricted"}
    if old_status in {"left", "kicked"} and new_status in active:
        await send_join_leave_inbox(user_obj.id, user_obj.id, chat_row, "join")
    elif old_status in active and new_status in {"left", "kicked"}:
        await send_join_leave_inbox(user_obj.id, user_obj.id, chat_row, "leave")


@dp.my_chat_member()
async def my_chat_member_handler(event: ChatMemberUpdated):
    new_status = getattr(getattr(event.new_chat_member, "status", None), "value", str(getattr(event.new_chat_member, "status", "")))
    if new_status != "administrator":
        return
    try:
        if int(event.chat.id) == int(await current_storage_channel_id()):
            return
    except Exception:
        pass
    title = getattr(event.chat, "title", None) or str(event.chat.id)
    chat_type = getattr(getattr(event.chat, "type", None), "value", str(getattr(event.chat, "type", "group")))
    try:
        await db_execute(
            """INSERT INTO managed_chats(chat_id,title,chat_type,enabled,created_at,updated_at)
               VALUES(%s,%s,%s,TRUE,%s,%s)
               ON CONFLICT(chat_id) DO UPDATE SET title=EXCLUDED.title,chat_type=EXCLUDED.chat_type,updated_at=EXCLUDED.updated_at""",
            (int(event.chat.id), title[:255], chat_type[:30], utcnow_sql(), utcnow_sql()),
        )
        await bot.send_message(OWNER_ID, f"✅ Welcome Manager chat detect করেছে\n\n{title}\n<code>{event.chat.id}</code>\n\nAdmin Panel → Join/Leave থেকে URL ও settings ঠিক করুন।", parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("auto register managed chat failed")


@dp.channel_post()
async def storage_channel_post(message: Message):
    if message.chat.id != await current_storage_channel_id():
        return
    if not (message.video or message.document or message.animation):
        return

    code = f"video_{message.message_id}"
    try:
        await db_execute(
            """INSERT INTO video_storage(video_code,channel_id,message_id,created_at)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT (video_code) DO UPDATE SET channel_id=EXCLUDED.channel_id,message_id=EXCLUDED.message_id""",
            (code, message.chat.id, message.message_id, utcnow_sql()),
        )
    except Exception:
        log.exception("storage map save failed")
        return

    deep_link = f"https://t.me/{VIDEO_BOT_USERNAME}?start={code}"
    caption = message.caption or ""
    draft_title, thumb_text = _draft_title_and_overlay(caption)
    auto_thumb = await _telegram_thumb_data_uri(message)
    try:
        await db_execute(
            """INSERT INTO upload_drafts(video_code,channel_id,message_id,title,caption_text,thumb,deep_link,uploader_label,consumed,created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s)
               ON CONFLICT (video_code) DO UPDATE SET channel_id=EXCLUDED.channel_id,message_id=EXCLUDED.message_id,
               title=EXCLUDED.title,caption_text=EXCLUDED.caption_text,thumb=CASE WHEN EXCLUDED.thumb<>'' THEN EXCLUDED.thumb ELSE upload_drafts.thumb END,
               deep_link=EXCLUDED.deep_link,uploader_label=EXCLUDED.uploader_label,consumed=FALSE,created_at=EXCLUDED.created_at""",
            (code, message.chat.id, message.message_id, draft_title, thumb_text, auto_thumb, deep_link, _channel_uploader_label(message), utcnow_sql()),
        )
    except Exception:
        log.exception("auto draft save failed")

    uploader_label = _channel_uploader_label(message)
    await _notify_all_admins_new_video(
        code=code, deep_link=deep_link, caption=caption, auto_thumb=auto_thumb,
        channel_id=message.chat.id, uploader_label=uploader_label,
    )


@dp.callback_query(F.data.startswith("set_tutorial:"))
async def set_tutorial_callback(query: CallbackQuery):
    if not query.from_user or int(query.from_user.id) != OWNER_ID:
        await query.answer("Owner only", show_alert=True)
        return
    code = (query.data or "").split(":", 1)[1].strip()
    rec = await lookup_video(code)
    if not rec:
        await query.answer("ভিডিও mapping পাওয়া যায়নি", show_alert=True)
        return
    await db_execute("UPDATE app_settings SET tutorial_video_code=%s, tutorial_enabled=TRUE, updated_at=CURRENT_TIMESTAMP WHERE id='main'", (code,))
    await db_execute("""INSERT INTO tutorial_settings(id,video_code,enabled,updated_by,updated_at)
                      VALUES('main',%s,TRUE,%s,CURRENT_TIMESTAMP)
                      ON CONFLICT(id) DO UPDATE SET video_code=EXCLUDED.video_code,enabled=TRUE,
                      updated_by=EXCLUDED.updated_by,updated_at=CURRENT_TIMESTAMP""", (code, OWNER_ID))
    await db_execute("DELETE FROM tutorial_views")
    await query.answer("Tutorial video সেট হয়েছে ✅", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(OWNER_ID, f"✅ <code>{code}</code> এখন /start Tutorial Video হিসেবে সেট করা হয়েছে।", parse_mode=ParseMode.HTML)


def _render_template(text, **values):
    out = str(text or "")
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


async def broadcast_worker():
    while True:
        try:
            settings = await get_settings()
            videos = await db_fetchall(
                "SELECT * FROM videos WHERE published=TRUE AND broadcast_enabled=TRUE AND broadcast_sent=FALSE ORDER BY created_at ASC LIMIT 10"
            )
            for v in videos:
                button_text = settings.get("broadcast_button_text") or "▶ ভিডিও ওপেন করুন"

                # V21.3 package notification URL routing:
                # Mini Bot keeps the original package-specific dynamic link.
                # Video Bot + Notification Bot + selected Channels use a fixed Mini Bot start link.
                mini_package_url = v.get("share_link") or (
                    f"https://t.me/{MINI_BOT_USERNAME}?startapp={v.get('share_code')}"
                    if v.get('share_code') else None
                )
                other_package_url = "https://t.me/Bangladesh_vairal_videobot?start=start"

                if not mini_package_url:
                    continue

                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=button_text, url=mini_package_url)
                ]])
                # Three-bot routing:
                #   Mini Bot         -> TEXT ONLY (no thumbnail)
                #   Video Bot        -> THUMBNAIL + editable caption/button
                #   Notification Bot -> THUMBNAIL + editable caption/button
                mini_users = await db_fetchall("SELECT user_id FROM mini_bot_users WHERE is_active=TRUE")
                video_users = await db_fetchall("SELECT user_id FROM video_bot_users WHERE is_active=TRUE")
                notification_users = await db_fetchall("SELECT user_id FROM bot_users WHERE is_active=TRUE")
                total_video = (await db_fetchone("SELECT COUNT(*) c FROM videos WHERE published=TRUE"))["c"]
                package_name = v.get('title') or 'New Video'
                ok = fail = 0

                def package_keyboard(text_key, fallback, url):
                    label = (settings.get(text_key) or fallback).strip()[:64]
                    return InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text=label, url=url)
                    ]])

                async def send_text_only(client, uid, msg_key, btn_key):
                    text = _render_template(settings.get(msg_key), package_name=package_name, total_video=total_video)
                    if not text.strip():
                        text = f"🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}"
                    await client.send_message(
                        uid,
                        text,
                        reply_markup=package_keyboard(btn_key, "🎬 ভিডিও দেখুন", mini_package_url),
                        protect_content=True
                    )

                async def send_with_thumbnail(client, uid, msg_key, btn_key):
                    text = _render_template(settings.get(msg_key), package_name=package_name, total_video=total_video)
                    if not text.strip():
                        text = f"🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}"
                    kb2 = package_keyboard(btn_key, "🎬 ভিডিও দেখুন", other_package_url)
                    thumb = v.get("thumb") or ""
                    if thumb.startswith("http://") or thumb.startswith("https://"):
                        await client.send_photo(uid, photo=thumb, caption=text, reply_markup=kb2, protect_content=True)
                    elif thumb.startswith("data:image/") and "," in thumb:
                        header, encoded = thumb.split(",", 1)
                        ext = "png" if "png" in header else "jpg"
                        photo = BufferedInputFile(base64.b64decode(encoded), filename=f"thumb.{ext}")
                        await client.send_photo(uid, photo=photo, caption=text, reply_markup=kb2, protect_content=True)
                    else:
                        # If a package has no usable thumbnail, still deliver the notification.
                        await client.send_message(uid, text, reply_markup=kb2, protect_content=True)

                for row in mini_users:
                    uid = int(row["user_id"])
                    try:
                        await send_text_only(mini_bot, uid, "mini_bot_package_message", "mini_bot_package_button_text")
                        ok += 1; await asyncio.sleep(0.05)
                    except Exception:
                        fail += 1
                        await db_execute("UPDATE mini_bot_users SET is_active=FALSE WHERE user_id=%s", (uid,))

                for row in video_users:
                    uid = int(row["user_id"])
                    try:
                        await send_with_thumbnail(video_bot, uid, "video_bot_package_message", "video_bot_package_button_text")
                        ok += 1; await asyncio.sleep(0.05)
                    except Exception:
                        fail += 1
                        await db_execute("UPDATE video_bot_users SET is_active=FALSE WHERE user_id=%s", (uid,))

                for row in notification_users:
                    uid = int(row["user_id"])
                    try:
                        await send_with_thumbnail(bot, uid, "notification_bot_package_message", "notification_bot_package_button_text")
                        ok += 1; await asyncio.sleep(0.05)
                    except Exception:
                        fail += 1
                        await db_execute("UPDATE bot_users SET is_active=FALSE WHERE user_id=%s", (uid,))
                # V20.4 Three-Bot package notification rule:
                #   Mini Bot -> text + button only (never send thumbnail)
                #   Video Bot / Notification Bot -> thumbnail + caption + button
                # Selected groups/channels are published by Notification Bot, so they also get thumbnail.
                channel_ok = channel_fail = 0
                channel_errors = []
                try:
                    channels = await db_fetchall("SELECT id,chat_id,title FROM notification_channels WHERE enabled=TRUE ORDER BY id")
                    ch_caption = _render_template(settings.get("channel_package_message"), package_name=package_name, total_video=total_video)
                    if not ch_caption.strip():
                        ch_caption = f"🔥 New Video Package\n\n📦 Package Name: {package_name}\n🎬 Total Video: {total_video}"
                    ch_btn = (settings.get("channel_package_button_text") or "ভিডিও দেখুন").strip()[:64]
                    ch_kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text=ch_btn, url=other_package_url)
                    ]])
                    thumb = v.get("thumb") or ""
                    for ch in channels:
                        chat_id = int(ch["chat_id"])
                        try:
                            status = await _notification_channel_status(chat_id)
                            if not status.get("ok"):
                                raise RuntimeError(status.get("error") or "Notification Bot cannot post to target")
                            if thumb.startswith("http://") or thumb.startswith("https://"):
                                await bot.send_photo(chat_id, photo=thumb, caption=ch_caption, reply_markup=ch_kb, protect_content=True)
                            elif thumb.startswith("data:image/") and "," in thumb:
                                header, encoded = thumb.split(",", 1)
                                ext = "png" if "png" in header else "jpg"
                                photo = BufferedInputFile(base64.b64decode(encoded), filename=f"package_thumb.{ext}")
                                await bot.send_photo(chat_id, photo=photo, caption=ch_caption, reply_markup=ch_kb, protect_content=True)
                            else:
                                await bot.send_message(chat_id, ch_caption, reply_markup=ch_kb, protect_content=True)
                            channel_ok += 1
                        except Exception as e:
                            channel_fail += 1
                            channel_errors.append(f"{ch.get('title') or chat_id}: {str(e)[:120]}")
                            log.exception("selected-channel notification failed chat=%s", chat_id)
                except Exception as e:
                    channel_fail += 1
                    channel_errors.append(str(e)[:120])
                    log.exception("selected-channel broadcast failed")
                await db_execute("UPDATE videos SET broadcast_sent=TRUE WHERE id=%s", (v["id"],))
                report = (
                    f"📢 Broadcast complete\n{v.get('title','')}\n"
                    f"👤 Bot users sent: {ok}\n❌ Bot users failed: {fail}\n"
                    f"📣 Channels sent: {channel_ok}\n⚠️ Channels failed: {channel_fail}"
                )
                if channel_errors:
                    report += "\n\n" + "\n".join("• " + e for e in channel_errors[:5])
                await bot.send_message(OWNER_ID, report)
        except Exception:
            log.exception("broadcast worker error")
        await asyncio.sleep(POLL_SECONDS)


# ---------- Telegram Mini App authentication ----------
def verify_init_data(init_data: str, max_age=86400):
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", MINI_BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        auth_date = int(pairs.get("auth_date", "0") or 0)
        if auth_date and abs(time.time() - auth_date) > max_age:
            return None
        user_data = json.loads(pairs.get("user", "{}"))
        return user_data if user_data.get("id") else None
    except Exception:
        return None


def request_user(request):
    return verify_init_data(request.headers.get("X-Telegram-Init-Data", ""))


def require_admin(request, perm=None):
    u = request_user(request)
    if not u or not is_admin_id(int(u.get("id", 0))):
        raise web.HTTPForbidden(text="Admin authorization required")
    if perm and not has_perm(int(u.get("id", 0)), perm):
        raise web.HTTPForbidden(text="Admin permission denied")
    return u


def require_admin_any(request, *perms):
    u = require_admin(request)
    uid = int(u.get("id", 0))
    if uid == OWNER_ID or any(has_perm(uid, p) for p in perms):
        return u
    raise web.HTTPForbidden(text="Admin permission denied")


async def json_body(request):
    try:
        return await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")


async def index_handler(request):
    return web.FileResponse(INDEX_FILE)


async def health_handler(request):
    return web.json_response({"ok": True, "service": "viral-video-bot-v20", "database": "postgresql", "mini_bot": MINI_BOT_USERNAME, "notification_bot": BOT_USERNAME, "video_bot": VIDEO_BOT_USERNAME})


async def api_bootstrap(request):
    u = request_user(request)
    is_admin = bool(u and is_admin_id(int(u.get("id", 0))))
    if is_admin:
        videos = await db_fetchall("SELECT * FROM videos WHERE published=TRUE ORDER BY created_at DESC")
    else:
        videos = await db_fetchall("SELECT id,share_code,title,category_id,thumb,thumb_text,published,views,required_ads,featured,trending,created_at FROM videos WHERE published=TRUE ORDER BY created_at DESC")
    for v in videos:
        if v.get("share_code"):
            v["share_link"] = f"https://t.me/{MINI_BOT_USERNAME}?startapp={v['share_code']}"

    categories = await db_fetchall('SELECT id,name,icon,sort_order AS "order" FROM categories ORDER BY sort_order,id')
    viral = await db_fetchall("SELECT id,title,url FROM viral_links ORDER BY created_at DESC")
    settings = await get_settings()
    unlocked = []
    favorites = []
    my_reactions = []
    if u:
        uid=int(u["id"])
        # V21.0: after Telegram auto-delete time expires, make the package locked again.
        await reset_all_expired_access_for_user(uid, settings)
        rows = await db_fetchall("""SELECT DISTINCT v.id FROM video_requests r JOIN videos v ON v.video_code=r.video_code
                                  WHERE r.user_id=%s AND r.delivered=TRUE""", (uid,))
        unlocked = [r["id"] for r in rows]
        favorites = [r["video_id"] for r in await db_fetchall("SELECT video_id FROM favorites WHERE user_id=%s",(uid,))]
        my_reactions = [r["video_id"] for r in await db_fetchall("SELECT video_id FROM reactions WHERE user_id=%s",(uid,))]
    for v in videos:
        v["reaction_count"]=(await db_fetchone("SELECT COUNT(*) c FROM reactions WHERE video_id=%s",(v["id"],)))["c"]
        v["comment_count"]=(await db_fetchone("SELECT COUNT(*) c FROM comments WHERE video_id=%s",(v["id"],)))["c"]
    safe_settings = dict(settings)
    # Credentials never exist in this table, but keep response explicitly UI-only.
    stats = None
    if u and is_admin_id(int(u.get("id", 0))):
        total_users = (await db_fetchone("SELECT COUNT(*) c FROM bot_users"))["c"]
        requests = (await db_fetchone("SELECT COUNT(*) c FROM video_requests"))["c"]
        unlocks=(await db_fetchone("SELECT COUNT(*) c FROM video_requests WHERE delivered=TRUE"))["c"]
        unlocks_today=(await db_fetchone("SELECT COUNT(*) c FROM video_requests WHERE delivered=TRUE AND created_at >= CURRENT_DATE"))["c"]
        ads_watched=(await db_fetchone("SELECT COUNT(*) c FROM ad_completions"))["c"]
        ads_watched_today=(await db_fetchone("SELECT COUNT(*) c FROM ad_completions WHERE created_at >= CURRENT_DATE"))["c"]
        comments_count=(await db_fetchone("SELECT COUNT(*) c FROM comments"))["c"]
        reactions_count=(await db_fetchone("SELECT COUNT(*) c FROM reactions"))["c"]
        stats = {
            "total_users": total_users, "video_requests": requests,
            "unlocks": unlocks, "unlocks_today": unlocks_today,
            "ads_watched": ads_watched, "ads_watched_today": ads_watched_today,
            "comments": comments_count, "reactions": reactions_count
        }
    payload = {
        "videos": videos,
        "categories": categories,
        "viral_links": viral,
        "settings": safe_settings,
        "unlocked": unlocked,
        "favorites": favorites,
        "my_reactions": my_reactions,
        "is_admin": is_admin,
        "stats": stats,
        "bot_username": MINI_BOT_USERNAME,
        "mini_bot_username": MINI_BOT_USERNAME,
        "notification_bot_username": BOT_USERNAME if is_admin else None,
        "video_bot_username": VIDEO_BOT_USERNAME,
        "is_owner": bool(u and int(u.get("id",0)) == OWNER_ID),
        "admin_permissions": ADMIN_PERMS.get(int(u.get("id",0)), {}) if u and is_admin else {},
    }
    return web.Response(text=json.dumps(payload, default=str, ensure_ascii=False), content_type="application/json")


async def api_presence(request):
    u = request_user(request)
    if u:
        await db_execute(
            """INSERT INTO mini_bot_users(user_id,username,first_name,last_name,is_active,last_seen_at)
               VALUES(%s,%s,%s,%s,TRUE,%s)
               ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,is_active=TRUE,last_seen_at=EXCLUDED.last_seen_at""",
            (int(u["id"]), u.get("username"), u.get("first_name"), u.get("last_name"), utcnow_sql()),
        )
        await db_execute(
            """INSERT INTO miniapp_presence(user_id,username,first_name,last_seen_at)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,last_seen_at=EXCLUDED.last_seen_at""",
            (int(u["id"]), u.get("username"), u.get("first_name"), utcnow_sql()),
        )
    online = (await db_fetchone("SELECT COUNT(*) c FROM miniapp_presence WHERE last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '75 seconds'"))["c"]
    today = (await db_fetchone("SELECT COUNT(*) c FROM miniapp_presence WHERE last_seen_at >= CURRENT_DATE"))["c"]
    payload = {"online": online, "today_active": today}
    if u and is_admin_id(int(u.get("id", 0))):
        payload["total_users"] = (await db_fetchone("SELECT COUNT(*) c FROM mini_bot_users"))["c"]
        payload["notification_users"] = (await db_fetchone("SELECT COUNT(*) c FROM bot_users"))["c"]
        payload["video_users"] = (await db_fetchone("SELECT COUNT(*) c FROM video_bot_users"))["c"]
        payload["video_requests"] = (await db_fetchone("SELECT COUNT(*) c FROM video_requests"))["c"]
    return web.json_response(payload)


async def api_increment_view(request):
    video_id = request.match_info["video_id"]
    u = request_user(request)
    await db_execute("UPDATE videos SET views=views+1 WHERE id=%s", (video_id,))
    if u:
        uid = int(u["id"])
        await db_execute(
            """INSERT INTO video_views(user_id,video_id,open_count,created_at,last_seen_at) VALUES(%s,%s,1,%s,%s)
               ON CONFLICT (user_id,video_id) DO UPDATE SET open_count=video_views.open_count+1,last_seen_at=EXCLUDED.last_seen_at""",
            (uid, video_id, utcnow_sql(), utcnow_sql()),
        )
        await db_execute("INSERT INTO user_video_events(user_id,video_id,event_type,created_at) VALUES(%s,%s,'detail_open',%s)", (uid, video_id, utcnow_sql()))
        await v20_credit_video_reward(uid, video_id)
    return web.json_response({"ok": True})


async def api_resolve_start(request):
    code = request.match_info["code"]
    # Primary Mini App deep link: startapp=v... -> published package.
    row = await db_fetchone("SELECT id FROM videos WHERE share_code=%s AND published=TRUE LIMIT 1", (code,))
    if row:
        return web.json_response({"ok": True, "video_id": row["id"], "package": True, "storage_ready": False})

    # Backward compatibility for old video_xxx links. Those are Bot delivery links,
    # but if the matching package exists we can still open its detail page.
    row = await db_fetchone("SELECT id FROM videos WHERE video_code=%s AND published=TRUE LIMIT 1", (code,))
    storage = await lookup_video(code) if str(code).startswith("video_") else None
    return web.json_response({"ok": bool(row or storage), "video_id": row["id"] if row else None, "package": False, "storage_ready": bool(storage)})


async def api_profile(request):
    u = request_user(request)
    if not u:
        raise web.HTTPUnauthorized(text="Telegram authorization required")
    uid = int(u["id"])
    settings = await get_settings()
    if settings.get("profile_stats_enabled") is False:
        return web.json_response({"opened_total":0,"opened_unique":0,"unlocked":0,"favorites":0,"reactions":0,"comments":0,"ad_tasks":0,"recent_unlocked":[]})
    opened = await db_fetchone("SELECT COALESCE(SUM(open_count),0) c, COUNT(*) unique_c FROM video_views WHERE user_id=%s", (uid,))
    unlocked = await db_fetchone("SELECT COUNT(DISTINCT video_code) c FROM video_requests WHERE user_id=%s AND delivered=TRUE", (uid,))
    favs = await db_fetchone("SELECT COUNT(*) c FROM favorites WHERE user_id=%s", (uid,))
    reacts = await db_fetchone("SELECT COUNT(*) c FROM reactions WHERE user_id=%s", (uid,))
    comments = await db_fetchone("SELECT COUNT(*) c FROM comments WHERE user_id=%s", (uid,))
    ad_tasks = await db_fetchone("SELECT COUNT(*) c FROM ad_completions WHERE user_id=%s", (uid,))
    recent = await db_fetchall(
        """SELECT DISTINCT ON (v.id) v.id,v.title,v.thumb,r.created_at FROM video_requests r
           JOIN videos v ON v.video_code=r.video_code WHERE r.user_id=%s AND r.delivered=TRUE
           ORDER BY v.id,r.created_at DESC LIMIT 5""", (uid,)
    )
    joined = await db_fetchone("SELECT created_at FROM mini_bot_users WHERE user_id=%s", (uid,))
    return web.Response(text=json.dumps({
        "join_date": (joined or {}).get("created_at"),
        "opened_total": int(opened["c"] or 0), "opened_unique": int(opened["unique_c"] or 0),
        "unlocked": int(unlocked["c"] or 0), "favorites": int(favs["c"] or 0),
        "reactions": int(reacts["c"] or 0), "comments": int(comments["c"] or 0), "ad_tasks": int(ad_tasks["c"] or 0), "recent_unlocked": recent
    }, default=str, ensure_ascii=False), content_type="application/json")


async def api_toggle_favorite(request):
    if (await get_settings()).get("favorites_enabled") is False: raise web.HTTPForbidden(text="Favorites disabled")
    u = request_user(request)
    if not u: raise web.HTTPUnauthorized(text="Telegram authorization required")
    uid, vid = int(u["id"]), request.match_info["video_id"]
    exists = await db_fetchone("SELECT 1 x FROM favorites WHERE user_id=%s AND video_id=%s", (uid,vid))
    if exists:
        await db_execute("DELETE FROM favorites WHERE user_id=%s AND video_id=%s", (uid,vid)); active=False
    else:
        await db_execute("INSERT INTO favorites(user_id,video_id,created_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (uid,vid,utcnow_sql())); active=True
    count=(await db_fetchone("SELECT COUNT(*) c FROM favorites WHERE video_id=%s",(vid,)))["c"]
    return web.json_response({"ok":True,"active":active,"count":count})


async def api_toggle_reaction(request):
    if (await get_settings()).get("reactions_enabled") is False: raise web.HTTPForbidden(text="Reactions disabled")
    u = request_user(request)
    if not u: raise web.HTTPUnauthorized(text="Telegram authorization required")
    uid, vid = int(u["id"]), request.match_info["video_id"]
    exists = await db_fetchone("SELECT 1 x FROM reactions WHERE user_id=%s AND video_id=%s", (uid,vid))
    if exists:
        await db_execute("DELETE FROM reactions WHERE user_id=%s AND video_id=%s", (uid,vid)); active=False
    else:
        await db_execute("INSERT INTO reactions(user_id,video_id,reaction,created_at) VALUES(%s,%s,'heart',%s) ON CONFLICT DO NOTHING", (uid,vid,utcnow_sql())); active=True
    count=(await db_fetchone("SELECT COUNT(*) c FROM reactions WHERE video_id=%s",(vid,)))["c"]
    return web.json_response({"ok":True,"active":active,"count":count})


async def api_comments(request):
    if (await get_settings()).get("comments_enabled") is False:
        if request.method == 'GET': return web.json_response({"comments":[]})
        raise web.HTTPForbidden(text="Comments disabled")
    vid = request.match_info["video_id"]
    if request.method == 'GET':
        rows=await db_fetchall("SELECT id,display_name,username,text,created_at FROM comments WHERE video_id=%s ORDER BY created_at DESC LIMIT 100",(vid,))
        return web.Response(text=json.dumps({"comments":rows},default=str,ensure_ascii=False),content_type='application/json')
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text="Telegram authorization required")
    d=await json_body(request); text=str(d.get('text','')).strip()[:700]
    if not text: raise web.HTTPBadRequest(text='Empty comment')
    name=(str(u.get('first_name') or '')+' '+str(u.get('last_name') or '')).strip() or 'User'
    await db_execute("INSERT INTO comments(video_id,user_id,display_name,username,text,created_at) VALUES(%s,%s,%s,%s,%s,%s)",(vid,int(u['id']),name,u.get('username'),text,utcnow_sql()))
    return web.json_response({"ok":True})


async def api_video_social(request):
    vid=request.match_info['video_id']; u=request_user(request); uid=int(u['id']) if u else None
    rc=(await db_fetchone("SELECT COUNT(*) c FROM reactions WHERE video_id=%s",(vid,)))["c"]
    cc=(await db_fetchone("SELECT COUNT(*) c FROM comments WHERE video_id=%s",(vid,)))["c"]
    fav=react=False
    if uid:
        fav=bool(await db_fetchone("SELECT 1 x FROM favorites WHERE user_id=%s AND video_id=%s",(uid,vid)))
        react=bool(await db_fetchone("SELECT 1 x FROM reactions WHERE user_id=%s AND video_id=%s",(uid,vid)))
    return web.json_response({"reaction_count":rc,"comment_count":cc,"favorite":fav,"reacted":react})


async def api_unlock_url(request):
    u = request_user(request)
    if not u:
        raise web.HTTPUnauthorized(text="Telegram authorization required")
    vid = request.match_info["video_id"]
    settings = await get_settings()
    row = await db_fetchone("SELECT video_code,short_url,required_ads,delivery_mode FROM videos WHERE id=%s AND published=TRUE", (vid,))
    if not row:
        raise web.HTTPNotFound(text="Package not found")
    await reset_expired_video_access(int(u["id"]), row.get("video_code"), settings)
    mode = str(settings.get("monetization_mode") or "both").lower()
    ads_req = int(row.get("required_ads") if row.get("required_ads") is not None else (settings.get("required_ads_default") or 1))
    mon_req = int(settings.get("monetag_required_default") or 1)
    ads_done = int((await db_fetchone("SELECT COUNT(*) c FROM ad_completions WHERE user_id=%s AND video_id=%s AND provider='adsgram'", (int(u["id"]), vid)))["c"] or 0)
    mon_done = int((await db_fetchone("SELECT COUNT(*) c FROM ad_completions WHERE user_id=%s AND video_id=%s AND provider='monetag'", (int(u["id"]), vid)))["c"] or 0)
    if mode in ("adsgram","both") and settings.get("adsgram_enabled", True) and ads_done < ads_req:
        raise web.HTTPForbidden(text=f"Complete AdsGram first ({ads_done}/{ads_req})")
    if mode in ("monetag","both") and settings.get("monetag_enabled", True) and mon_done < mon_req:
        raise web.HTTPForbidden(text=f"Complete Monetag first ({mon_done}/{mon_req})")
    await db_execute("INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) VALUES(%s,%s,%s,'unlock_granted',%s)", (int(u["id"]), vid, row.get("video_code"), utcnow_sql()))
    await v20_credit_video_reward(int(u['id']), vid)
    dmode = (row.get("delivery_mode") or "video_bot").strip()
    if dmode == "short_link" and row.get("short_url"):
        return web.json_response({"ok": True, "url": row.get("short_url"), "mode": "short_link"})
    target = f"https://t.me/{VIDEO_BOT_USERNAME}?start={row.get('video_code')}"
    return web.json_response({"ok": True, "url": target, "mode": "video_bot", "video_bot_username": VIDEO_BOT_USERNAME})


async def api_unlock_click(request):
    u=request_user(request)
    if not u: return web.json_response({"ok":True})
    vid=request.match_info['video_id']
    row=await db_fetchone("SELECT video_code FROM videos WHERE id=%s",(vid,))
    await db_execute("INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) VALUES(%s,%s,%s,'unlock_click',%s)",(int(u['id']),vid,row['video_code'] if row else None,utcnow_sql()))
    return web.json_response({"ok":True})


async def api_ad_status(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text="Telegram authorization required")
    vid=request.match_info["video_id"]; settings=await get_settings()
    row=await db_fetchone("SELECT required_ads,video_code FROM videos WHERE id=%s AND published=TRUE",(vid,))
    if not row: raise web.HTTPNotFound(text="Package not found")
    await reset_expired_video_access(int(u["id"]), row.get("video_code"), settings)
    mode=str(settings.get("monetization_mode") or "both").lower()
    ads_req=int(row.get("required_ads") if row.get("required_ads") is not None else (settings.get("required_ads_default") or 1))
    mon_req=int(settings.get("monetag_required_default") or 1)
    ads_done=int((await db_fetchone("SELECT COUNT(*) c FROM ad_completions WHERE user_id=%s AND video_id=%s AND provider='adsgram'",(int(u['id']),vid)))["c"] or 0)
    mon_done=int((await db_fetchone("SELECT COUNT(*) c FROM ad_completions WHERE user_id=%s AND video_id=%s AND provider='monetag'",(int(u['id']),vid)))["c"] or 0)
    ads_enabled=bool(settings.get("adsgram_enabled",True)) and mode in ("adsgram","both") and ads_req>0
    mon_enabled=bool(settings.get("monetag_enabled",True)) and mode in ("monetag","both") and mon_req>0
    unlocked=(not ads_enabled or ads_done>=ads_req) and (not mon_enabled or mon_done>=mon_req)
    return web.json_response({"enabled":ads_enabled or mon_enabled,"unlocked":unlocked,"mode":mode,
      "adsgram":{"enabled":ads_enabled,"required":ads_req,"completed":min(ads_done,ads_req),"block_id":settings.get("adsgram_block_id") or "int-45179","button_text":settings.get("ad_button_text") or "📢 AdsGram Ad দেখুন"},
      "monetag":{"enabled":mon_enabled,"required":mon_req,"completed":min(mon_done,mon_req),"zone_id":str(settings.get("monetag_zone_id") or "11404425"),"button_text":settings.get("monetag_button_text") or "💰 Monetag Ad দেখুন","wait_seconds":int(settings.get("monetag_wait_seconds") or 20)},
      "unlock_text":settings.get("ad_unlock_text") or "🔓 ভিডিও আনলক করুন"})

async def api_ad_session(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text="Telegram authorization required")
    vid=request.match_info["video_id"]; d=await json_body(request); provider=str(d.get("provider") or "adsgram").lower()
    if provider not in {"adsgram","monetag"}: raise web.HTTPBadRequest(text="Unknown provider")
    settings=await get_settings(); enabled=settings.get("adsgram_enabled",True) if provider=="adsgram" else settings.get("monetag_enabled",True)
    if not enabled: return web.json_response({"ok":True,"bypass":True})
    if not await db_fetchone("SELECT id FROM videos WHERE id=%s AND published=TRUE",(vid,)): raise web.HTTPNotFound(text="Package not found")
    token=secrets.token_urlsafe(32)
    await db_execute("INSERT INTO ad_sessions(session_token,user_id,video_id,status,provider,created_at) VALUES(%s,%s,%s,'pending',%s,%s)",(token,int(u['id']),vid,provider,utcnow_sql()))
    return web.json_response({"ok":True,"session_token":token,"provider":provider,"block_id":settings.get("adsgram_block_id") or "int-45179","zone_id":str(settings.get("monetag_zone_id") or "11404425"),"wait_seconds":int(settings.get("monetag_wait_seconds") or 20)})

async def api_ad_complete(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text="Telegram authorization required")
    vid=request.match_info["video_id"]; d=await json_body(request); token=str(d.get("session_token") or '').strip(); provider=str(d.get("provider") or '').lower()
    if not token: raise web.HTTPBadRequest(text="Missing ad session token")
    sess=await db_fetchone("SELECT * FROM ad_sessions WHERE session_token=%s AND user_id=%s AND video_id=%s",(token,int(u['id']),vid))
    if not sess: raise web.HTTPBadRequest(text="Invalid ad session")
    provider=provider or str(sess.get("provider") or "adsgram")
    if provider != str(sess.get("provider") or provider): raise web.HTTPBadRequest(text="Provider mismatch")
    if sess.get("status") != "completed":
        await db_execute("UPDATE ad_sessions SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE session_token=%s",(token,))
        settings=await get_settings(); ident=(settings.get("adsgram_block_id") or "int-45179") if provider=="adsgram" else str(settings.get("monetag_zone_id") or "11404425")
        await db_execute("INSERT INTO ad_completions(user_id,video_id,session_token,provider,block_id,created_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT (session_token) DO NOTHING",(int(u['id']),vid,token,provider,ident,utcnow_sql()))
        await db_execute("INSERT INTO user_video_events(user_id,video_id,event_type,created_at) VALUES(%s,%s,%s,%s)",(int(u['id']),vid,provider+'_complete',utcnow_sql()))
    return await api_ad_status(request)


# ---------- V20 modular services ----------
async def v20_wallet_settings():
    return await db_fetchone("SELECT * FROM wallet_settings WHERE id='main'") or {}

async def v20_video_buttons():
    return await db_fetchone("SELECT * FROM video_buttons WHERE id='main'") or {}

def v20_button_keyboard(cfg, deleted=False):
    rows=[]
    prefix='deleted_' if deleted else ''
    for i in (1,2):
        if cfg.get(f'{prefix}button{i}_enabled') and cfg.get(f'{prefix}button{i}_name') and cfg.get(f'{prefix}button{i}_url'):
            rows.append([InlineKeyboardButton(text=str(cfg[f'{prefix}button{i}_name'])[:64], url=str(cfg[f'{prefix}button{i}_url']))])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

async def v20_credit_video_reward(user_id:int, video_id:str):
    ws=await v20_wallet_settings(); amount=float(ws.get('per_video_reward') or 0)
    if amount <= 0: return 0.0
    daily=float(ws.get('daily_reward_limit') or 0)
    if daily > 0:
        r=await db_fetchone("SELECT COALESCE(SUM(amount),0) c FROM transactions WHERE user_id=%s AND type='credit' AND reference_type='video' AND created_at>=CURRENT_DATE",(user_id,))
        if float(r['c'] or 0) >= daily: return 0.0
        amount=min(amount,max(0,daily-float(r['c'] or 0)))
    try:
        await db_execute("INSERT INTO transactions(user_id,type,amount,reference_type,reference_id,note) VALUES(%s,'credit',%s,'video',%s,'Video watch reward')",(user_id,amount,video_id))
    except Exception:
        return 0.0
    await db_execute("INSERT INTO wallet(user_id,balance,total_earn,updated_at) VALUES(%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET balance=wallet.balance+EXCLUDED.balance,total_earn=wallet.total_earn+EXCLUDED.total_earn,updated_at=CURRENT_TIMESTAMP",(user_id,amount,amount))
    return amount

async def api_v20_wallet(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text='Telegram authorization required')
    uid=int(u['id'])
    w=await db_fetchone("SELECT * FROM wallet WHERE user_id=%s",(uid,)) or {'balance':0,'total_earn':0,'total_withdraw':0}
    watched=(await db_fetchone("SELECT COALESCE(SUM(open_count),0) c FROM video_views WHERE user_id=%s",(uid,)))['c']
    unlocked=(await db_fetchone("SELECT COUNT(DISTINCT video_code) c FROM video_requests WHERE user_id=%s AND delivered=TRUE",(uid,)))['c']
    tx=await db_fetchall("SELECT type,amount,reference_type,note,created_at FROM transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 20",(uid,))
    wr=await db_fetchall("SELECT id,amount,payment_method,account_number,status,created_at FROM withdraw_requests WHERE user_id=%s ORDER BY created_at DESC LIMIT 20",(uid,))
    return web.Response(text=json.dumps({'balance':float(w.get('balance') or 0),'total_earn':float(w.get('total_earn') or 0),'total_withdraw':float(w.get('total_withdraw') or 0),'total_video_watched':int(watched or 0),'total_video_unlock':int(unlocked or 0),'transactions':tx,'withdraw_history':wr},default=str,ensure_ascii=False),content_type='application/json')

async def api_v20_withdraw(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text='Telegram authorization required')
    uid=int(u['id']); d=await json_body(request); ws=await v20_wallet_settings()
    if not ws.get('withdraw_enabled',True): raise web.HTTPForbidden(text='Withdraw disabled')
    try: amount=float(d.get('amount') or 0)
    except: raise web.HTTPBadRequest(text='Invalid amount')
    mn=float(ws.get('minimum_withdraw') or 0); mx=float(ws.get('maximum_withdraw') or 0)
    if amount<=0 or amount<mn or (mx>0 and amount>mx): raise web.HTTPBadRequest(text=f'Withdraw range {mn}-{mx}')
    method=str(d.get('payment_method') or '').strip(); number=str(d.get('number') or '').strip()
    allowed=[x.strip() for x in str(ws.get('payment_methods') or 'bKash,Nagad').split(',') if x.strip()]
    if method not in allowed or not number: raise web.HTTPBadRequest(text='Payment method/number required')
    w=await db_fetchone("SELECT balance FROM wallet WHERE user_id=%s",(uid,)) or {'balance':0}
    if float(w['balance'] or 0)<amount: raise web.HTTPBadRequest(text='Insufficient balance')
    pending=(await db_fetchone("SELECT COALESCE(SUM(amount),0)c FROM withdraw_requests WHERE user_id=%s AND status='pending'",(uid,)))['c']
    if float(w['balance'] or 0)-float(pending or 0)<amount: raise web.HTTPBadRequest(text='Balance already reserved by pending request')
    await db_execute("INSERT INTO withdraw_requests(user_id,amount,payment_method,account_number) VALUES(%s,%s,%s,%s)",(uid,amount,method,number))
    return web.json_response({'ok':True})

async def api_v20_tasks(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text='Telegram authorization required')
    uid=int(u['id']); rows=await db_fetchall("SELECT t.*, EXISTS(SELECT 1 FROM task_completions c WHERE c.user_id=%s AND c.task_id=t.id) completed FROM tasks t WHERE enabled=TRUE ORDER BY id DESC",(uid,))
    return web.Response(text=json.dumps({'tasks':rows},default=str,ensure_ascii=False),content_type='application/json')

async def api_v20_task_complete(request):
    u=request_user(request)
    if not u: raise web.HTTPUnauthorized(text='Telegram authorization required')
    uid=int(u['id']); tid=int(request.match_info['task_id']); t=await db_fetchone("SELECT * FROM tasks WHERE id=%s AND enabled=TRUE",(tid,))
    if not t: raise web.HTTPNotFound(text='Task not found')
    exists=await db_fetchone("SELECT 1 x FROM task_completions WHERE user_id=%s AND task_id=%s",(uid,tid))
    if exists: return web.json_response({'ok':True,'already_completed':True})
    amt=float(t.get('reward_amount') or 0)
    await db_execute("INSERT INTO task_completions(user_id,task_id,rewarded) VALUES(%s,%s,TRUE)",(uid,tid))
    if amt>0:
        await db_execute("INSERT INTO transactions(user_id,type,amount,reference_type,reference_id,note) VALUES(%s,'credit',%s,'task',%s,%s)",(uid,amt,str(tid),t.get('title')))
        await db_execute("INSERT INTO wallet(user_id,balance,total_earn,updated_at) VALUES(%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET balance=wallet.balance+EXCLUDED.balance,total_earn=wallet.total_earn+EXCLUDED.total_earn,updated_at=CURRENT_TIMESTAMP",(uid,amt,amt))
    return web.json_response({'ok':True,'reward':amt})

async def api_v20_admin_config(request):
    u=require_admin(request)
    uid=int(u['id'])
    if request.method=='GET':
        return web.Response(text=json.dumps({'tutorial':await db_fetchone("SELECT * FROM tutorial_settings WHERE id='main'"),'wallet':await v20_wallet_settings(),'buttons':await v20_video_buttons(),'ads':await db_fetchone("SELECT * FROM ad_settings WHERE id='main'"),'texts':{k:(await get_settings()).get(k) for k in ('video_access_title','video_access_instruction','ad_progress_seen_text','ad_progress_remaining_text','ad_watch_button_text','all_ads_done_text','watch_video_button_text','video_access_secure_text','mini_bot_package_message','mini_bot_package_button_text','video_bot_package_message','video_bot_package_button_text','notification_bot_package_message','notification_bot_package_button_text','channel_package_message','channel_package_button_text')},'channels':await db_fetchall("SELECT * FROM notification_channels ORDER BY id"),'tasks':await db_fetchall("SELECT * FROM tasks ORDER BY id DESC"),'withdraws':await db_fetchall("SELECT * FROM withdraw_requests ORDER BY created_at DESC LIMIT 200") if (uid==OWNER_ID or has_perm(uid,'can_manage_withdraw')) else [],'is_owner':uid==OWNER_ID},default=str,ensure_ascii=False),content_type='application/json')
    d=await json_body(request); section=str(d.get('section') or '')
    if section=='tutorial':
        if not (uid==OWNER_ID or has_perm(uid,'can_manage_settings')): raise web.HTTPForbidden(text='Owner/Admin settings permission required')
        enabled = bool(d.get('enabled', True))
        raw_code = str(d.get('video_code') or '').strip()
        code = _tutorial_video_code(raw_code) or None
        title = str(d.get('title') or '')[:255]
        description = d.get('description')
        button_text = str(d.get('button_text') or '🏠 Home Page')[:100]
        # Upsert so Tutorial Settings works even if the seed row is missing.
        await db_execute("""INSERT INTO tutorial_settings(id,enabled,video_code,title,description,button_text,updated_by,updated_at)
                          VALUES('main',%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                          ON CONFLICT(id) DO UPDATE SET enabled=EXCLUDED.enabled,video_code=EXCLUDED.video_code,
                          title=EXCLUDED.title,description=EXCLUDED.description,button_text=EXCLUDED.button_text,
                          updated_by=EXCLUDED.updated_by,updated_at=CURRENT_TIMESTAMP""",
                         (enabled,code,title,description,button_text,uid))
        # Keep legacy settings synchronized because older code paths still read these fields.
        caption = (title + ('\n\n' + str(description).strip() if description else '')).strip() or 'ভিডিও দেখার নিয়ম'
        await db_execute("UPDATE app_settings SET tutorial_enabled=%s,tutorial_video_code=%s,tutorial_caption=%s,tutorial_button_text=%s,updated_at=CURRENT_TIMESTAMP WHERE id='main'",
                         (enabled,code,caption,button_text))
        # V20.7 Start Video is shown on each normal /start in Mini + Notification Bot.
        # Clear legacy one-time-view data for compatibility with older deployments.
        await db_execute("DELETE FROM tutorial_views")
    elif section=='wallet':
        if not (uid==OWNER_ID or has_perm(uid,'can_manage_withdraw') or has_perm(uid,'can_manage_settings')): raise web.HTTPForbidden()
        await db_execute("UPDATE wallet_settings SET per_video_reward=%s,daily_reward_limit=%s,minimum_withdraw=%s,maximum_withdraw=%s,withdraw_enabled=%s,payment_methods=%s,updated_at=CURRENT_TIMESTAMP WHERE id='main'",(float(d.get('per_video_reward') or 0),float(d.get('daily_reward_limit') or 0),float(d.get('minimum_withdraw') or 0),float(d.get('maximum_withdraw') or 0),bool(d.get('withdraw_enabled',True)),str(d.get('payment_methods') or 'bKash,Nagad')))
    elif section=='buttons':
        if not has_perm(uid,'can_manage_settings'): raise web.HTTPForbidden()
        keys=['custom_message','button1_name','button1_url','button1_enabled','button2_name','button2_url','button2_enabled','deleted_message','deleted_button1_name','deleted_button1_url','deleted_button1_enabled','deleted_button2_name','deleted_button2_url','deleted_button2_enabled']
        vals=[d.get(k) for k in keys]; await db_execute('UPDATE video_buttons SET '+','.join(f'{k}=%s' for k in keys)+',updated_at=CURRENT_TIMESTAMP WHERE id=\'main\'',tuple(vals))
    elif section=='texts':
        if not (uid==OWNER_ID or has_perm(uid,'can_manage_settings') or has_perm(uid,'can_manage_notifications')): raise web.HTTPForbidden()
        keys=('video_access_title','video_access_instruction','ad_progress_seen_text','ad_progress_remaining_text','ad_watch_button_text','all_ads_done_text','watch_video_button_text','video_access_secure_text','mini_bot_package_message','mini_bot_package_button_text','video_bot_package_message','video_bot_package_button_text','notification_bot_package_message','notification_bot_package_button_text','channel_package_message','channel_package_button_text')
        vals=[d.get(k) for k in keys]
        await db_execute('UPDATE app_settings SET '+','.join(f'{k}=%s' for k in keys)+',updated_at=CURRENT_TIMESTAMP WHERE id=\'main\'',tuple(vals))
    elif section=='ads':
        if not has_perm(uid,'can_manage_settings'): raise web.HTTPForbidden()
        await db_execute("UPDATE ad_settings SET mode=%s,adsgram_enabled=%s,adsgram_block_id=%s,adsgram_required=%s,adsgram_reward_mode=%s,monetag_enabled=%s,monetag_zone_id=%s,monetag_smart_link=%s,monetag_script_api=%s,monetag_wait_seconds=%s,updated_at=CURRENT_TIMESTAMP WHERE id='main'",(d.get('mode','both'),bool(d.get('adsgram_enabled')),d.get('adsgram_block_id'),int(d.get('adsgram_required') or 1),bool(d.get('adsgram_reward_mode',True)),bool(d.get('monetag_enabled')),d.get('monetag_zone_id'),d.get('monetag_smart_link'),d.get('monetag_script_api'),int(d.get('monetag_wait_seconds') or 20)))
        await db_execute("UPDATE app_settings SET monetization_mode=%s,adsgram_enabled=%s,adsgram_block_id=%s,required_ads_default=%s,monetag_enabled=%s,monetag_zone_id=%s,monetag_wait_seconds=%s,updated_at=CURRENT_TIMESTAMP WHERE id='main'",(d.get('mode','both'),bool(d.get('adsgram_enabled')),d.get('adsgram_block_id'),int(d.get('adsgram_required') or 1),bool(d.get('monetag_enabled')),d.get('monetag_zone_id'),int(d.get('monetag_wait_seconds') or 20)))
    else: raise web.HTTPBadRequest(text='Unknown section')
    return web.json_response({'ok':True})

async def api_v20_admin_tasks(request):
    u=require_admin(request); uid=int(u['id'])
    if not (uid==OWNER_ID or has_perm(uid,'can_manage_tasks') or has_perm(uid,'can_manage_content')): raise web.HTTPForbidden()
    if request.method=='POST':
        d=await json_body(request); tid=d.get('id')
        if tid: await db_execute("UPDATE tasks SET title=%s,task_type=%s,reward_amount=%s,task_link=%s,enabled=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(d.get('title'),d.get('task_type','link'),float(d.get('reward_amount') or 0),d.get('task_link'),bool(d.get('enabled',True)),int(tid)))
        else: await db_execute("INSERT INTO tasks(title,task_type,reward_amount,task_link,enabled,created_by) VALUES(%s,%s,%s,%s,%s,%s)",(d.get('title'),d.get('task_type','link'),float(d.get('reward_amount') or 0),d.get('task_link'),bool(d.get('enabled',True)),uid))
    else: await db_execute("DELETE FROM tasks WHERE id=%s",(int(request.match_info['task_id']),))
    return web.json_response({'ok':True})

async def _notification_channel_status(chat_id: int):
    """Verify that the Notification Bot can post to the configured target."""
    try:
        chat = await bot.get_chat(int(chat_id))
        me = await bot.get_me()
        member = await bot.get_chat_member(int(chat_id), me.id)
        status = str(getattr(member, "status", "") or "")
        can_post = bool(getattr(member, "can_post_messages", False))
        chat_type = str(getattr(chat, "type", "") or "")
        # In groups/supergroups an ordinary member can send unless restricted.
        if chat_type in ("group", "supergroup"):
            allowed = status not in ("left", "kicked", "restricted")
        else:
            allowed = status in ("administrator", "creator") and can_post
        return {
            "ok": bool(allowed),
            "chat_id": int(chat_id),
            "title": getattr(chat, "title", None),
            "chat_type": chat_type,
            "bot_status": status,
            "can_post_messages": can_post,
            "error": None if allowed else (
                "Notification Bot-কে channel-এর Admin করে Post Messages permission দিন"
                if chat_type == "channel"
                else "Notification Bot-এর group send permission নেই"
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "chat_id": int(chat_id),
            "title": None,
            "chat_type": None,
            "bot_status": None,
            "can_post_messages": False,
            "error": str(e),
        }


async def api_v20_admin_channels(request):
    u=require_admin(request); uid=int(u['id'])
    if not (uid==OWNER_ID or has_perm(uid,'can_manage_notifications') or has_perm(uid,'can_manage_settings')):
        raise web.HTTPForbidden()

    if request.method=='POST':
        d=await json_body(request)
        try:
            cid=int(str(d.get('chat_id') or '').strip())
        except Exception:
            raise web.HTTPBadRequest(text='Valid Channel ID দিন, যেমন -1001234567890')
        status=await _notification_channel_status(cid)
        title=str(d.get('title') or status.get('title') or cid)
        await db_execute(
            "INSERT INTO notification_channels(chat_id,title,enabled) VALUES(%s,%s,%s) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=EXCLUDED.title,enabled=EXCLUDED.enabled,updated_at=CURRENT_TIMESTAMP",
            (cid,title,bool(status.get('ok')))
        )
        return web.json_response({
            'ok': bool(status.get('ok')),
            'saved': True,
            'channel': status,
            'message': 'Channel verified ✅' if status.get('ok') else status.get('error')
        }, status=200 if status.get('ok') else 400)

    await db_execute("DELETE FROM notification_channels WHERE id=%s",(int(request.match_info['channel_id']),))
    return web.json_response({'ok':True})


async def api_v20_admin_channel_test(request):
    u=require_admin(request); uid=int(u['id'])
    if not (uid==OWNER_ID or has_perm(uid,'can_manage_notifications') or has_perm(uid,'can_manage_settings')):
        raise web.HTTPForbidden()
    row=await db_fetchone("SELECT * FROM notification_channels WHERE id=%s",(int(request.match_info['channel_id']),))
    if not row:
        raise web.HTTPNotFound(text='Channel not found')
    cid=int(row['chat_id'])
    status=await _notification_channel_status(cid)
    if not status.get('ok'):
        await db_execute("UPDATE notification_channels SET enabled=FALSE,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(int(row['id']),))
        raise web.HTTPBadRequest(text=status.get('error') or 'Notification Bot cannot post here')
    try:
        await bot.send_message(
            cid,
            "✅ Notification Channel Test Successful\n\nএই channel-এ package notification পাঠানো যাবে।",
            protect_content=True
        )
        await db_execute("UPDATE notification_channels SET enabled=TRUE,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(int(row['id']),))
        return web.json_response({'ok':True,'channel':status})
    except Exception as e:
        await db_execute("UPDATE notification_channels SET enabled=FALSE,updated_at=CURRENT_TIMESTAMP WHERE id=%s",(int(row['id']),))
        raise web.HTTPBadRequest(text=f'Test notification failed: {e}')


async def api_v20_admin_withdraw(request):
    u=require_admin(request); uid=int(u['id'])
    if not (uid==OWNER_ID or has_perm(uid,'can_manage_withdraw')): raise web.HTTPForbidden()
    rid=int(request.match_info['request_id']); d=await json_body(request); status=str(d.get('status') or '').lower()
    if status not in {'approved','rejected'}: raise web.HTTPBadRequest(text='Invalid status')
    r=await db_fetchone("SELECT * FROM withdraw_requests WHERE id=%s",(rid,));
    if not r or r.get('status')!='pending': raise web.HTTPBadRequest(text='Request already processed')
    if status=='approved':
        w=await db_fetchone("SELECT balance FROM wallet WHERE user_id=%s",(int(r['user_id']),)) or {'balance':0}
        if float(w['balance'] or 0)<float(r['amount']): raise web.HTTPBadRequest(text='Insufficient user balance')
        await db_execute("UPDATE wallet SET balance=balance-%s,total_withdraw=total_withdraw+%s,updated_at=CURRENT_TIMESTAMP WHERE user_id=%s",(r['amount'],r['amount'],r['user_id']))
        await db_execute("INSERT INTO transactions(user_id,type,amount,reference_type,reference_id,note) VALUES(%s,'debit',%s,'withdraw',%s,'Withdraw approved')",(r['user_id'],-abs(float(r['amount'])),str(rid)))
    await db_execute("UPDATE withdraw_requests SET status=%s,admin_note=%s,reviewed_by=%s,reviewed_at=CURRENT_TIMESTAMP WHERE id=%s",(status,d.get('note'),uid,rid))
    return web.json_response({'ok':True})


async def api_admin_drafts(request):
    require_admin(request, "can_manage_content")
    rows = await db_fetchall(
        """SELECT id,video_code,channel_id,message_id,title,caption_text,thumb,deep_link,consumed,created_at
           FROM upload_drafts WHERE consumed=FALSE ORDER BY created_at DESC LIMIT 30"""
    )
    return web.Response(text=json.dumps({"drafts": rows}, default=str, ensure_ascii=False), content_type="application/json")


async def api_admin_draft_delete(request):
    """Delete one pending Auto Thumbnail Draft from upload_drafts.

    This removes the draft entry from the Admin Panel only. It does not delete
    the original video from the private Telegram Storage Channel or video_map.
    """
    require_admin(request, "can_manage_content")
    code = str(request.match_info.get("video_code") or "").strip()
    if not code:
        raise web.HTTPBadRequest(text="Missing video_code")
    row = await db_fetchone("SELECT id,video_code FROM upload_drafts WHERE video_code=%s AND consumed=FALSE", (code,))
    if not row:
        raise web.HTTPNotFound(text="Draft not found")
    await db_execute("DELETE FROM upload_drafts WHERE video_code=%s AND consumed=FALSE", (code,))
    return web.json_response({"ok": True, "deleted": code})


async def api_admin_video_save(request):
    require_admin_any(request, "can_manage_packages", "can_manage_content")
    d = await json_body(request)
    required = ["id", "video_code", "title"]
    if any(not str(d.get(k, "")).strip() for k in required):
        raise web.HTTPBadRequest(text="Missing required video fields")

    existing = await db_fetchone("SELECT share_code,thumb,thumb_text,views,delivery_mode,short_url FROM videos WHERE id=%s", (d["id"],))
    share_code = str(d.get("share_code") or (existing or {}).get("share_code") or "").strip() or f"v{int(time.time() * 1000)}"
    share_link = f"https://t.me/{MINI_BOT_USERNAME}?startapp={share_code}"
    settings = await get_settings()
    try:
        required_ads = max(0, min(10, int(d.get("required_ads", settings.get("required_ads_default", 1)) or 0)))
    except Exception:
        required_ads = int(settings.get("required_ads_default", 1) or 1)
    thumb = d.get("thumb")
    if (thumb is None or thumb == "") and existing:
        thumb = existing.get("thumb") or ""
    thumb_text = str(d.get("thumb_text") or "").strip()[:180]
    if not thumb_text and existing:
        thumb_text = existing.get("thumb_text") or ""
    broadcast_enabled = bool(d.get("broadcast_enabled", True))
    published = bool(d.get("published", True))
    featured = bool(d.get("featured", False))
    trending = bool(d.get("trending", True))
    views = int((existing or {}).get("views") or 0)
    delivery_mode = str(d.get("delivery_mode") or (existing or {}).get("delivery_mode") or "video_bot").strip()
    if delivery_mode not in {"video_bot", "short_link"}: delivery_mode = "video_bot"
    short_url = str(d.get("short_url") or (existing or {}).get("short_url") or "").strip()
    await db_execute(
        """INSERT INTO videos(id,share_code,video_code,title,category_id,thumb,thumb_text,deep_link,short_url,delivery_mode,broadcast_enabled,broadcast_sent,published,views,required_ads,featured,trending,created_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET share_code=COALESCE(videos.share_code,EXCLUDED.share_code),video_code=EXCLUDED.video_code,title=EXCLUDED.title,category_id=EXCLUDED.category_id,
           thumb=EXCLUDED.thumb,thumb_text=EXCLUDED.thumb_text,deep_link=EXCLUDED.deep_link,short_url=EXCLUDED.short_url,delivery_mode=EXCLUDED.delivery_mode,
           broadcast_enabled=EXCLUDED.broadcast_enabled,published=EXCLUDED.published,required_ads=EXCLUDED.required_ads,featured=EXCLUDED.featured,trending=EXCLUDED.trending""",
        (d["id"], share_code, d["video_code"], d["title"], d.get("category_id"), thumb, thumb_text, d.get("deep_link", ""), short_url, delivery_mode, broadcast_enabled, published, views, required_ads, featured, trending, utcnow_sql()),
    )
    await db_execute("UPDATE upload_drafts SET consumed=TRUE WHERE video_code=%s", (d["video_code"],))
    return web.json_response({"ok": True, "share_code": share_code, "share_link": share_link, "required_ads": required_ads, "delivery_mode": delivery_mode})


async def api_admin_video_rebroadcast(request):
    require_admin_any(request, "can_manage_packages", "can_manage_content")
    vid = request.match_info["video_id"]
    await db_execute("UPDATE videos SET broadcast_enabled=TRUE,broadcast_sent=FALSE WHERE id=%s", (vid,))
    return web.json_response({"ok": True})


async def api_admin_video_delete(request):
    require_admin_any(request, "can_manage_packages", "can_manage_content")
    await db_execute("DELETE FROM videos WHERE id=%s", (request.match_info["video_id"],))
    return web.json_response({"ok": True})


async def api_admin_category_save(request):
    require_admin(request, "can_manage_content")
    d = await json_body(request)
    await db_execute(
        "INSERT INTO categories(id,name,icon,sort_order) VALUES(%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,icon=EXCLUDED.icon,sort_order=EXCLUDED.sort_order",
        (d["id"], d["name"], d.get("icon", "📁"), int(d.get("order", 0))),
    )
    return web.json_response({"ok": True})


async def api_admin_viral_save(request):
    require_admin(request, "can_manage_content")
    d = await json_body(request)
    await db_execute(
        "INSERT INTO viral_links(id,title,url,created_at) VALUES(%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,url=EXCLUDED.url",
        (d["id"], d["title"], d["url"], utcnow_sql()),
    )
    return web.json_response({"ok": True})


async def api_admin_viral_delete(request):
    require_admin(request, "can_manage_content")
    await db_execute("DELETE FROM viral_links WHERE id=%s", (request.match_info["link_id"],))
    return web.json_response({"ok": True})


async def api_admin_settings_save(request):
    require_admin(request, "can_manage_settings")
    d = await json_body(request)
    _admin_user = request_user(request) or {}
    if int(_admin_user.get("id", 0) or 0) != OWNER_ID:
        current = await get_settings()
        for _k in ("tutorial_enabled","tutorial_video_code","tutorial_caption","tutorial_button_text"):
            d[_k] = current.get(_k)
    fields = [
        "brand_name", "brand_subtitle", "hero_text", "nav_home", "nav_fav", "nav_unlock", "nav_viral", "nav_profile",
        "online_label", "show_online", "web_app_url", "bot_menu_button_text", "welcome_text", "watch_button_text",
        "broadcast_button_text", "auto_delete_minutes", "protect_content", "maintenance_mode",
        "tutorial_enabled", "tutorial_video_code", "tutorial_caption", "tutorial_button_text",
        "storage_channel_id", "maintenance_message", "support_url", "join_channel_url", "start_button_text",
        "comments_enabled", "reactions_enabled", "favorites_enabled", "profile_stats_enabled",
        "adsgram_enabled", "adsgram_block_id", "required_ads_default", "ad_button_text", "ad_unlock_text",
        "monetization_mode", "monetag_enabled", "monetag_zone_id", "monetag_required_default", "monetag_button_text", "monetag_wait_seconds",
        "welcome_manager_enabled", "join_request_welcome_enabled", "direct_join_welcome_enabled", "leave_inbox_enabled", "auto_approve_join_requests",
        "join_welcome_text", "leave_inbox_text",
        "welcome_video_button_text", "welcome_video_button_url", "welcome_video_button_enabled",
        "welcome_start_button_text", "welcome_start_button_url", "welcome_start_button_enabled",
        "welcome_rejoin_button_text", "welcome_rejoin_button_url", "welcome_rejoin_button_enabled"
    ]
    bool_fields = {"show_online", "protect_content", "maintenance_mode", "tutorial_enabled", "comments_enabled", "reactions_enabled", "favorites_enabled", "profile_stats_enabled", "adsgram_enabled", "monetag_enabled", "welcome_manager_enabled", "join_request_welcome_enabled", "direct_join_welcome_enabled", "leave_inbox_enabled", "auto_approve_join_requests", "welcome_video_button_enabled", "welcome_start_button_enabled", "welcome_rejoin_button_enabled"}

    vals = []
    for f in fields:
        v = d.get(f)
        if f in bool_fields:
            v = bool(v)
        elif f == "auto_delete_minutes":
            try:
                v = max(1, min(1440, int(v or 20)))
            except Exception:
                v = 20
        elif f in {"required_ads_default", "monetag_required_default"}:
            try:
                v = max(0, min(10, int(v or 1)))
            except Exception:
                v = 1
        elif f == "monetag_wait_seconds":
            try: v = max(0, min(120, int(v or 20)))
            except Exception: v = 20
        elif f == "monetization_mode":
            v = str(v or "both").lower(); v = v if v in {"adsgram","monetag","both","off"} else "both"
        elif f == "storage_channel_id":
            try:
                v = int(v) if v not in (None, "") else None
            except Exception:
                raise web.HTTPBadRequest(text="Invalid storage channel ID")
        vals.append(v)

    try:
        # Make sure the singleton row exists first, then update it. This is safer
        # for migrated databases than one large INSERT/UPSERT.
        await db_execute("INSERT INTO app_settings(id) VALUES('main') ON CONFLICT (id) DO NOTHING")
        set_clause = ",".join([f"{f}=%s" for f in fields])
        await db_execute(
            f"UPDATE app_settings SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id='main'",
            tuple(vals),
        )
        await sync_menu_button()
        saved = await get_settings()
        await db_execute("UPDATE ad_settings SET mode=%s,adsgram_enabled=%s,adsgram_block_id=%s,adsgram_required=%s,monetag_enabled=%s,monetag_zone_id=%s,monetag_wait_seconds=%s,updated_at=CURRENT_TIMESTAMP WHERE id='main'", (saved.get('monetization_mode') or 'both',bool(saved.get('adsgram_enabled',True)),saved.get('adsgram_block_id'),int(saved.get('required_ads_default') or 1),bool(saved.get('monetag_enabled',True)),saved.get('monetag_zone_id'),int(saved.get('monetag_wait_seconds') or 20)))
        return web.Response(text=json.dumps({"ok": True, "settings": saved}, default=str, ensure_ascii=False), content_type="application/json")
    except web.HTTPException:
        raise
    except Exception as e:
        log.exception("settings save failed")
        raise web.HTTPInternalServerError(text=f"Settings save failed: {type(e).__name__}: {e}")


async def api_admin_admins(request):
    u = require_admin(request, "can_manage_admins")
    if request.method == "GET":
        rows = await db_fetchall("SELECT user_id,role,display_name,can_manage_content,can_manage_settings,can_broadcast,can_manage_users,can_manage_admins,can_manage_packages,can_manage_withdraw,can_manage_support,can_manage_tasks,can_manage_notifications,can_view_analytics,created_at FROM admin_users ORDER BY created_at")
        return web.Response(text=json.dumps({"owner_id": OWNER_ID, "admins": rows}, default=str, ensure_ascii=False), content_type="application/json")
    d = await json_body(request)
    uid = int(d.get("user_id", 0))
    if uid <= 0 or uid == OWNER_ID: raise web.HTTPBadRequest(text="Invalid admin user id")
    vals = (uid, str(d.get("role") or "admin"), str(d.get("display_name") or "Admin")[:255], bool(d.get("can_manage_content",True)), bool(d.get("can_manage_settings",True)), bool(d.get("can_broadcast",True)), bool(d.get("can_manage_users",True)), bool(d.get("can_manage_admins",False)), bool(d.get("can_manage_packages",True)), bool(d.get("can_manage_withdraw",False)), bool(d.get("can_manage_support",False)), bool(d.get("can_manage_tasks",False)), bool(d.get("can_manage_notifications",False)), bool(d.get("can_view_analytics",True)))
    await db_execute("""INSERT INTO admin_users(user_id,role,display_name,can_manage_content,can_manage_settings,can_broadcast,can_manage_users,can_manage_admins,can_manage_packages,can_manage_withdraw,can_manage_support,can_manage_tasks,can_manage_notifications,can_view_analytics)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET role=EXCLUDED.role,display_name=EXCLUDED.display_name,can_manage_content=EXCLUDED.can_manage_content,can_manage_settings=EXCLUDED.can_manage_settings,can_broadcast=EXCLUDED.can_broadcast,can_manage_users=EXCLUDED.can_manage_users,can_manage_admins=EXCLUDED.can_manage_admins,can_manage_packages=EXCLUDED.can_manage_packages,can_manage_withdraw=EXCLUDED.can_manage_withdraw,can_manage_support=EXCLUDED.can_manage_support,can_manage_tasks=EXCLUDED.can_manage_tasks,can_manage_notifications=EXCLUDED.can_manage_notifications,can_view_analytics=EXCLUDED.can_view_analytics""", vals)
    await refresh_admin_cache()
    return web.json_response({"ok":True})


async def api_admin_admin_delete(request):
    require_admin(request, "can_manage_admins")
    uid = int(request.match_info["user_id"])
    if uid == OWNER_ID: raise web.HTTPBadRequest(text="Owner cannot be removed")
    await db_execute("DELETE FROM admin_users WHERE user_id=%s", (uid,))
    await refresh_admin_cache()
    return web.json_response({"ok":True})


async def api_admin_users(request):
    require_admin_any(request, "can_manage_support", "can_manage_users")
    rows = await db_fetchall("""
        SELECT u.user_id,u.username,u.first_name,u.last_name,u.is_active,u.last_seen_at,u.created_at,
          COALESCE((SELECT COUNT(*) FROM video_views vv WHERE vv.user_id=u.user_id),0) AS opened_unique,
          COALESCE((SELECT SUM(vv.open_count) FROM video_views vv WHERE vv.user_id=u.user_id),0) AS opened_total,
          COALESCE((SELECT COUNT(DISTINCT vr.video_code) FROM video_requests vr WHERE vr.user_id=u.user_id AND vr.delivered=TRUE),0) AS unlocked,
          COALESCE((SELECT COUNT(*) FROM ad_completions ac WHERE ac.user_id=u.user_id),0) AS ads_completed,
          (SELECT MAX(vr.created_at) FROM video_requests vr WHERE vr.user_id=u.user_id AND vr.delivered=TRUE) AS last_unlock_at,
          EXISTS(SELECT 1 FROM video_bot_users vb WHERE vb.user_id=u.user_id AND vb.is_active=TRUE) AS video_bot_started
        , EXISTS(SELECT 1 FROM bot_users nb WHERE nb.user_id=u.user_id AND nb.is_active=TRUE) AS notification_bot_started
        FROM mini_bot_users u ORDER BY u.last_seen_at DESC LIMIT 500
    """)
    return web.Response(text=json.dumps({"users":rows}, default=str, ensure_ascii=False), content_type="application/json")


async def api_admin_user_toggle(request):
    require_admin(request, "can_manage_users")
    uid=int(request.match_info["user_id"]); d=await json_body(request); active=bool(d.get("is_active",True))
    await db_execute("UPDATE mini_bot_users SET is_active=%s WHERE user_id=%s", (active,uid))
    return web.json_response({"ok":True})



async def api_admin_managed_chats(request):
    require_admin(request, "can_manage_settings")
    if request.method == "GET":
        rows = await db_fetchall("SELECT * FROM managed_chats ORDER BY updated_at DESC, title")
        return web.Response(text=json.dumps({"chats": rows}, default=str, ensure_ascii=False), content_type="application/json")
    d = await json_body(request)
    try:
        chat_id = int(d.get("chat_id"))
    except Exception:
        raise web.HTTPBadRequest(text="Valid Chat ID required")
    title = str(d.get("title") or chat_id)[:255]
    join_url = str(d.get("join_url") or "").strip() or None
    chat_type = str(d.get("chat_type") or "group")[:30]
    await db_execute(
        """INSERT INTO managed_chats(chat_id,title,chat_type,join_url,enabled,join_request_welcome,direct_join_welcome,leave_welcome,auto_approve,created_at,updated_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(chat_id) DO UPDATE SET title=EXCLUDED.title,chat_type=EXCLUDED.chat_type,join_url=EXCLUDED.join_url,enabled=EXCLUDED.enabled,
           join_request_welcome=EXCLUDED.join_request_welcome,direct_join_welcome=EXCLUDED.direct_join_welcome,leave_welcome=EXCLUDED.leave_welcome,
           auto_approve=EXCLUDED.auto_approve,updated_at=EXCLUDED.updated_at""",
        (chat_id, title, chat_type, join_url, bool(d.get("enabled", True)), bool(d.get("join_request_welcome", True)), bool(d.get("direct_join_welcome", True)), bool(d.get("leave_welcome", True)), d.get("auto_approve"), utcnow_sql(), utcnow_sql()),
    )
    return web.json_response({"ok": True})


async def api_admin_managed_chat_delete(request):
    require_admin(request, "can_manage_settings")
    await db_execute("DELETE FROM managed_chats WHERE chat_id=%s", (int(request.match_info["chat_id"]),))
    return web.json_response({"ok": True})


async def api_admin_join_leave_stats(request):
    require_admin(request, "can_manage_settings")
    total = (await db_fetchone("SELECT COUNT(*) c FROM join_leave_events"))["c"]
    sent = (await db_fetchone("SELECT COUNT(*) c FROM join_leave_events WHERE inbox_sent=TRUE"))["c"]
    joins = (await db_fetchone("SELECT COUNT(*) c FROM join_leave_events WHERE event_type IN ('join','join_request')"))["c"]
    leaves = (await db_fetchone("SELECT COUNT(*) c FROM join_leave_events WHERE event_type='leave'"))["c"]
    return web.json_response({"total": total, "sent": sent, "joins": joins, "leaves": leaves})


async def api_admin_broadcast(request):
    u=require_admin(request, "can_broadcast"); d=await json_body(request)
    typ=str(d.get("message_type") or "text"); text=str(d.get("text") or "").strip(); media=str(d.get("media_url") or "").strip(); bt=str(d.get("button_text") or "").strip(); bu=str(d.get("button_url") or "").strip()
    if not text and not media: raise web.HTTPBadRequest(text="Message or media required")
    main_users=await db_fetchall("SELECT user_id FROM bot_users WHERE is_active=TRUE")
    video_users=await db_fetchall("SELECT user_id FROM video_bot_users WHERE is_active=TRUE")
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=bt,url=bu)]]) if bt and bu else None
    ok=fail=0
    async def send_direct(client, uid):
        if typ=="photo" and media: await client.send_photo(uid,photo=media,caption=text or None,reply_markup=kb,protect_content=True)
        elif typ=="video" and media: await client.send_video(uid,video=media,caption=text or None,reply_markup=kb,protect_content=True)
        else: await client.send_message(uid,text,reply_markup=kb,protect_content=True)
    for row in main_users:
        uid=int(row["user_id"])
        try: await send_direct(bot,uid); ok+=1; await asyncio.sleep(0.05)
        except Exception: fail+=1
    for row in video_users:
        uid=int(row["user_id"])
        try: await send_direct(video_bot,uid); ok+=1; await asyncio.sleep(0.05)
        except Exception: fail+=1
    await db_execute("INSERT INTO admin_broadcasts(created_by,message_type,text_content,media_url,button_text,button_url,sent_count,failed_count) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(int(u["id"]),typ,text,media,bt,bu,ok,fail))
    return web.json_response({"ok":True,"sent":ok,"failed":fail})


async def api_admin_bot_status(request):
    require_admin(request, "can_manage_settings")
    async def info(client, role):
        try:
            me = await client.get_me()
            return {"role": role, "ok": True, "id": me.id, "username": me.username, "name": me.full_name}
        except Exception as e:
            return {"role": role, "ok": False, "error": str(e)[:200]}
    return web.json_response({
        "mini": await info(mini_bot, "Mini App / Control"),
        "notification": await info(bot, "Notification / Community"),
        "video": await info(video_bot, "Video Delivery"),
    })


async def api_admin_bot_test(request):
    u = require_admin(request, "can_manage_settings")
    d = await json_body(request)
    target = str(d.get("target") or "notification")
    client = {"mini": mini_bot, "notification": bot, "video": video_bot}.get(target)
    if not client:
        raise web.HTTPBadRequest(text="Unknown bot target")
    await client.send_message(int(u["id"]), f"✅ V18 {target.title()} Bot test successful")
    return web.json_response({"ok": True})


async def delete_queue_worker():
    """Delete scheduled Telegram messages from a durable PostgreSQL queue."""
    while True:
        try:
            rows = await db_fetchall("SELECT id,bot_kind,chat_id,message_id,retry_count FROM delete_queue WHERE status='pending' AND delete_at<=CURRENT_TIMESTAMP ORDER BY delete_at ASC LIMIT 100")
            for row in rows:
                try:
                    client = video_bot if (row.get("bot_kind") or "main") == "video" else bot
                    await client.delete_message(int(row["chat_id"]), int(row["message_id"]))
                    await db_execute("UPDATE delete_queue SET status='done',deleted_at=CURRENT_TIMESTAMP,last_error=NULL WHERE id=%s", (row["id"],))
                except Exception as exc:
                    retry = int(row.get("retry_count") or 0) + 1
                    # Telegram may say message is already deleted; after several attempts stop retrying forever.
                    status = 'failed' if retry >= 5 else 'pending'
                    await db_execute("UPDATE delete_queue SET status=%s,retry_count=%s,last_error=%s WHERE id=%s", (status, retry, str(exc)[:1000], row["id"]))
        except Exception:
            log.exception("delete queue worker error")
        await asyncio.sleep(30)


async def menu_sync_worker():
    last_signature = None
    while True:
        try:
            settings = await get_settings()
            signature = (settings.get("web_app_url") or DEFAULT_MINI_APP_URL, settings.get("bot_menu_button_text") or DEFAULT_MENU_BUTTON_TEXT)
            if signature != last_signature and await sync_menu_button():
                last_signature = signature
        except Exception:
            log.exception("menu sync worker error")
        await asyncio.sleep(max(30, MENU_SYNC_SECONDS))


async def start_web_server():
    app = web.Application(client_max_size=12 * 1024 * 1024)
    app.router.add_get("/", index_handler)
    app.router.add_get("/index.html", index_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/api/bootstrap", api_bootstrap)
    app.router.add_post("/api/presence", api_presence)
    app.router.add_post("/api/views/{video_id}", api_increment_view)
    app.router.add_get("/api/resolve-start/{code}", api_resolve_start)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/v20/wallet", api_v20_wallet)
    app.router.add_post("/api/v20/withdraw", api_v20_withdraw)
    app.router.add_get("/api/v20/tasks", api_v20_tasks)
    app.router.add_post("/api/v20/tasks/{task_id}/complete", api_v20_task_complete)
    app.router.add_get("/api/v20/admin/config", api_v20_admin_config)
    app.router.add_post("/api/v20/admin/config", api_v20_admin_config)
    app.router.add_post("/api/v20/admin/tasks", api_v20_admin_tasks)
    app.router.add_delete("/api/v20/admin/tasks/{task_id}", api_v20_admin_tasks)
    app.router.add_post("/api/v20/admin/channels", api_v20_admin_channels)
    app.router.add_delete("/api/v20/admin/channels/{channel_id}", api_v20_admin_channels)
    app.router.add_post("/api/v20/admin/channels/{channel_id}/test", api_v20_admin_channel_test)
    app.router.add_post("/api/v20/admin/withdraw/{request_id}", api_v20_admin_withdraw)
    app.router.add_get("/api/videos/{video_id}/social", api_video_social)
    app.router.add_post("/api/videos/{video_id}/favorite", api_toggle_favorite)
    app.router.add_post("/api/videos/{video_id}/reaction", api_toggle_reaction)
    app.router.add_get("/api/videos/{video_id}/comments", api_comments)
    app.router.add_post("/api/videos/{video_id}/comments", api_comments)
    app.router.add_post("/api/videos/{video_id}/unlock-click", api_unlock_click)
    app.router.add_get("/api/videos/{video_id}/unlock-url", api_unlock_url)
    app.router.add_get("/api/videos/{video_id}/ad-status", api_ad_status)
    app.router.add_post("/api/videos/{video_id}/ad-session", api_ad_session)
    app.router.add_post("/api/videos/{video_id}/ad-complete", api_ad_complete)
    app.router.add_get("/api/admin/drafts", api_admin_drafts)
    app.router.add_delete("/api/admin/drafts/{video_code}", api_admin_draft_delete)
    app.router.add_post("/api/admin/videos", api_admin_video_save)
    app.router.add_delete("/api/admin/videos/{video_id}", api_admin_video_delete)
    app.router.add_post("/api/admin/videos/{video_id}/rebroadcast", api_admin_video_rebroadcast)
    app.router.add_post("/api/admin/categories", api_admin_category_save)
    app.router.add_post("/api/admin/viral-links", api_admin_viral_save)
    app.router.add_delete("/api/admin/viral-links/{link_id}", api_admin_viral_delete)
    app.router.add_post("/api/admin/settings", api_admin_settings_save)
    app.router.add_get("/api/admin/managed-chats", api_admin_managed_chats)
    app.router.add_post("/api/admin/managed-chats", api_admin_managed_chats)
    app.router.add_delete("/api/admin/managed-chats/{chat_id}", api_admin_managed_chat_delete)
    app.router.add_get("/api/admin/join-leave-stats", api_admin_join_leave_stats)
    app.router.add_get("/api/admin/admins", api_admin_admins)
    app.router.add_post("/api/admin/admins", api_admin_admins)
    app.router.add_delete("/api/admin/admins/{user_id}", api_admin_admin_delete)
    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_post("/api/admin/users/{user_id}/toggle", api_admin_user_toggle)
    app.router.add_post("/api/admin/broadcast", api_admin_broadcast)
    app.router.add_get("/api/admin/bot-status", api_admin_bot_status)
    app.router.add_post("/api/admin/bot-test", api_admin_bot_test)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Mini App/API listening on 0.0.0.0:%s", PORT)
    return runner


async def main():
    global MINI_BOT_USERNAME
    await init_db()
    if not MINI_BOT_USERNAME:
        try:
            MINI_BOT_USERNAME = (await mini_bot.get_me()).username or ""
        except Exception:
            log.exception("Mini Bot username auto-detect failed")
    if not MINI_BOT_USERNAME:
        raise RuntimeError("MINI_BOT_USERNAME is missing and could not be auto-detected")
    await sync_menu_button(force_log=True)
    web_runner = await start_web_server()
    asyncio.create_task(broadcast_worker())
    asyncio.create_task(menu_sync_worker())
    asyncio.create_task(delete_queue_worker())
    mini_poll = asyncio.create_task(mini_dp.start_polling(mini_bot, allowed_updates=["message"]))
    main_poll = asyncio.create_task(dp.start_polling(bot, allowed_updates=["message", "channel_post", "callback_query", "chat_join_request", "chat_member", "my_chat_member"]))
    video_poll = asyncio.create_task(video_dp.start_polling(video_bot, allowed_updates=["message"]))
    try:
        await asyncio.gather(mini_poll, main_poll, video_poll)
    finally:
        await web_runner.cleanup()
        if db_pool:
            await db_pool.close()
        await mini_bot.session.close()
        await bot.session.close()
        await video_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
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

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.getenv("BOT_USERNAME", "Bangladesh_vairal_videobot").lstrip("@")
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

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
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
    ADMIN_PERMS = {OWNER_ID: {"role":"owner","can_manage_content":True,"can_manage_settings":True,"can_broadcast":True,"can_manage_users":True,"can_manage_admins":True}}
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
            for key in ("protect_content", "maintenance_mode", "show_online", "tutorial_enabled", "comments_enabled", "reactions_enabled", "favorites_enabled", "profile_stats_enabled"):
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
    }


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
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=text, web_app=WebAppInfo(url=url)))
        await bot.set_my_commands([BotCommand(command="start", description="Bot চালু করুন")])
        if force_log:
            log.info("Telegram menu button set: %s -> %s", text, url)
        return True
    except Exception:
        log.exception("menu button sync failed")
        return False


async def webapp_keyboard(settings=None):
    settings = settings or await get_settings()
    url = (settings.get("web_app_url") or DEFAULT_MINI_APP_URL or "").strip()
    text = (settings.get("bot_menu_button_text") or DEFAULT_MENU_BUTTON_TEXT or "🎬 Video open").strip()
    if not valid_webapp_url(url):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]])


async def delete_later(chat_id: int, message_id: int, minutes: int):
    await asyncio.sleep(max(1, minutes) * 60)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        log.warning("auto delete failed chat=%s msg=%s", chat_id, message_id)


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
        asyncio.create_task(delete_later(message.chat.id, sent.message_id, minutes))
        asyncio.create_task(delete_later(message.chat.id, notice.message_id, minutes))
        await db_execute(
            "INSERT INTO video_requests(user_id,video_code,delivered,created_at) VALUES(%s,%s,TRUE,%s)",
            (message.from_user.id, code, utcnow_sql()),
        )
        pub = await db_fetchone("SELECT id FROM videos WHERE video_code=%s LIMIT 1", (code,))
        await db_execute(
            "INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) VALUES(%s,%s,%s,'delivered',%s)",
            (message.from_user.id, pub["id"] if pub else None, code, utcnow_sql()),
        )
    except Exception:
        log.exception("copy video failed")
        await message.answer("❌ ভিডিও পাঠানো যায়নি। Storage Channel permission চেক করুন।")


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
        # Use tutorial-specific button label while keeping the configured web app URL.
        url = (settings.get("web_app_url") or DEFAULT_MINI_APP_URL or "").strip()
        label = (settings.get("tutorial_button_text") or "🎬 ভিডিও দেখতে শুরু করুন").strip()[:64]
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))]])
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
    tutorial_sent = await send_start_tutorial(message, settings)
    if tutorial_sent:
        return
    welcome = settings.get("welcome_text") or (
        f"👋 স্বাগতম {settings.get('brand_name','Bangladesh Viral Video')} Bot-এ।\n\n"
        "নিচের বাটন থেকে Mini App খুলুন এবং আপনার পছন্দের ভিডিও দেখুন।"
    )
    await message.answer(welcome, reply_markup=await webapp_keyboard(settings))


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

    deep_link = f"https://t.me/{BOT_USERNAME}?start={code}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Deep Link খুলুন", url=deep_link)],
        [InlineKeyboardButton(text="🎓 Tutorial Video হিসেবে সেট করুন", callback_data=f"set_tutorial:{code}")],
    ])
    caption = message.caption or ""
    await bot.send_message(
        OWNER_ID,
        f"✅ নতুন ভিডিও detect হয়েছে\n\nStorage Channel: <code>{message.chat.id}</code>\nVideo Code: <code>{code}</code>\nOriginal Bot Link:\n<code>{deep_link}</code>\n\n"
        "এখন এই link short করে Mini App Admin Panel-এ Title + Thumbnail + Short Link দিয়ে Publish করুন।\n\n"
        f"Channel caption: {caption[:500]}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


@dp.callback_query(F.data.startswith("set_tutorial:"))
async def set_tutorial_callback(query: CallbackQuery):
    if not query.from_user or not is_admin_id(query.from_user.id) or not has_perm(query.from_user.id, "can_manage_settings"):
        await query.answer("Admin permission required", show_alert=True)
        return
    code = (query.data or "").split(":", 1)[1].strip()
    rec = await lookup_video(code)
    if not rec:
        await query.answer("ভিডিও mapping পাওয়া যায়নি", show_alert=True)
        return
    await db_execute(
        "UPDATE app_settings SET tutorial_video_code=%s, tutorial_enabled=TRUE, updated_at=CURRENT_TIMESTAMP WHERE id='main'",
        (code,),
    )
    await query.answer("Tutorial video সেট হয়েছে ✅", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(OWNER_ID, f"✅ <code>{code}</code> এখন /start Tutorial Video হিসেবে সেট করা হয়েছে।", parse_mode=ParseMode.HTML)


async def broadcast_worker():
    while True:
        try:
            settings = await get_settings()
            videos = await db_fetchall(
                "SELECT * FROM videos WHERE published=TRUE AND broadcast_enabled=TRUE AND broadcast_sent=FALSE ORDER BY created_at ASC LIMIT 10"
            )
            for v in videos:
                users = await db_fetchall("SELECT user_id FROM bot_users WHERE is_active=TRUE")
                button_text = settings.get("broadcast_button_text") or "▶ ভিডিও ওপেন করুন"
                target_url = v.get("share_link") or (f"https://t.me/{BOT_USERNAME}?startapp={v.get('share_code')}" if v.get('share_code') else None)
                if not target_url:
                    continue
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=target_url)]])
                ok = fail = 0
                for row in users:
                    uid = int(row["user_id"])
                    try:
                        thumb = v.get("thumb") or ""
                        if thumb.startswith("http://") or thumb.startswith("https://"):
                            await bot.send_photo(uid, photo=thumb, caption=f"🎬 {v.get('title','নতুন ভিডিও')}", reply_markup=kb, protect_content=True)
                        elif thumb.startswith("data:image/") and "," in thumb:
                            header, encoded = thumb.split(",", 1)
                            ext = "png" if "png" in header else "jpg"
                            photo = BufferedInputFile(base64.b64decode(encoded), filename=f"thumb.{ext}")
                            await bot.send_photo(uid, photo=photo, caption=f"🎬 {v.get('title','নতুন ভিডিও')}", reply_markup=kb, protect_content=True)
                        else:
                            await bot.send_message(uid, f"🎬 {v.get('title','নতুন ভিডিও')}", reply_markup=kb, protect_content=True)
                        ok += 1
                        await asyncio.sleep(0.05)
                    except Exception:
                        fail += 1
                        await db_execute("UPDATE bot_users SET is_active=FALSE WHERE user_id=%s", (uid,))
                await db_execute("UPDATE videos SET broadcast_sent=TRUE WHERE id=%s", (v["id"],))
                await bot.send_message(OWNER_ID, f"📢 Broadcast complete\n{v.get('title','')}\n✅ Sent: {ok}\n❌ Failed: {fail}")
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
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
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


async def json_body(request):
    try:
        return await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")


async def index_handler(request):
    return web.FileResponse(INDEX_FILE)


async def health_handler(request):
    return web.json_response({"ok": True, "service": "viral-video-bot", "database": "postgresql", "bot": BOT_USERNAME})


async def api_bootstrap(request):
    u = request_user(request)
    is_admin = bool(u and is_admin_id(int(u.get("id", 0))))
    if is_admin:
        videos = await db_fetchall("SELECT * FROM videos WHERE published=TRUE ORDER BY created_at DESC")
    else:
        videos = await db_fetchall("SELECT id,share_code,title,category_id,thumb,short_url,published,views,created_at FROM videos WHERE published=TRUE ORDER BY created_at DESC")
    for v in videos:
        if v.get("share_code"):
            v["share_link"] = f"https://t.me/{BOT_USERNAME}?startapp={v['share_code']}"

    categories = await db_fetchall('SELECT id,name,icon,sort_order AS "order" FROM categories ORDER BY sort_order,id')
    viral = await db_fetchall("SELECT id,title,url FROM viral_links ORDER BY created_at DESC")
    settings = await get_settings()
    unlocked = []
    favorites = []
    my_reactions = []
    if u:
        uid=int(u["id"])
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
        comments_count=(await db_fetchone("SELECT COUNT(*) c FROM comments"))["c"]
        reactions_count=(await db_fetchone("SELECT COUNT(*) c FROM reactions"))["c"]
        stats = {"total_users": total_users, "video_requests": requests, "unlocks":unlocks, "comments":comments_count, "reactions":reactions_count}
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
        "bot_username": BOT_USERNAME if is_admin else None,
        "is_owner": bool(u and int(u.get("id",0)) == OWNER_ID),
        "admin_permissions": ADMIN_PERMS.get(int(u.get("id",0)), {}) if u and is_admin else {},
    }
    return web.Response(text=json.dumps(payload, default=str, ensure_ascii=False), content_type="application/json")


async def api_presence(request):
    u = request_user(request)
    if u:
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
        payload["total_users"] = (await db_fetchone("SELECT COUNT(*) c FROM bot_users"))["c"]
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
        return web.json_response({"opened_total":0,"opened_unique":0,"unlocked":0,"favorites":0,"reactions":0,"comments":0,"recent_unlocked":[]})
    opened = await db_fetchone("SELECT COALESCE(SUM(open_count),0) c, COUNT(*) unique_c FROM video_views WHERE user_id=%s", (uid,))
    unlocked = await db_fetchone("SELECT COUNT(DISTINCT video_code) c FROM video_requests WHERE user_id=%s AND delivered=TRUE", (uid,))
    favs = await db_fetchone("SELECT COUNT(*) c FROM favorites WHERE user_id=%s", (uid,))
    reacts = await db_fetchone("SELECT COUNT(*) c FROM reactions WHERE user_id=%s", (uid,))
    comments = await db_fetchone("SELECT COUNT(*) c FROM comments WHERE user_id=%s", (uid,))
    recent = await db_fetchall(
        """SELECT DISTINCT ON (v.id) v.id,v.title,v.thumb,r.created_at FROM video_requests r
           JOIN videos v ON v.video_code=r.video_code WHERE r.user_id=%s AND r.delivered=TRUE
           ORDER BY v.id,r.created_at DESC LIMIT 5""", (uid,)
    )
    return web.Response(text=json.dumps({
        "opened_total": int(opened["c"] or 0), "opened_unique": int(opened["unique_c"] or 0),
        "unlocked": int(unlocked["c"] or 0), "favorites": int(favs["c"] or 0),
        "reactions": int(reacts["c"] or 0), "comments": int(comments["c"] or 0), "recent_unlocked": recent
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


async def api_unlock_click(request):
    u=request_user(request)
    if not u: return web.json_response({"ok":True})
    vid=request.match_info['video_id']
    row=await db_fetchone("SELECT video_code FROM videos WHERE id=%s",(vid,))
    await db_execute("INSERT INTO user_video_events(user_id,video_id,video_code,event_type,created_at) VALUES(%s,%s,%s,'unlock_click',%s)",(int(u['id']),vid,row['video_code'] if row else None,utcnow_sql()))
    return web.json_response({"ok":True})


async def api_admin_video_save(request):
    require_admin(request, "can_manage_content")
    d = await json_body(request)
    required = ["id", "video_code", "title", "short_url"]
    if any(not str(d.get(k, "")).strip() for k in required):
        raise web.HTTPBadRequest(text="Missing required video fields")

    share_code = str(d.get("share_code") or "").strip() or f"v{int(time.time() * 1000)}"
    share_link = f"https://t.me/{BOT_USERNAME}?startapp={share_code}"
    await db_execute(
        """INSERT INTO videos(id,share_code,video_code,title,category_id,thumb,deep_link,short_url,broadcast_enabled,broadcast_sent,published,views,created_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,TRUE,0,%s)
           ON CONFLICT (id) DO UPDATE SET share_code=COALESCE(videos.share_code,EXCLUDED.share_code),video_code=EXCLUDED.video_code,title=EXCLUDED.title,category_id=EXCLUDED.category_id,
           thumb=EXCLUDED.thumb,deep_link=EXCLUDED.deep_link,short_url=EXCLUDED.short_url,
           broadcast_enabled=EXCLUDED.broadcast_enabled,broadcast_sent=FALSE,published=TRUE""",
        (d["id"], share_code, d["video_code"], d["title"], d.get("category_id"), d.get("thumb", ""), d.get("deep_link", ""), d["short_url"], bool(d.get("broadcast_enabled", True)), utcnow_sql()),
    )
    return web.json_response({"ok": True, "share_code": share_code, "share_link": share_link})


async def api_admin_video_delete(request):
    require_admin(request, "can_manage_content")
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
    fields = [
        "brand_name", "brand_subtitle", "hero_text", "nav_home", "nav_fav", "nav_unlock", "nav_viral", "nav_profile",
        "online_label", "show_online", "web_app_url", "bot_menu_button_text", "welcome_text", "watch_button_text",
        "broadcast_button_text", "auto_delete_minutes", "protect_content", "maintenance_mode",
        "tutorial_enabled", "tutorial_video_code", "tutorial_caption", "tutorial_button_text",
        "storage_channel_id", "maintenance_message", "support_url", "join_channel_url", "start_button_text",
        "comments_enabled", "reactions_enabled", "favorites_enabled", "profile_stats_enabled"
    ]
    vals = [d.get(f) for f in fields]
    bool_fields = {"show_online", "protect_content", "maintenance_mode", "tutorial_enabled", "comments_enabled", "reactions_enabled", "favorites_enabled", "profile_stats_enabled"}
    vals = [bool(v) if fields[i] in bool_fields else v for i, v in enumerate(vals)]
    placeholders = ",".join(["%s"] * (len(fields) + 1))
    updates = ",".join([f"{f}=EXCLUDED.{f}" for f in fields])
    await db_execute(
        f"INSERT INTO app_settings(id,{','.join(fields)}) VALUES({placeholders}) ON CONFLICT (id) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP",
        tuple(["main"] + vals),
    )
    await sync_menu_button()
    return web.json_response({"ok": True})


async def api_admin_admins(request):
    u = require_admin(request, "can_manage_admins")
    if request.method == "GET":
        rows = await db_fetchall("SELECT user_id,role,display_name,can_manage_content,can_manage_settings,can_broadcast,can_manage_users,can_manage_admins,created_at FROM admin_users ORDER BY created_at")
        return web.Response(text=json.dumps({"owner_id": OWNER_ID, "admins": rows}, default=str, ensure_ascii=False), content_type="application/json")
    d = await json_body(request)
    uid = int(d.get("user_id", 0))
    if uid <= 0 or uid == OWNER_ID: raise web.HTTPBadRequest(text="Invalid admin user id")
    vals = (uid, str(d.get("role") or "admin"), str(d.get("display_name") or "Admin")[:255], bool(d.get("can_manage_content",True)), bool(d.get("can_manage_settings",True)), bool(d.get("can_broadcast",True)), bool(d.get("can_manage_users",True)), bool(d.get("can_manage_admins",False)))
    await db_execute("""INSERT INTO admin_users(user_id,role,display_name,can_manage_content,can_manage_settings,can_broadcast,can_manage_users,can_manage_admins)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET role=EXCLUDED.role,display_name=EXCLUDED.display_name,can_manage_content=EXCLUDED.can_manage_content,can_manage_settings=EXCLUDED.can_manage_settings,can_broadcast=EXCLUDED.can_broadcast,can_manage_users=EXCLUDED.can_manage_users,can_manage_admins=EXCLUDED.can_manage_admins""", vals)
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
    require_admin(request, "can_manage_users")
    rows = await db_fetchall("SELECT user_id,username,first_name,last_name,is_active,last_seen_at,created_at FROM bot_users ORDER BY last_seen_at DESC LIMIT 500")
    return web.Response(text=json.dumps({"users":rows}, default=str, ensure_ascii=False), content_type="application/json")


async def api_admin_user_toggle(request):
    require_admin(request, "can_manage_users")
    uid=int(request.match_info["user_id"]); d=await json_body(request); active=bool(d.get("is_active",True))
    await db_execute("UPDATE bot_users SET is_active=%s WHERE user_id=%s", (active,uid))
    return web.json_response({"ok":True})


async def api_admin_broadcast(request):
    u=require_admin(request, "can_broadcast"); d=await json_body(request)
    typ=str(d.get("message_type") or "text"); text=str(d.get("text") or "").strip(); media=str(d.get("media_url") or "").strip(); bt=str(d.get("button_text") or "").strip(); bu=str(d.get("button_url") or "").strip()
    if not text and not media: raise web.HTTPBadRequest(text="Message or media required")
    users=await db_fetchall("SELECT user_id FROM bot_users WHERE is_active=TRUE")
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=bt,url=bu)]]) if bt and bu else None
    ok=fail=0
    for row in users:
        uid=int(row["user_id"])
        try:
            if typ=="photo" and media: await bot.send_photo(uid,photo=media,caption=text or None,reply_markup=kb,protect_content=True)
            elif typ=="video" and media: await bot.send_video(uid,video=media,caption=text or None,reply_markup=kb,protect_content=True)
            else: await bot.send_message(uid,text,reply_markup=kb,protect_content=True)
            ok+=1; await asyncio.sleep(0.05)
        except Exception:
            fail+=1
    await db_execute("INSERT INTO admin_broadcasts(created_by,message_type,text_content,media_url,button_text,button_url,sent_count,failed_count) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(int(u["id"]),typ,text,media,bt,bu,ok,fail))
    return web.json_response({"ok":True,"sent":ok,"failed":fail})


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
    app.router.add_get("/api/videos/{video_id}/social", api_video_social)
    app.router.add_post("/api/videos/{video_id}/favorite", api_toggle_favorite)
    app.router.add_post("/api/videos/{video_id}/reaction", api_toggle_reaction)
    app.router.add_get("/api/videos/{video_id}/comments", api_comments)
    app.router.add_post("/api/videos/{video_id}/comments", api_comments)
    app.router.add_post("/api/videos/{video_id}/unlock-click", api_unlock_click)
    app.router.add_post("/api/admin/videos", api_admin_video_save)
    app.router.add_delete("/api/admin/videos/{video_id}", api_admin_video_delete)
    app.router.add_post("/api/admin/categories", api_admin_category_save)
    app.router.add_post("/api/admin/viral-links", api_admin_viral_save)
    app.router.add_delete("/api/admin/viral-links/{link_id}", api_admin_viral_delete)
    app.router.add_post("/api/admin/settings", api_admin_settings_save)
    app.router.add_get("/api/admin/admins", api_admin_admins)
    app.router.add_post("/api/admin/admins", api_admin_admins)
    app.router.add_delete("/api/admin/admins/{user_id}", api_admin_admin_delete)
    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_post("/api/admin/users/{user_id}/toggle", api_admin_user_toggle)
    app.router.add_post("/api/admin/broadcast", api_admin_broadcast)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Mini App/API listening on 0.0.0.0:%s", PORT)
    return runner


async def main():
    await init_db()
    await sync_menu_button(force_log=True)
    web_runner = await start_web_server()
    asyncio.create_task(broadcast_worker())
    asyncio.create_task(menu_sync_worker())
    try:
        await dp.start_polling(bot, allowed_updates=["message", "channel_post"])
    finally:
        await web_runner.cleanup()
        if db_pool:
            await db_pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

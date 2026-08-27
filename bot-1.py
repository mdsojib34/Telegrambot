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


def utcnow_sql():
    return datetime.now(timezone.utc)


async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    if os.path.exists(schema_path):
        async with db_pool.acquire() as conn:
            await conn.execute(open(schema_path, "r", encoding="utf-8").read())
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


async def get_settings():
    try:
        row = await db_fetchone("SELECT * FROM app_settings WHERE id='main' LIMIT 1")
        if row:
            for key in ("protect_content", "maintenance_mode", "show_online"):
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
    try:
        return await db_fetchone("SELECT * FROM video_storage WHERE video_code=%s LIMIT 1", (code,))
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
    if settings.get("maintenance_mode") and message.from_user.id != OWNER_ID:
        await message.answer("⚙️ সিস্টেমটি এখন Maintenance Mode-এ আছে। পরে আবার চেষ্টা করুন।")
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
        minutes = int(settings.get("auto_delete_minutes") or 20)
        notice = await message.answer(f"✅ ভিডিও পাঠানো হয়েছে।\n⏳ {minutes} মিনিট পরে ভিডিওটি অটোমেটিক ডিলিট হবে।")
        asyncio.create_task(delete_later(message.chat.id, sent.message_id, minutes))
        asyncio.create_task(delete_later(message.chat.id, notice.message_id, minutes))
        await db_execute(
            "INSERT INTO video_requests(user_id,video_code,delivered,created_at) VALUES(%s,%s,TRUE,%s)",
            (message.from_user.id, code, utcnow_sql()),
        )
    except Exception:
        log.exception("copy video failed")
        await message.answer("❌ ভিডিও পাঠানো যায়নি। Storage Channel permission চেক করুন।")


@dp.message(CommandStart())
async def start_handler(message: Message):
    await save_user(message)
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload.startswith("video_"):
        await deliver_video(message, payload)
        return

    settings = await get_settings()
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
    if message.from_user and message.from_user.id == OWNER_ID and text == "/adminhelp":
        await message.answer(
            "Storage Channel-এ video upload করুন → Bot deep link তৈরি করে Owner inbox-এ পাঠাবে।\n"
            "Mini App URL ও Menu Button নাম Admin Settings থেকে পরিবর্তন করা যায়।"
        )


@dp.channel_post()
async def storage_channel_post(message: Message):
    if message.chat.id != STORAGE_CHANNEL_ID:
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Deep Link খুলুন", url=deep_link)]])
    caption = message.caption or ""
    await bot.send_message(
        OWNER_ID,
        f"✅ নতুন ভিডিও detect হয়েছে\n\nVideo Code: <code>{code}</code>\nOriginal Bot Link:\n<code>{deep_link}</code>\n\n"
        "এখন এই link short করে Mini App Admin Panel-এ Title + Thumbnail + Short Link দিয়ে Publish করুন।\n\n"
        f"Channel caption: {caption[:500]}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


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
                target_url = v.get("short_url") or v.get("deep_link")
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


def require_admin(request):
    u = request_user(request)
    if not u or int(u.get("id", 0)) != OWNER_ID:
        raise web.HTTPForbidden(text="Admin authorization required")
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
    is_admin = bool(u and int(u.get("id", 0)) == OWNER_ID)
    if is_admin:
        videos = await db_fetchall("SELECT * FROM videos WHERE published=TRUE ORDER BY created_at DESC")
    else:
        videos = await db_fetchall("SELECT id,title,category_id,thumb,short_url,published,views,created_at FROM videos WHERE published=TRUE ORDER BY created_at DESC")
    categories = await db_fetchall('SELECT id,name,icon,sort_order AS "order" FROM categories ORDER BY sort_order,id')
    viral = await db_fetchall("SELECT id,title,url FROM viral_links ORDER BY created_at DESC")
    settings = await get_settings()
    unlocked = []
    if u:
        rows = await db_fetchall("""SELECT DISTINCT v.id FROM video_requests r JOIN videos v ON v.video_code=r.video_code
                                  WHERE r.user_id=%s AND r.delivered=TRUE""", (int(u["id"]),))
        unlocked = [r["id"] for r in rows]
    safe_settings = dict(settings)
    # Credentials never exist in this table, but keep response explicitly UI-only.
    stats = None
    if u and int(u.get("id", 0)) == OWNER_ID:
        total_users = (await db_fetchone("SELECT COUNT(*) c FROM bot_users"))["c"]
        requests = (await db_fetchone("SELECT COUNT(*) c FROM video_requests"))["c"]
        stats = {"total_users": total_users, "video_requests": requests}
    payload = {
        "videos": videos,
        "categories": categories,
        "viral_links": viral,
        "settings": safe_settings,
        "unlocked": unlocked,
        "is_admin": is_admin,
        "stats": stats,
        "bot_username": BOT_USERNAME if is_admin else None,
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
    if u and int(u.get("id", 0)) == OWNER_ID:
        payload["total_users"] = (await db_fetchone("SELECT COUNT(*) c FROM bot_users"))["c"]
        payload["video_requests"] = (await db_fetchone("SELECT COUNT(*) c FROM video_requests"))["c"]
    return web.json_response(payload)


async def api_increment_view(request):
    video_id = request.match_info["video_id"]
    await db_execute("UPDATE videos SET views=views+1 WHERE id=%s", (video_id,))
    return web.json_response({"ok": True})


async def api_admin_video_save(request):
    require_admin(request)
    d = await json_body(request)
    required = ["id", "video_code", "title", "short_url"]
    if any(not str(d.get(k, "")).strip() for k in required):
        raise web.HTTPBadRequest(text="Missing required video fields")
    await db_execute(
        """INSERT INTO videos(id,video_code,title,category_id,thumb,deep_link,short_url,broadcast_enabled,broadcast_sent,published,views,created_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,FALSE,TRUE,0,%s)
           ON CONFLICT (id) DO UPDATE SET video_code=EXCLUDED.video_code,title=EXCLUDED.title,category_id=EXCLUDED.category_id,
           thumb=EXCLUDED.thumb,deep_link=EXCLUDED.deep_link,short_url=EXCLUDED.short_url,
           broadcast_enabled=EXCLUDED.broadcast_enabled,broadcast_sent=FALSE,published=TRUE""",
        (d["id"], d["video_code"], d["title"], d.get("category_id"), d.get("thumb", ""), d.get("deep_link", ""), d["short_url"], bool(d.get("broadcast_enabled", True)), utcnow_sql()),
    )
    return web.json_response({"ok": True})


async def api_admin_video_delete(request):
    require_admin(request)
    await db_execute("DELETE FROM videos WHERE id=%s", (request.match_info["video_id"],))
    return web.json_response({"ok": True})


async def api_admin_category_save(request):
    require_admin(request)
    d = await json_body(request)
    await db_execute(
        "INSERT INTO categories(id,name,icon,sort_order) VALUES(%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,icon=EXCLUDED.icon,sort_order=EXCLUDED.sort_order",
        (d["id"], d["name"], d.get("icon", "📁"), int(d.get("order", 0))),
    )
    return web.json_response({"ok": True})


async def api_admin_viral_save(request):
    require_admin(request)
    d = await json_body(request)
    await db_execute(
        "INSERT INTO viral_links(id,title,url,created_at) VALUES(%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,url=EXCLUDED.url",
        (d["id"], d["title"], d["url"], utcnow_sql()),
    )
    return web.json_response({"ok": True})


async def api_admin_viral_delete(request):
    require_admin(request)
    await db_execute("DELETE FROM viral_links WHERE id=%s", (request.match_info["link_id"],))
    return web.json_response({"ok": True})


async def api_admin_settings_save(request):
    require_admin(request)
    d = await json_body(request)
    fields = [
        "brand_name", "brand_subtitle", "hero_text", "nav_home", "nav_fav", "nav_unlock", "nav_viral", "nav_profile",
        "online_label", "show_online", "web_app_url", "bot_menu_button_text", "welcome_text", "watch_button_text",
        "broadcast_button_text", "auto_delete_minutes", "protect_content", "maintenance_mode"
    ]
    vals = [d.get(f) for f in fields]
    bool_fields = {"show_online", "protect_content", "maintenance_mode"}
    vals = [bool(v) if fields[i] in bool_fields else v for i, v in enumerate(vals)]
    placeholders = ",".join(["%s"] * (len(fields) + 1))
    updates = ",".join([f"{f}=EXCLUDED.{f}" for f in fields])
    await db_execute(
        f"INSERT INTO app_settings(id,{','.join(fields)}) VALUES({placeholders}) ON CONFLICT (id) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP",
        tuple(["main"] + vals),
    )
    await sync_menu_button()
    return web.json_response({"ok": True})


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
    app.router.add_post("/api/admin/videos", api_admin_video_save)
    app.router.add_delete("/api/admin/videos/{video_id}", api_admin_video_delete)
    app.router.add_post("/api/admin/categories", api_admin_category_save)
    app.router.add_post("/api/admin/viral-links", api_admin_viral_save)
    app.router.add_delete("/api/admin/viral-links/{link_id}", api_admin_viral_delete)
    app.router.add_post("/api/admin/settings", api_admin_settings_save)
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

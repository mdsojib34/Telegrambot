# V7 PostgreSQL + Render Setup

1. GitHub-এ একটি Private repository তৈরি করুন।
2. এই ZIP extract করে সব project file upload করুন। নিরাপত্তার জন্য `.env` GitHub-এ upload না করাই ভালো।
3. Render > New > Web Service > GitHub repository Connect করুন।
4. Runtime: Python 3.
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python bot.py`
7. Render Web Service > Environment-এ নিচের variables দিন:
   - BOT_TOKEN = BotFather-এর token
   - BOT_USERNAME = Hooo9999bot
   - OWNER_ID = 6994836801
   - STORAGE_CHANNEL_ID = -1003710925816
   - DATABASE_URL = আপনার Render PostgreSQL Internal Database URL
   - MENU_BUTTON_TEXT = 🎬 Video open
   - BROADCAST_POLL_SECONDS = 15
   - MENU_SYNC_SECONDS = 60
   MINI_APP_URL blank রাখা যায়; Render-এর RENDER_EXTERNAL_URL থাকলে app সেটি ব্যবহার করবে। প্রয়োজনে deploy-এর পর Render public HTTPS URL এখানে দিন।
8. Deploy করুন। `/health` URL খুলে ok:true এবং database:postgresql দেখুন।
9. @Hooo9999bot-কে Storage Channel-এর admin করুন।
10. Bot-এ /start দিন। Menu button থেকে Mini App খুলবে।
11. Storage Channel-এ video upload করলে Owner inbox-এ `?start=video_xxx` deep link আসবে।
12. Deep link short করে Mini App Admin Panel-এ Title + Thumbnail + Short URL publish করুন।
13. User short URL complete করে bot deep link-এ এলে protected video পাবে; default 20 মিনিট পরে delete হবে।

Security: এই package-এর `.env`-এ বর্তমানে supplied credentials আছে। Setup test শেষে BotFather token revoke/regenerate এবং Render PostgreSQL credentials rotate করে Render Environment-এ নতুন values বসান।

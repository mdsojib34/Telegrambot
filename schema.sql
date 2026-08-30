-- PostgreSQL schema for PRO_TM_VIDEO_ZOOM V7 (Render)
CREATE TABLE IF NOT EXISTS video_storage (
  video_code VARCHAR(100) PRIMARY KEY, channel_id BIGINT NOT NULL, message_id BIGINT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bot_users (
  user_id BIGINT PRIMARY KEY, username VARCHAR(255), first_name VARCHAR(255), last_name VARCHAR(255), is_active BOOLEAN DEFAULT TRUE,
  last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS video_requests (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, video_code VARCHAR(100) NOT NULL, delivered BOOLEAN DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_vr_user ON video_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_vr_code ON video_requests(video_code);
CREATE TABLE IF NOT EXISTS categories (
  id VARCHAR(100) PRIMARY KEY, name VARCHAR(255) NOT NULL, icon VARCHAR(50) DEFAULT '📁', sort_order INTEGER DEFAULT 0
);
INSERT INTO categories(id,name,icon,sort_order) VALUES ('cat_viral','ভাইরাল','🔥',1) ON CONFLICT (id) DO NOTHING;
CREATE TABLE IF NOT EXISTS videos (
  id VARCHAR(100) PRIMARY KEY, share_code VARCHAR(100) UNIQUE, video_code VARCHAR(100) NOT NULL UNIQUE, title VARCHAR(500) NOT NULL, category_id VARCHAR(100), thumb TEXT,
  deep_link TEXT, short_url TEXT NOT NULL, broadcast_enabled BOOLEAN DEFAULT TRUE, broadcast_sent BOOLEAN DEFAULT FALSE,
  published BOOLEAN DEFAULT TRUE, views BIGINT DEFAULT 0, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published,created_at);

-- V9 package/share deep-link migration for existing databases
ALTER TABLE videos ADD COLUMN IF NOT EXISTS share_code VARCHAR(100);
CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_share_code ON videos(share_code) WHERE share_code IS NOT NULL;
UPDATE videos SET share_code = 'v' || regexp_replace(id, '[^0-9]', '', 'g')
WHERE share_code IS NULL AND regexp_replace(id, '[^0-9]', '', 'g') <> '';

CREATE TABLE IF NOT EXISTS viral_links (
  id VARCHAR(100) PRIMARY KEY, title VARCHAR(500) NOT NULL, url TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS miniapp_presence (
  user_id BIGINT PRIMARY KEY, username VARCHAR(255), first_name VARCHAR(255), last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_presence_seen ON miniapp_presence(last_seen_at);
CREATE TABLE IF NOT EXISTS app_settings (
  id VARCHAR(32) PRIMARY KEY,
  brand_name VARCHAR(255) DEFAULT 'Bangladesh Viral Video',
  brand_subtitle VARCHAR(255) DEFAULT 'PREMIUM VIDEO HUB',
  hero_text TEXT,
  nav_home VARCHAR(80) DEFAULT 'হোম',
  nav_fav VARCHAR(80) DEFAULT 'পছন্দ',
  nav_unlock VARCHAR(80) DEFAULT 'আনলক',
  nav_viral VARCHAR(80) DEFAULT 'ভাইরাল লিংক',
  nav_profile VARCHAR(80) DEFAULT 'প্রোফাইল',
  online_label VARCHAR(80) DEFAULT 'Online',
  show_online BOOLEAN DEFAULT TRUE,
  web_app_url TEXT,
  bot_menu_button_text VARCHAR(64) DEFAULT '🎬 Video open',
  welcome_text TEXT,
  watch_button_text VARCHAR(100) DEFAULT '▶ ভিডিও দেখুন',
  broadcast_button_text VARCHAR(100) DEFAULT '▶ ভিডিও ওপেন করুন',
  auto_delete_minutes INTEGER DEFAULT 20,
  protect_content BOOLEAN DEFAULT TRUE,
  maintenance_mode BOOLEAN DEFAULT FALSE,
  tutorial_enabled BOOLEAN DEFAULT TRUE,
  tutorial_video_code VARCHAR(100),
  tutorial_caption TEXT DEFAULT '🎓 ভিডিও কীভাবে দেখবেন\n\nএই ছোট ভিডিওটি দেখে নিন। তারপর নিচের বাটন থেকে ভিডিও অ্যাপ খুলে আপনার পছন্দের ভিডিও দেখুন।',
  tutorial_button_text VARCHAR(100) DEFAULT '🎬 ভিডিও দেখতে শুরু করুন',
  storage_channel_id BIGINT,
  maintenance_message TEXT DEFAULT '⚙️ সিস্টেমটি এখন Maintenance Mode-এ আছে। পরে আবার চেষ্টা করুন।',
  support_url TEXT,
  join_channel_url TEXT,
  start_button_text VARCHAR(100) DEFAULT '🎬 ভিডিও দেখতে শুরু করুন',
  comments_enabled BOOLEAN DEFAULT TRUE,
  reactions_enabled BOOLEAN DEFAULT TRUE,
  favorites_enabled BOOLEAN DEFAULT TRUE,
  profile_stats_enabled BOOLEAN DEFAULT TRUE,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Full idempotent migration for databases created by older versions.
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS brand_name VARCHAR(255) DEFAULT 'Bangladesh Viral Video';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS brand_subtitle VARCHAR(255) DEFAULT 'PREMIUM VIDEO HUB';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS hero_text TEXT;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS nav_home VARCHAR(80) DEFAULT 'হোম';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS nav_fav VARCHAR(80) DEFAULT 'পছন্দ';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS nav_unlock VARCHAR(80) DEFAULT 'আনলক';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS nav_viral VARCHAR(80) DEFAULT 'ভাইরাল লিংক';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS nav_profile VARCHAR(80) DEFAULT 'প্রোফাইল';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS online_label VARCHAR(80) DEFAULT 'Online';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS show_online BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS web_app_url TEXT;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS bot_menu_button_text VARCHAR(64) DEFAULT '🎬 Video open';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS welcome_text TEXT;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS watch_button_text VARCHAR(100) DEFAULT '▶ ভিডিও দেখুন';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS broadcast_button_text VARCHAR(100) DEFAULT '▶ ভিডিও ওপেন করুন';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS auto_delete_minutes INTEGER DEFAULT 20;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS protect_content BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS maintenance_mode BOOLEAN DEFAULT FALSE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS tutorial_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS tutorial_video_code VARCHAR(100);
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS tutorial_caption TEXT DEFAULT '🎓 ভিডিও কীভাবে দেখবেন\n\nএই ছোট ভিডিওটি দেখে নিন। তারপর নিচের বাটন থেকে ভিডিও অ্যাপ খুলে আপনার পছন্দের ভিডিও দেখুন।';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS tutorial_button_text VARCHAR(100) DEFAULT '🎬 ভিডিও দেখতে শুরু করুন';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS storage_channel_id BIGINT;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS maintenance_message TEXT DEFAULT '⚙️ সিস্টেমটি এখন Maintenance Mode-এ আছে। পরে আবার চেষ্টা করুন।';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS support_url TEXT;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS join_channel_url TEXT;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS start_button_text VARCHAR(100) DEFAULT '🎬 ভিডিও দেখতে শুরু করুন';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS comments_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS favorites_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS profile_stats_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

INSERT INTO app_settings(id,brand_name,brand_subtitle,hero_text,welcome_text)
VALUES ('main','Bangladesh Viral Video','PREMIUM VIDEO HUB','নতুন ও ট্রেন্ডিং ভিডিও খুঁজুন, পছন্দ করুন এবং নিরাপদভাবে আনলক করুন।','👋 স্বাগতম! নিচের বাটন থেকে ভিডিও অ্যাপ খুলুন।')
ON CONFLICT (id) DO NOTHING;

-- V8 social + profile analytics
CREATE TABLE IF NOT EXISTS video_views (
  user_id BIGINT NOT NULL, video_id VARCHAR(100) NOT NULL, open_count INTEGER DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(user_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_video_views_user ON video_views(user_id);

CREATE TABLE IF NOT EXISTS favorites (
  user_id BIGINT NOT NULL, video_id VARCHAR(100) NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(user_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_favorites_video ON favorites(video_id);

CREATE TABLE IF NOT EXISTS reactions (
  user_id BIGINT NOT NULL, video_id VARCHAR(100) NOT NULL, reaction VARCHAR(32) DEFAULT 'heart',
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_reactions_video ON reactions(video_id);

CREATE TABLE IF NOT EXISTS comments (
  id BIGSERIAL PRIMARY KEY, video_id VARCHAR(100) NOT NULL, user_id BIGINT NOT NULL,
  display_name VARCHAR(255), username VARCHAR(255), text VARCHAR(700) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_video_events (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, video_id VARCHAR(100), video_code VARCHAR(100),
  event_type VARCHAR(50) NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_uve_user ON user_video_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uve_type ON user_video_events(event_type);


-- V11 admin control tables
CREATE TABLE IF NOT EXISTS admin_users (
  user_id BIGINT PRIMARY KEY,
  role VARCHAR(30) DEFAULT 'admin',
  display_name VARCHAR(255),
  can_manage_content BOOLEAN DEFAULT TRUE,
  can_manage_settings BOOLEAN DEFAULT TRUE,
  can_broadcast BOOLEAN DEFAULT TRUE,
  can_manage_users BOOLEAN DEFAULT TRUE,
  can_manage_admins BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_broadcasts (
  id BIGSERIAL PRIMARY KEY,
  created_by BIGINT NOT NULL,
  message_type VARCHAR(20) DEFAULT 'text',
  text_content TEXT,
  media_url TEXT,
  button_text VARCHAR(100),
  button_url TEXT,
  sent_count INTEGER DEFAULT 0,
  failed_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================
-- V13 AdsGram rewarded unlock + durable delete queue + package editor
-- =========================================================
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS adsgram_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS adsgram_block_id VARCHAR(100) DEFAULT 'int-45179';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS required_ads_default INTEGER DEFAULT 1;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS ad_button_text VARCHAR(100) DEFAULT '📢 Ad দেখুন';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS ad_unlock_text VARCHAR(100) DEFAULT '🔓 ভিডিও আনলক করুন';

ALTER TABLE videos ADD COLUMN IF NOT EXISTS required_ads INTEGER DEFAULT 1;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS featured BOOLEAN DEFAULT FALSE;
ALTER TABLE videos ADD COLUMN IF NOT EXISTS trending BOOLEAN DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS ad_sessions (
  session_token VARCHAR(160) PRIMARY KEY,
  user_id BIGINT NOT NULL,
  video_id VARCHAR(100) NOT NULL,
  status VARCHAR(20) DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ad_sessions_user_video ON ad_sessions(user_id, video_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ad_completions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  video_id VARCHAR(100) NOT NULL,
  session_token VARCHAR(160) UNIQUE NOT NULL,
  provider VARCHAR(40) DEFAULT 'adsgram',
  block_id VARCHAR(100),
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ad_completions_user_video ON ad_completions(user_id, video_id, created_at DESC);

CREATE TABLE IF NOT EXISTS delete_queue (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  delete_at TIMESTAMPTZ NOT NULL,
  status VARCHAR(20) DEFAULT 'pending',
  retry_count INTEGER DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_delete_queue_due ON delete_queue(status, delete_at);


-- V14 two-bot delivery architecture
CREATE TABLE IF NOT EXISTS video_bot_users (
  user_id BIGINT PRIMARY KEY, username VARCHAR(255), first_name VARCHAR(255), last_name VARCHAR(255), is_active BOOLEAN DEFAULT TRUE,
  last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_video_bot_users_seen ON video_bot_users(last_seen_at);
ALTER TABLE videos ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(20) DEFAULT 'video_bot';
ALTER TABLE delete_queue ADD COLUMN IF NOT EXISTS bot_kind VARCHAR(20) DEFAULT 'main';
UPDATE delete_queue SET bot_kind='main' WHERE bot_kind IS NULL;

-- V15 Auto Thumbnail + Channel Caption Draft System
ALTER TABLE videos ADD COLUMN IF NOT EXISTS thumb_text TEXT;

CREATE TABLE IF NOT EXISTS upload_drafts (
  id BIGSERIAL PRIMARY KEY,
  video_code VARCHAR(100) UNIQUE NOT NULL,
  channel_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  title VARCHAR(500),
  caption_text TEXT,
  thumb TEXT,
  deep_link TEXT,
  consumed BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_upload_drafts_pending ON upload_drafts(consumed, created_at DESC);

-- V16 smart auto thumbnail + multi-admin notification metadata
ALTER TABLE upload_drafts ADD COLUMN IF NOT EXISTS uploader_label VARCHAR(255) DEFAULT 'Channel Upload';
CREATE INDEX IF NOT EXISTS idx_upload_drafts_uploader ON upload_drafts(uploader_label);

-- =========================================================
-- V17 Join/Leave Welcome Manager
-- =========================================================
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS welcome_manager_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS join_request_welcome_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS direct_join_welcome_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS leave_inbox_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS auto_approve_join_requests BOOLEAN DEFAULT FALSE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS join_welcome_text TEXT DEFAULT '👋 স্বাগতম! আমাদের ভিডিও কমিউনিটিতে আপনাকে স্বাগতম। নিচের বাটন থেকে ভিডিও অ্যাপ খুলুন।';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS leave_inbox_text TEXT DEFAULT '😢 আপনি আমাদের গ্রুপ/চ্যানেল থেকে বের হয়ে গেছেন। নতুন ভিডিও মিস না করতে আবার যুক্ত হতে পারেন।';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS welcome_video_button_text VARCHAR(100) DEFAULT '🎬 ভিডিও ওপেন করুন';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS welcome_start_button_text VARCHAR(100) DEFAULT '🚀 Start Bot';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS welcome_rejoin_button_text VARCHAR(100) DEFAULT '🔄 আবার Join করুন';

CREATE TABLE IF NOT EXISTS managed_chats (
  chat_id BIGINT PRIMARY KEY,
  title VARCHAR(255),
  chat_type VARCHAR(30) DEFAULT 'group',
  join_url TEXT,
  enabled BOOLEAN DEFAULT TRUE,
  join_request_welcome BOOLEAN DEFAULT TRUE,
  direct_join_welcome BOOLEAN DEFAULT TRUE,
  leave_welcome BOOLEAN DEFAULT TRUE,
  auto_approve BOOLEAN,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_managed_chats_enabled ON managed_chats(enabled);

CREATE TABLE IF NOT EXISTS join_leave_events (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  event_type VARCHAR(30) NOT NULL,
  inbox_sent BOOLEAN DEFAULT FALSE,
  error_text TEXT,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_join_leave_events_chat ON join_leave_events(chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_join_leave_events_user ON join_leave_events(user_id, created_at DESC);

-- =========================================================
-- V18 THREE-BOT CENTRAL CONTROL
-- MINI_BOT = Mini App launcher + admin control center
-- BOT      = notification/community bot
-- VIDEO_BOT = protected video delivery
-- =========================================================
CREATE TABLE IF NOT EXISTS mini_bot_users (
  user_id BIGINT PRIMARY KEY,
  username VARCHAR(255),
  first_name VARCHAR(255),
  last_name VARCHAR(255),
  is_active BOOLEAN DEFAULT TRUE,
  last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mini_bot_users_seen ON mini_bot_users(last_seen_at DESC);

ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS mini_bot_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS notification_bot_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS video_bot_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS gallery_layout VARCHAR(30) DEFAULT 'phone_gallery';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS gallery_show_description BOOLEAN DEFAULT TRUE;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS gallery_show_social BOOLEAN DEFAULT TRUE;

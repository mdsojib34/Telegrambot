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
  id VARCHAR(100) PRIMARY KEY, video_code VARCHAR(100) NOT NULL UNIQUE, title VARCHAR(500) NOT NULL, category_id VARCHAR(100), thumb TEXT,
  deep_link TEXT, short_url TEXT NOT NULL, broadcast_enabled BOOLEAN DEFAULT TRUE, broadcast_sent BOOLEAN DEFAULT FALSE,
  published BOOLEAN DEFAULT TRUE, views BIGINT DEFAULT 0, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published,created_at);
CREATE TABLE IF NOT EXISTS viral_links (
  id VARCHAR(100) PRIMARY KEY, title VARCHAR(500) NOT NULL, url TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS miniapp_presence (
  user_id BIGINT PRIMARY KEY, username VARCHAR(255), first_name VARCHAR(255), last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_presence_seen ON miniapp_presence(last_seen_at);
CREATE TABLE IF NOT EXISTS app_settings (
  id VARCHAR(32) PRIMARY KEY, brand_name VARCHAR(255) DEFAULT 'Bangladesh Viral Video', brand_subtitle VARCHAR(255) DEFAULT 'PREMIUM VIDEO HUB',
  hero_text TEXT, nav_home VARCHAR(80) DEFAULT 'হোম', nav_fav VARCHAR(80) DEFAULT 'পছন্দ', nav_unlock VARCHAR(80) DEFAULT 'আনলক',
  nav_viral VARCHAR(80) DEFAULT 'ভাইরাল লিংক', nav_profile VARCHAR(80) DEFAULT 'প্রোফাইল', online_label VARCHAR(80) DEFAULT 'Online',
  show_online BOOLEAN DEFAULT TRUE, web_app_url TEXT, bot_menu_button_text VARCHAR(64) DEFAULT '🎬 Video open', welcome_text TEXT,
  watch_button_text VARCHAR(100) DEFAULT '▶ ভিডিও দেখুন', broadcast_button_text VARCHAR(100) DEFAULT '▶ ভিডিও ওপেন করুন',
  auto_delete_minutes INTEGER DEFAULT 20, protect_content BOOLEAN DEFAULT TRUE, maintenance_mode BOOLEAN DEFAULT FALSE,
  updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO app_settings(id,brand_name,brand_subtitle,hero_text,web_app_url,welcome_text)
VALUES ('main','Bangladesh Viral Video','PREMIUM VIDEO HUB','নতুন ও ট্রেন্ডিং ভিডিও খুঁজুন, পছন্দ করুন এবং নিরাপদভাবে আনলক করুন।',NULL,'👋 স্বাগতম! নিচের বাটন থেকে ভিডিও অ্যাপ খুলুন।')
ON CONFLICT (id) DO NOTHING;

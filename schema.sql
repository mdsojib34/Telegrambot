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

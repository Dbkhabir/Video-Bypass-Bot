# 🎥 Video Bypass Telegram Bot

Telegram bot that downloads videos from streaming sites and sends them directly via MTProto (2GB upload support).

## ✅ Supported Sites
- Luluvdo
- Vidara
- Brainzaps
- Streamtape

## ⚙️ Setup

### 1. Get Credentials

| Variable | Where to Get |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → /newbot |
| `TELEGRAM_API_ID` | [my.telegram.org](https://my.telegram.org) → API Development Tools |
| `TELEGRAM_API_HASH` | [my.telegram.org](https://my.telegram.org) → API Development Tools |

### 2. Deploy on Railway

1. Push this code to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. Add environment variables in Railway dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_API_ID`
   - `TELEGRAM_API_HASH`
5. Railway will auto-detect the Dockerfile and deploy

### 3. Run Locally (Optional)

```bash
# Install dependencies
pip install -r requirements.txt

# Install ffmpeg
# Ubuntu/Debian: sudo apt install ffmpeg
# Mac: brew install ffmpeg

# Set environment variables
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_API_ID="your_api_id"
export TELEGRAM_API_HASH="your_api_hash"

# Run
python bot.py
```

## 📋 Bot Commands
- `/start` — Start the bot
- `/help` — How to use
- `/queue` — Check active/queued tasks

## 🔧 Features
- 📤 2GB upload via MTProto (Pyrogram)
- 🖼️ Auto thumbnail generation
- 📊 Video metadata (resolution, duration, size)
- ⚡ 3 concurrent downloads, rest queued
- 🔄 ffmpeg + yt-dlp fallback for reliability

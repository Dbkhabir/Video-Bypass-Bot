# 🎬 Video Bypass Bot

> 🤖 Telegram Bot যা স্ট্রিমিং সাইট থেকে ভিডিও ডাউনলোড করে সরাসরি Telegram-এ পাঠায়!

---

## ✨ Features

- 🔗 **লিংক বাইপাস** — স্ট্রিমিং সাইট থেকে সরাসরি ভিডিও URL বের করে
- 📥 **অটো ডাউনলোড** — ffmpeg দিয়ে HLS/m3u8 স্ট্রিম ডাউনলোড
- 📤 **2GB পর্যন্ত আপলোড** — Pyrogram MTProto দিয়ে বড় ফাইল সাপোর্ট
- 📊 **রিয়েল-টাইম প্রগ্রেস** — ডাউনলোড ও আপলোডে লাইভ প্রগ্রেস বার, স্পিড, ETA
- 🖼️ **থাম্বনেইল** — অটো জেনারেট করা ভিডিও থাম্বনেইল
- ⚡ **3টা একসাথে** — সর্বোচ্চ 3টা ভিডিও একসাথে প্রসেস, বাকিগুলো কিউতে
- 🛑 **ক্যান্সেল বাটন** — প্রতিটা টাস্কে ইনলাইন ক্যান্সেল বাটন
- 🔄 **অটো রিট্রাই** — এক্সট্রাকশন ফেইল হলে 3 বার চেষ্টা করে

---

## 🌐 Supported Sites

| সাইট | স্ট্যাটাস | টাইপ |
|-------|----------|------|
| 🟢 Luluvdo | ✅ সাপোর্টেড | HLS/m3u8 |
| 🟢 Vidara | ✅ সাপোর্টেড | HLS/m3u8 |
| 🟢 Brainzaps | ✅ সাপোর্টেড | HLS/m3u8 |
| 🟢 Streamtape | ✅ সাপোর্টেড | Direct HTTP |

---

## 📁 Files

```
📦 video-bypass-bot
├── 🐍 bot.py              # মূল bot কোড
├── 📋 requirements.txt     # Python packages
├── 🐳 Dockerfile           # Docker build config
├── 🚂 railway.json         # Railway deployment config
├── 🚫 .dockerignore        # Docker exclude list
└── 📖 README.md            # এই ফাইল
```

---

## 🚀 Railway Deploy

### 📌 Step 1: GitHub Repository

1. 🆕 GitHub-এ নতুন **Private** repository তৈরি করো
2. 📤 এই সব ফাইল repository-তে push করো

```bash
git init
git add .
git commit -m "🚀 Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### 📌 Step 2: Railway Setup

1. 🌐 [railway.app](https://railway.app) এ যাও
2. ➕ **"New Project"** → **"Deploy from GitHub repo"**
3. 📂 তোমার GitHub repo সিলেক্ট করো

### 📌 Step 3: Environment Variables

Railway Dashboard → তোমার service → **Variables** tab এ এই 3টা variable add করো:

| 🔑 Variable | 📝 Value |
|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | তোমার bot token |
| `TELEGRAM_API_ID` | তোমার API ID (number) |
| `TELEGRAM_API_HASH` | তোমার API Hash |

> 💡 এগুলো [my.telegram.org](https://my.telegram.org) থেকে পাবে

### 📌 Step 4: Deploy

✅ Variables সেট করার পর Railway **অটোমেটিক deploy** করবে।
📋 **Logs** tab-এ দেখো `Bot connected!` আসছে কিনা।

---

## 🤖 Bot Commands

| Command | কাজ |
|---------|-----|
| `/start` | 🏠 Bot শুরু করো |
| `/help` | 📚 সাহায্য দেখো |
| `/queue` | 📊 চলমান টাস্ক দেখো |
| `/cancel` | 🛑 সব টাস্ক বাতিল করো |

---

## 📊 কিভাবে কাজ করে

```
🔗 লিংক পাঠাও
    ↓
🔍 পেজ অ্যানালাইজ
    ↓
🎯 ডাইরেক্ট URL এক্সট্রাক্ট
    ↓
📥 ffmpeg দিয়ে ডাউনলোড (প্রগ্রেস বার সহ)
    ↓
📤 Telegram-এ আপলোড (প্রগ্রেস বার সহ)
    ↓
✅ ভিডিও ডেলিভারি!
```

---

## ⚙️ Tech Stack

- 🐍 **Python 3.12**
- 📱 **Pyrogram** — MTProto Telegram client
- 🎥 **ffmpeg** — HLS/m3u8 ভিডিও ডাউনলোড
- 🐳 **Docker** — কনটেইনারাইজড ডেপ্লয়মেন্ট
- 🚂 **Railway** — ক্লাউড হোস্টিং

---

## 🛠️ Troubleshooting

| ❌ সমস্যা | ✅ সমাধান |
|-----------|----------|
| `Missing TELEGRAM_BOT_TOKEN` | 🔑 Variables ঠিকমত সেট করো |
| Bot crash হচ্ছে | 📋 Logs tab চেক করো |
| ভিডিও ডাউনলোড হচ্ছে না | 🔄 Railway Dashboard-এ Redeploy করো |
| Upload ফেইল হচ্ছে | 📦 ফাইল 2GB এর বেশি হতে পারে |

---

> 💬 লিংক পাঠাও, ভিডিও পাও! 🎉

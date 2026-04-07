import os
import re
import time
import logging
import tempfile
import subprocess
import asyncio
import requests
from urllib.parse import urlparse
from collections import deque

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ParseMode, ChatAction

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 3
active_tasks: dict[int, asyncio.Task] = {}
task_queue: deque = deque()
queue_lock = asyncio.Lock()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

SUPPORTED_DOMAINS = [
    "luluvdo.com",
    "vidara.to",
    "vidara.so",
    "brainzaps.tv",
    "streamtape.com",
    "streamtape.to",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def is_supported_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        return any(d in domain for d in SUPPORTED_DOMAINS)
    except Exception:
        return False


def get_site_name(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        for d in SUPPORTED_DOMAINS:
            if d in domain:
                return d.split(".")[0].capitalize()
        return "Unknown"
    except Exception:
        return "Unknown"


def extract_streamtape(url: str) -> dict:
    result = {"direct_url": None, "title": "Streamtape Video", "error": None}
    try:
        url = url.replace("/e/", "/v/")
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text

        title_match = re.search(r"<title>([^<]+)</title>", html)
        if title_match:
            t = title_match.group(1).strip()
            if t and "streamtape" not in t.lower():
                result["title"] = t

        js_match = re.search(
            r"document\.getElementById\('norobotlink'\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)\.substring\((\d+)\)\.substring\((\d+)\)",
            html,
        )
        if js_match:
            part1 = js_match.group(1)
            part2 = js_match.group(2)
            sub1 = int(js_match.group(3))
            sub2 = int(js_match.group(4))
            token_part = part2[sub1:][sub2:]
            result["direct_url"] = "https:" + part1 + token_part
        else:
            result["error"] = "Could not find video token in page"
    except Exception as e:
        result["error"] = str(e)
    return result


def extract_luluvdo(url: str) -> dict:
    result = {"direct_url": None, "title": "Luluvdo Video", "error": None}
    try:
        video_id = url.rstrip("/").split("/")[-1]
        embed_url = f"https://luluvdo.com/e/{video_id}"
        resp = requests.get(
            embed_url,
            headers={**HEADERS, "Referer": "https://luluvdo.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text

        title_match = re.search(r"<title>([^<]+)</title>", html)
        if title_match:
            t = title_match.group(1).strip().replace(" - LuluStream", "")
            if t:
                result["title"] = t

        m3u8 = re.findall(r"(https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*)", html)
        if m3u8:
            result["direct_url"] = m3u8[0]
        else:
            result["error"] = "No m3u8 URL found in embed page"
    except Exception as e:
        result["error"] = str(e)
    return result


def extract_brainzaps(url: str) -> dict:
    result = {"direct_url": None, "title": "Brainzaps Video", "error": None}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text

        title_match = re.search(r"<h2[^>]*>([^<]+)</h2>", html)
        if not title_match:
            title_match = re.search(r"<title>([^<]+)</title>", html)
        if title_match:
            t = title_match.group(1).strip()
            for remove in ["Watch ", " online", " - Brainzaps"]:
                t = t.replace(remove, "")
            if t:
                result["title"] = t

        eval_match = re.search(
            r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',(\d+),(\d+),'([^']*)'",
            html,
            re.DOTALL,
        )
        if not eval_match:
            result["error"] = "No packed JS found"
            return result

        encoded_str = eval_match.group(1)
        a = int(eval_match.group(2))
        c = int(eval_match.group(3))
        keywords = eval_match.group(4).split("|")

        def base_convert(num, base):
            chars = "0123456789abcdefghijklmnopqrstuvwxyz"
            if num < base:
                return chars[num]
            return base_convert(num // base, base) + chars[num % base]

        decoded = encoded_str
        while c > 0:
            c -= 1
            if keywords[c]:
                pattern = r"\b" + base_convert(c, a) + r"\b"
                decoded = re.sub(pattern, keywords[c], decoded)

        m3u8 = re.findall(r"(https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*)", decoded)
        if m3u8:
            result["direct_url"] = m3u8[0]
        else:
            result["error"] = "No m3u8 URL found in decoded JS"
    except Exception as e:
        result["error"] = str(e)
    return result


def extract_vidara(url: str) -> dict:
    result = {"direct_url": None, "title": "Vidara Video", "error": None}
    try:
        video_id = url.rstrip("/").split("/")[-1]
        resp = requests.post(
            "https://vidara.so/api/stream",
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "Referer": f"https://vidara.so/e/{video_id}",
                "Origin": "https://vidara.so",
            },
            json={"filecode": video_id, "device": "web"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            result["direct_url"] = data.get("streaming_url")
            result["title"] = data.get("title", "Vidara Video")
        else:
            result["error"] = f"API returned status {resp.status_code}"
    except Exception as e:
        result["error"] = str(e)
    return result


def detect_site(url: str) -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    if "streamtape" in domain:
        return "streamtape"
    if "luluvdo" in domain:
        return "luluvdo"
    if "brainzaps" in domain:
        return "brainzaps"
    if "vidara" in domain:
        return "vidara"
    return "unknown"


EXTRACTORS = {
    "streamtape": extract_streamtape,
    "luluvdo": extract_luluvdo,
    "brainzaps": extract_brainzaps,
    "vidara": extract_vidara,
}


def download_video(direct_url: str, site: str, original_url: str = "") -> str | None:
    tmp_path = os.path.join(tempfile.gettempdir(), f"tgbot_{int(time.time())}.mp4")

    if site == "luluvdo" and original_url:
        try:
            import yt_dlp
            video_id = original_url.rstrip("/").split("/")[-1]
            page_url = f"https://luluvdo.com/e/{video_id}"
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "best[ext=mp4]/best",
                "outtmpl": tmp_path,
                "merge_output_format": "mp4",
                "socket_timeout": 30,
                "nocheckcertificate": True,
                "geo_bypass": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([page_url])
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                return tmp_path
            alt_path = tmp_path.rsplit(".", 1)[0] + ".mp4"
            if os.path.exists(alt_path) and os.path.getsize(alt_path) > 0:
                return alt_path
        except Exception as e:
            logger.warning(f"yt-dlp direct download failed for luluvdo, falling back to ffmpeg: {e}")

    if site == "streamtape":
        try:
            resp = requests.get(
                direct_url,
                headers={**HEADERS, "Referer": "https://streamtape.com/"},
                stream=True,
                timeout=120,
                allow_redirects=True,
            )
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                return tmp_path
        except Exception as e:
            logger.error(f"Streamtape download error: {e}")
        return None

    referer_map = {
        "luluvdo": "https://luluvdo.com/",
        "brainzaps": "https://brainzaps.tv/",
        "vidara": "https://vidara.so/",
    }
    origin_map = {
        "luluvdo": "https://luluvdo.com",
        "brainzaps": "https://brainzaps.tv",
        "vidara": "https://vidara.so",
    }
    referer = referer_map.get(site, "")
    origin = origin_map.get(site, "")

    headers_str = f"User-Agent: {HEADERS['User-Agent']}\r\n"
    if referer:
        headers_str += f"Referer: {referer}\r\n"
    if origin:
        headers_str += f"Origin: {origin}\r\n"

    cmd = [
        "ffmpeg", "-y",
        "-headers", headers_str,
        "-i", direct_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        tmp_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            return tmp_path
        logger.warning(f"ffmpeg failed, trying yt-dlp fallback. stderr: {proc.stderr[-300:]}")
    except Exception as e:
        logger.warning(f"ffmpeg error, trying yt-dlp fallback: {e}")

    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "best[ext=mp4]/best",
            "outtmpl": tmp_path,
            "merge_output_format": "mp4",
            "socket_timeout": 30,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "http_headers": {**HEADERS, "Referer": referer, "Origin": origin},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([direct_url])
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            return tmp_path
        alt_path = tmp_path.rsplit(".", 1)[0] + ".mp4"
        if os.path.exists(alt_path) and os.path.getsize(alt_path) > 0:
            return alt_path
    except Exception as e:
        logger.error(f"yt-dlp fallback also failed: {e}")
    return None


def generate_thumbnail(filepath: str) -> str | None:
    thumb_path = filepath.rsplit(".", 1)[0] + "_thumb.jpg"
    try:
        cmd = [
            "ffmpeg", "-y", "-i", filepath,
            "-ss", "00:00:03",
            "-vframes", "1",
            "-vf", "scale=320:-1",
            "-q:v", "5",
            thumb_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
        cmd[4] = "00:00:00"
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.warning(f"Thumbnail generation failed: {e}")
    return None


def get_video_metadata(filepath: str) -> dict:
    meta = {"width": 0, "height": 0, "duration": 0}
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", filepath,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            import json
            data = json.loads(proc.stdout)
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    meta["width"] = int(s.get("width", 0))
                    meta["height"] = int(s.get("height", 0))
                    dur = s.get("duration")
                    if dur:
                        meta["duration"] = int(float(dur))
                    break
            if not meta["duration"]:
                dur = data.get("format", {}).get("duration")
                if dur:
                    meta["duration"] = int(float(dur))
    except Exception:
        pass
    return meta


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "Unknown"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_resolution(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return "Unknown"
    labels = {2160: "4K", 1440: "2K", 1080: "FHD", 720: "HD", 480: "SD", 360: "SD", 240: "Low"}
    for threshold, label in labels.items():
        if h >= threshold:
            return f"{w}×{h} ({label})"
    return f"{w}×{h}"


def format_size(size_bytes):
    if not size_bytes:
        return "Unknown"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


app = Client(
    "video_bypass_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=tempfile.gettempdir(),
)


@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    welcome = (
        "🎥 **Video Bypass Bot**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 Send me a streaming link and I'll\n"
        "download & send the video directly!\n\n"
        "🌐 **Supported Sites:**\n"
        "├ 🟢 Luluvdo\n"
        "├ 🟢 Vidara\n"
        "├ 🟢 Brainzaps\n"
        "└ 🟢 Streamtape\n\n"
        "📤 **Max Upload:** 2 GB\n"
        "⚡ **Concurrent:** 3 tasks at once\n\n"
        "💬 Just paste a link to start!"
    )
    await message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    help_text = (
        "📚 **How to Use**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "① Copy a video link from any supported site\n"
        "② Paste it here in the chat\n"
        "③ Wait while I process it\n"
        "④ Get the video delivered!\n\n"
        "⚙️ **Commands:**\n"
        "├ /start — Start the bot\n"
        "├ /help — Show this help\n"
        "└ /queue — Check active tasks"
    )
    await message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("queue"))
async def queue_command(client: Client, message: Message):
    active = len(active_tasks)
    queued = len(task_queue)
    if active == 0 and queued == 0:
        text = (
            "📊 **Task Status**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "✨ No active tasks!\n"
            "💬 Send a link to get started."
        )
    else:
        text = (
            f"📊 **Task Status**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚡ Active: **{active}** / {MAX_CONCURRENT}\n"
            f"🕐 Queued: **{queued}**\n\n"
            f"{'💬 You can send more links!' if active < MAX_CONCURRENT else '⏳ Queue is full, please wait.'}"
        )
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def process_queue(client: Client):
    async with queue_lock:
        while task_queue and len(active_tasks) < MAX_CONCURRENT:
            chat_id, msg_id, url, status_msg = task_queue.popleft()
            task = asyncio.create_task(process_video(client, chat_id, msg_id, url, status_msg))
            task_id = msg_id
            active_tasks[task_id] = task
            task.add_done_callback(lambda t, tid=task_id: on_task_done(tid, client))
        if task_queue:
            for i, (cid, mid, u, smsg) in enumerate(task_queue):
                try:
                    site_n = get_site_name(u)
                    await smsg.edit_text(
                        f"🕐 **In Queue — {site_n}**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📍 Position: **#{i + 1}** in queue\n"
                        f"⏳ {len(active_tasks)} tasks running\n\n"
                        f"💬 Your video will start processing soon!",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass


def on_task_done(task_id: int, client: Client):
    active_tasks.pop(task_id, None)
    asyncio.ensure_future(process_queue(client))


@app.on_message(filters.text & filters.private)
async def handle_message(client: Client, message: Message):
    text = message.text.strip()

    if text.startswith("/"):
        return

    url_match = re.search(r"https?://[^\s]+", text)
    if not url_match:
        await message.reply_text(
            "⛔ Please send a valid URL from a supported site."
        )
        return

    url = url_match.group(0)

    if not is_supported_url(url):
        await message.reply_text(
            "⛔ **Unsupported site!**\n\n"
            "🌐 **Supported:**\n"
            "├ Luluvdo\n├ Vidara\n├ Brainzaps\n└ Streamtape",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    site_name = get_site_name(url)

    if len(active_tasks) >= MAX_CONCURRENT:
        status_msg = await message.reply_text(
            f"🕐 **In Queue — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 Position: **#{len(task_queue) + 1}** in queue\n"
            f"⏳ {len(active_tasks)} tasks running\n\n"
            f"💬 Your video will start processing soon!",
            parse_mode=ParseMode.MARKDOWN,
        )
        task_queue.append((message.chat.id, message.id, url, status_msg))
        return

    status_msg = await message.reply_text(
        f"⚡ **Processing — {site_name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 Analyzing page...\n"
        f"⬜ Extracting link\n"
        f"⬜ Downloading video\n"
        f"⬜ Uploading to Telegram",
        parse_mode=ParseMode.MARKDOWN,
    )

    task = asyncio.create_task(process_video(client, message.chat.id, message.id, url, status_msg))
    active_tasks[message.id] = task
    task.add_done_callback(lambda t, tid=message.id: on_task_done(tid, client))


async def process_video(client: Client, chat_id: int, msg_id: int, url: str, status_msg):
    try:
        await _process_video_inner(client, chat_id, msg_id, url, status_msg)
    except Exception as e:
        logger.error(f"Unhandled error processing {url}: {e}")
        try:
            await status_msg.edit_text(
                f"⛔ **Unexpected Error**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💬 {str(e)[:200]}\n\n"
                f"🔁 Please try again.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


async def _process_video_inner(client: Client, chat_id: int, msg_id: int, url: str, status_msg):
    site = detect_site(url)
    site_name = get_site_name(url)
    process_start = time.time()

    try:
        await status_msg.edit_text(
            f"⚡ **Processing — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔄 Analyzing page...\n"
            f"⬜ Extracting link\n"
            f"⬜ Downloading video\n"
            f"⬜ Uploading to Telegram",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    try:
        await client.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception:
        pass

    loop = asyncio.get_event_loop()
    extractor = EXTRACTORS.get(site)
    if not extractor:
        await status_msg.edit_text("⛔ No extractor available for this site.")
        return

    try:
        await status_msg.edit_text(
            f"⚡ **Processing — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Page analyzed\n"
            f"🔄 Extracting direct link...\n"
            f"⬜ Downloading video\n"
            f"⬜ Uploading to Telegram",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    info = await loop.run_in_executor(None, extractor, url)

    if not info.get("direct_url"):
        error_detail = info.get("error", "Could not extract video URL")
        await status_msg.edit_text(
            f"⛔ **Extraction Failed**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 Site: {site_name}\n"
            f"💬 {error_detail}\n\n"
            f"🔁 The video may be removed or the site changed.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    direct_url = info["direct_url"]
    title = info.get("title", "Video") or "Video"

    try:
        await status_msg.edit_text(
            f"⚡ **Processing — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Page analyzed\n"
            f"✅ Direct link extracted\n"
            f"🔄 Downloading video...\n"
            f"⬜ Uploading to Telegram\n\n"
            f"🎞️ __{title}__",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    try:
        await client.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
    except Exception:
        pass

    filepath = await loop.run_in_executor(None, download_video, direct_url, site, url)

    if not filepath or not os.path.exists(filepath):
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌐 Stream", url=url),
                    InlineKeyboardButton("📥 Direct Link", url=direct_url),
                ]
            ]
        )
        await status_msg.edit_text(
            f"⚠️ **Download Failed**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎞️ {title}\n"
            f"💬 Could not download the video.\n\n"
            f"👇 Use the buttons to access manually:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    file_size = os.path.getsize(filepath)
    meta = get_video_metadata(filepath)
    thumb_path = await loop.run_in_executor(None, generate_thumbnail, filepath)

    if file_size > 2 * 1024 * 1024 * 1024:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌐 Stream", url=url),
                    InlineKeyboardButton("📥 Direct Link", url=direct_url),
                ]
            ]
        )
        await status_msg.edit_text(
            f"⚠️ **File Too Large** — {format_size(file_size)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎞️ {title}\n"
            f"📤 Telegram limit is 2 GB.\n\n"
            f"👇 Use the buttons to access manually:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        try:
            os.remove(filepath)
        except Exception:
            pass
        return

    try:
        await status_msg.edit_text(
            f"⚡ **Processing — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Page analyzed\n"
            f"✅ Direct link extracted\n"
            f"✅ Downloaded — {format_size(file_size)}\n"
            f"🔄 Uploading to Telegram...\n\n"
            f"🎞️ __{title}__",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    try:
        await client.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
    except Exception:
        pass

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 Stream", url=url),
                InlineKeyboardButton("📥 Direct Link", url=direct_url),
            ]
        ]
    )

    resolution = format_resolution(meta["width"], meta["height"])
    duration = format_duration(meta["duration"])
    total_time = time.time() - process_start

    caption = (
        f"🎬 **{title}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌐 Source: {site_name}\n"
        f"🖥️ Quality: {resolution}\n"
        f"⏱️ Duration: {duration}\n"
        f"📦 Size: {format_size(file_size)}\n"
        f"⚡ Processed in {total_time:.0f}s"
    )

    try:
        send_kwargs = dict(
            chat_id=chat_id,
            video=filepath,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
            supports_streaming=True,
            width=meta["width"] or None,
            height=meta["height"] or None,
            duration=meta["duration"] or None,
        )
        if thumb_path:
            send_kwargs["thumb"] = thumb_path

        await client.send_video(**send_kwargs)
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Failed to send video: {e}")
        await status_msg.edit_text(
            f"⚠️ **Upload Failed**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎞️ {title}\n"
            f"💬 {str(e)[:200]}\n\n"
            f"👇 Use the buttons to access manually:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    finally:
        for f in [filepath, thumb_path]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass


def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.error("Missing TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, or TELEGRAM_API_HASH!")
        return

    logger.info("Bot starting with Pyrogram (MTProto) - 2GB upload support!")
    app.run()


if __name__ == "__main__":
    main()

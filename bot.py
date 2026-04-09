import os
import re
import time
import uuid
import logging
import tempfile
import subprocess
import asyncio
import requests
import json
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
cancelled_tasks: set[int] = set()


def make_progress_bar(percent: float, length: int = 12) -> str:
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return bar


def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec <= 0:
        return "0 B/s"
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds > 86400:
        return "..."
    if seconds < 60:
        return f"{int(seconds)}s"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}m {s}s"

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

http = requests.Session()
http.headers.update(HEADERS)


def retry_request(func, max_retries=3, delay=1):
    last_error = None
    for attempt in range(max_retries):
        try:
            result = func()
            return result
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(delay)
    raise last_error


def fresh_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def is_supported_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        return any(d in domain for d in SUPPORTED_DOMAINS)
    except Exception:
        return False


SITE_INFO = {
    "luluvdo": {"name": "Luluvdo", "url": "https://luluvdo.com"},
    "vidara": {"name": "Vidara", "url": "https://vidara.so"},
    "brainzaps": {"name": "Brainzaps", "url": "https://brainzaps.tv"},
    "streamtape": {"name": "Streamtape", "url": "https://streamtape.com"},
}

active_chat_tasks: dict[int, set[int]] = {}


def get_site_name(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        for key, info in SITE_INFO.items():
            if key in domain:
                return info["name"]
        return "Unknown"
    except Exception:
        return "Unknown"


def get_site_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        for key, info in SITE_INFO.items():
            if key in domain:
                return info["url"]
        return ""
    except Exception:
        return ""


def clean_title(raw: str, site_words: list[str]) -> str:
    t = raw.strip()
    t = re.sub(r"\s*(at\s+)?\S*\.(com|net|org|to|so|tv|io|cc|me)\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\.(mp4|mkv|avi|mov|webm|flv|wmv|ts)", "", t, flags=re.IGNORECASE)
    for w in site_words:
        t = re.sub(r"(?i)\b" + re.escape(w) + r"\b", "", t)
    t = re.sub(r"\s*[-–|:]\s*$", "", t.strip())
    t = re.sub(r"^\s*[-–|:]\s*", "", t.strip())
    t = re.sub(r"\s*[-–|:]\s*$", "", t.strip())
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[_]+", " ", t)
    return t.strip() if t.strip() else ""


def extract_streamtape(url: str) -> dict:
    result = {"direct_url": None, "title": "Streamtape Video", "error": None}
    url = url.replace("/e/", "/v/")

    for attempt in range(3):
        try:
            s = fresh_session()
            resp = s.get(url, timeout=15)
            resp.raise_for_status()
            html = resp.text

            for pattern in [
                r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r'<meta\s+name="title"\s+content="([^"]+)"',
                r"<title>([^<]+)</title>",
            ]:
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    t = clean_title(m.group(1), ["streamtape", "video not found"])
                    if t and len(t) > 2:
                        result["title"] = t
                        break

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
                return result
            else:
                result["error"] = "Could not find video token in page"
                if attempt < 2:
                    logger.warning(f"Streamtape extract failed (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
                    continue
        except Exception as e:
            result["error"] = str(e)
            if attempt < 2:
                logger.warning(f"Streamtape error (attempt {attempt + 1}): {e}")
                time.sleep(1)
                continue
    return result


def get_m3u8_duration(master_url: str, referer: str = "") -> float:
    try:
        s = fresh_session()
        hdrs = {"Referer": referer} if referer else {}
        r = s.get(master_url, headers=hdrs, timeout=10)
        if r.status_code != 200:
            return 0
        base_url = master_url.rsplit("/", 1)[0] + "/"
        index_url = None
        for line in r.text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("http"):
                index_url = line
            else:
                index_url = base_url + line
            break

        def parse_extinf(text):
            total = 0
            for l in text.split("\n"):
                if l.startswith("#EXTINF:"):
                    try:
                        total += float(l.split(":")[1].split(",")[0])
                    except Exception:
                        pass
            return total

        if not index_url:
            return parse_extinf(r.text)

        r2 = s.get(index_url, headers=hdrs, timeout=10)
        if r2.status_code != 200:
            return 0
        return parse_extinf(r2.text)
    except Exception:
        return 0


def extract_luluvdo(url: str) -> dict:
    result = {"direct_url": None, "title": "Luluvdo Video", "error": None}
    video_id = url.rstrip("/").split("/")[-1]
    embed_url = f"https://luluvdo.com/e/{video_id}"

    for attempt in range(3):
        try:
            s = fresh_session()
            resp = s.get(
                embed_url,
                headers={"Referer": "https://luluvdo.com/"},
                timeout=15,
            )
            resp.raise_for_status()
            html = resp.text

            for pattern in [
                r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r"<title>([^<]+)</title>",
            ]:
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    t = clean_title(m.group(1), ["lulustream", "luluvdo", ".mp4", ".mkv"])
                    if t and len(t) > 2:
                        result["title"] = t
                        break

            m3u8 = re.findall(r"(https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*)", html)
            if m3u8:
                result["direct_url"] = m3u8[0]
                result["hls_duration"] = get_m3u8_duration(m3u8[0], "https://luluvdo.com/")
                return result
            else:
                result["error"] = "No m3u8 URL found in embed page"
                if attempt < 2:
                    logger.warning(f"Luluvdo extract failed (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
                    continue
        except Exception as e:
            result["error"] = str(e)
            if attempt < 2:
                logger.warning(f"Luluvdo error (attempt {attempt + 1}): {e}")
                time.sleep(1)
                continue
    return result


def extract_brainzaps(url: str) -> dict:
    result = {"direct_url": None, "title": "Brainzaps Video", "error": None}

    for attempt in range(3):
        try:
            s = fresh_session()
            resp = s.get(url, timeout=15)
            resp.raise_for_status()
            html = resp.text

            for pattern in [
                r'<meta\s+property="og:title"\s+content="([^"]+)"',
                r"<h[1-6][^>]*>([^<]{3,})</h[1-6]>",
                r"<title>([^<]+)</title>",
            ]:
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    t = clean_title(m.group(1), ["brainzaps", "brainzaps.tv", "watch ", " online"])
                    if t and len(t) > 2:
                        result["title"] = t
                        break

            eval_match = re.search(
                r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',(\d+),(\d+),'([^']*)'",
                html,
                re.DOTALL,
            )
            if not eval_match:
                result["error"] = "No packed JS found"
                if attempt < 2:
                    logger.warning(f"Brainzaps extract failed (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
                    continue
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
                    pat = r"\b" + base_convert(c, a) + r"\b"
                    decoded = re.sub(pat, keywords[c], decoded)

            m3u8 = re.findall(r"(https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*)", decoded)
            if m3u8:
                result["direct_url"] = m3u8[0]
                result["hls_duration"] = get_m3u8_duration(m3u8[0], url)
                return result
            else:
                result["error"] = "No m3u8 URL found in decoded JS"
                if attempt < 2:
                    logger.warning(f"Brainzaps m3u8 not found (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
                    continue
        except Exception as e:
            result["error"] = str(e)
            if attempt < 2:
                logger.warning(f"Brainzaps error (attempt {attempt + 1}): {e}")
                time.sleep(1)
                continue
    return result


def extract_vidara(url: str) -> dict:
    result = {"direct_url": None, "title": "Vidara Video", "error": None}
    video_id = url.rstrip("/").split("/")[-1]

    for attempt in range(3):
        try:
            s = fresh_session()
            resp = s.post(
                "https://vidara.so/api/stream",
                headers={
                    "Content-Type": "application/json",
                    "Referer": f"https://vidara.so/e/{video_id}",
                    "Origin": "https://vidara.so",
                },
                json={"filecode": video_id, "device": "web"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                stream_url = data.get("streaming_url")
                if stream_url:
                    result["direct_url"] = stream_url
                    raw_title = data.get("title", "")
                    if raw_title:
                        t = clean_title(raw_title, ["vidara", ".mp4", ".mkv"])
                        result["title"] = t if t else "Vidara Video"
                    if ".m3u8" in stream_url:
                        result["hls_duration"] = get_m3u8_duration(stream_url, f"https://vidara.so/e/{video_id}")
                    return result
                else:
                    result["error"] = "No streaming URL in API response"
                    if attempt < 2:
                        logger.warning(f"Vidara no stream URL (attempt {attempt + 1}), retrying...")
                        time.sleep(1)
                        continue
            else:
                result["error"] = f"API returned status {resp.status_code}"
                if attempt < 2:
                    logger.warning(f"Vidara API status {resp.status_code} (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
                    continue
        except Exception as e:
            result["error"] = str(e)
            if attempt < 2:
                logger.warning(f"Vidara error (attempt {attempt + 1}): {e}")
                time.sleep(1)
                continue
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


def graceful_kill(proc, timeout=10):
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()



def download_video(direct_url: str, site: str, original_url: str = "", progress: dict | None = None, task_id: int = 0, hls_duration: float = 0) -> str | None:
    tmp_path = os.path.join(tempfile.gettempdir(), f"tgbot_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4")
    FFMPEG_TIMEOUT = 600

    def update_progress(downloaded, total, start_time):
        if progress is None:
            return
        elapsed = time.time() - start_time
        speed = downloaded / elapsed if elapsed > 0 else 0
        eta = (total - downloaded) / speed if speed > 0 and total > 0 else 0
        percent = (downloaded / total * 100) if total > 0 else 0
        progress.update({
            "downloaded": downloaded,
            "total": total,
            "speed": speed,
            "eta": eta,
            "percent": percent,
            "updated": time.time(),
        })

    def is_cancelled():
        return task_id in cancelled_tasks

    def cleanup(path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    MIN_VIDEO_SIZE = 100000

    def check_result(path):
        if os.path.exists(path) and os.path.getsize(path) > MIN_VIDEO_SIZE:
            logger.info(f"Download success: {os.path.getsize(path)} bytes at {path}")
            return path
        alt = path.rsplit(".", 1)[0] + ".mp4"
        if os.path.exists(alt) and os.path.getsize(alt) > MIN_VIDEO_SIZE:
            logger.info(f"Download success (alt path): {os.path.getsize(alt)} bytes")
            return alt
        return None

    referer_map = {
        "luluvdo": "https://luluvdo.com/",
        "brainzaps": "https://brainzaps.tv/",
        "vidara": "https://vidara.so/",
        "streamtape": "https://streamtape.com/",
    }
    origin_map = {
        "luluvdo": "https://luluvdo.com",
        "brainzaps": "https://brainzaps.tv",
        "vidara": "https://vidara.so",
        "streamtape": "https://streamtape.com",
    }
    referer = referer_map.get(site, "")
    origin = origin_map.get(site, "")

    if site == "streamtape":
        logger.info(f"[{site}] HTTP stream download: {direct_url[:80]}")
        try:
            s = fresh_session()
            resp = s.get(
                direct_url,
                headers={"Referer": referer},
                stream=True,
                timeout=120,
                allow_redirects=True,
            )
            resp.raise_for_status()
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            dl_start = time.time()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1048576):
                    if is_cancelled():
                        logger.info(f"Download cancelled for task {task_id}")
                        cleanup(tmp_path)
                        return None
                    f.write(chunk)
                    downloaded += len(chunk)
                    update_progress(downloaded, total_size, dl_start)
            result = check_result(tmp_path)
            if result:
                return result
        except Exception as e:
            logger.error(f"Streamtape download error: {e}")
        cleanup(tmp_path)
        return None

    is_m3u8 = ".m3u8" in direct_url

    if is_m3u8 or site in ("luluvdo", "brainzaps", "vidara"):
        logger.info(f"[{site}] ffmpeg download (m3u8={is_m3u8}): {direct_url[:100]}")
        headers_str = f"User-Agent: {HEADERS['User-Agent']}\r\n"
        if referer:
            headers_str += f"Referer: {referer}\r\n"
        if origin:
            headers_str += f"Origin: {origin}\r\n"

        if progress is not None:
            progress.update({"downloaded": 0, "total": 0, "speed": 0, "eta": 0, "percent": 0, "ffmpeg": True, "updated": time.time()})

        cmd = [
            "ffmpeg", "-y",
            "-headers", headers_str,
            "-i", direct_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            tmp_path,
        ]
        stderr_log = tmp_path + ".stderr"
        stderr_f = None
        try:
            stderr_f = open(stderr_log, "w")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_f)
            ffmpeg_start = time.time()
            last_size = 0
            stall_start = None
            STALL_LIMIT = 90
            total_duration = hls_duration
            locked_est_total = 0
            ffmpeg_time_re = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")

            def parse_ffmpeg_time():
                try:
                    stderr_f.flush()
                    with open(stderr_log, "r") as sf:
                        text = sf.read()
                    cur_time = 0
                    for tm in ffmpeg_time_re.finditer(text):
                        t = int(tm.group(1)) * 3600 + int(tm.group(2)) * 60 + int(tm.group(3)) + int(tm.group(4)) / 100
                        if t > cur_time:
                            cur_time = t
                    return cur_time
                except Exception:
                    return 0

            while proc.poll() is None:
                if is_cancelled():
                    graceful_kill(proc)
                    cleanup(tmp_path)
                    return None
                elapsed = time.time() - ffmpeg_start
                if elapsed > FFMPEG_TIMEOUT:
                    logger.warning(f"[{site}] ffmpeg timeout after {FFMPEG_TIMEOUT}s, graceful stop")
                    graceful_kill(proc, timeout=30)
                    break
                cur_size = 0
                if os.path.exists(tmp_path):
                    cur_size = os.path.getsize(tmp_path)
                    speed = cur_size / elapsed if elapsed > 0 else 0
                    cur_time = parse_ffmpeg_time()
                    est_total = 0
                    pct = 0
                    eta = 0
                    if total_duration > 0 and cur_time > 0:
                        pct = min(cur_time / total_duration * 100, 99.9)
                        raw_est = int(cur_size / (cur_time / total_duration))
                        if pct >= 25 and not locked_est_total:
                            locked_est_total = raw_est
                        est_total = locked_est_total if locked_est_total else raw_est
                        remaining_time = (total_duration - cur_time) / (cur_time / elapsed) if cur_time > 0 else 0
                        eta = max(0, remaining_time)
                    if progress is not None:
                        progress.update({
                            "downloaded": cur_size,
                            "total": est_total,
                            "speed": speed,
                            "percent": pct,
                            "eta": eta,
                            "ffmpeg": True,
                            "updated": time.time(),
                        })
                if cur_size == last_size and cur_size > 0:
                    if stall_start is None:
                        stall_start = time.time()
                    elif time.time() - stall_start > STALL_LIMIT:
                        logger.warning(f"[{site}] ffmpeg stalled for {STALL_LIMIT}s (size={cur_size}), graceful stop")
                        graceful_kill(proc, timeout=30)
                        break
                else:
                    stall_start = None
                    last_size = cur_size
                time.sleep(2)

            result = check_result(tmp_path)
            if result:
                return result
            logger.warning(f"[{site}] ffmpeg finished but file invalid, rc={proc.returncode}")
        except Exception as e:
            logger.warning(f"[{site}] ffmpeg error: {e}")
        finally:
            if stderr_f:
                try: stderr_f.close()
                except: pass
            try: os.remove(stderr_log)
            except: pass

        cleanup(tmp_path)
    logger.error(f"[{site}] All download methods failed for: {direct_url[:80]}")
    return None


def generate_thumbnail(filepath: str) -> str | None:
    thumb_path = filepath.rsplit(".", 1)[0] + "_thumb.jpg"
    for ss in ["00:00:03", "00:00:01", "00:00:00"]:
        try:
            cmd = [
                "ffmpeg", "-y",
                "-ss", ss,
                "-i", filepath,
                "-vframes", "1",
                "-vf", "scale=320:-1",
                "-q:v", "5",
                thumb_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                logger.info(f"Thumbnail generated at ss={ss}, size={os.path.getsize(thumb_path)}")
                return thumb_path
            logger.warning(f"Thumbnail at ss={ss} failed: rc={proc.returncode}, stderr={proc.stderr[-200:]}")
        except Exception as e:
            logger.warning(f"Thumbnail at ss={ss} error: {e}")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", filepath,
            "-vf", "thumbnail,scale=320:-1",
            "-frames:v", "1",
            "-q:v", "5",
            thumb_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            logger.info(f"Thumbnail generated via thumbnail filter, size={os.path.getsize(thumb_path)}")
            return thumb_path
        logger.warning(f"Thumbnail filter failed: rc={proc.returncode}")
    except Exception as e:
        logger.warning(f"Thumbnail filter error: {e}")
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
        "├ /queue — Check active tasks\n"
        "└ /cancel — Cancel all tasks"
    )
    await message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    chat_id = message.chat.id
    chat_task_ids = active_chat_tasks.get(chat_id, set())
    if chat_task_ids:
        count = len(chat_task_ids)
        for tid in list(chat_task_ids):
            cancelled_tasks.add(tid)
        new_queue = deque()
        removed = 0
        for item in task_queue:
            if item[0] == chat_id:
                removed += 1
            else:
                new_queue.append(item)
        task_queue.clear()
        task_queue.extend(new_queue)
        await message.reply_text(
            f"🛑 **Cancelling {count} active task(s)...**\n"
            f"{'🗑️ Removed ' + str(removed) + ' queued task(s).' if removed else ''}\n\n"
            f"⏳ Downloads will stop shortly.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await message.reply_text(
            "✨ No active tasks to cancel!",
            parse_mode=ParseMode.MARKDOWN,
        )


@app.on_callback_query(filters.regex(r"^cancel_(\d+)$"))
async def cancel_callback(client: Client, callback_query):
    task_id = int(callback_query.data.split("_")[1])
    cancelled_tasks.add(task_id)
    await callback_query.answer("🛑 Cancelling task...", show_alert=True)


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
            active_chat_tasks.setdefault(chat_id, set()).add(task_id)
            task.add_done_callback(lambda t, tid=task_id, cid=chat_id: on_task_done(tid, cid, client))
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


def on_task_done(task_id: int, chat_id: int, client: Client):
    active_tasks.pop(task_id, None)
    cancelled_tasks.discard(task_id)
    if chat_id in active_chat_tasks:
        active_chat_tasks[chat_id].discard(task_id)
        if not active_chat_tasks[chat_id]:
            del active_chat_tasks[chat_id]
    asyncio.ensure_future(process_queue(client))


async def enqueue_url(client: Client, chat_id: int, task_id: int, url: str):
    site_name = get_site_name(url)

    if len(active_tasks) >= MAX_CONCURRENT:
        status_msg = await client.send_message(
            chat_id,
            f"🕐 **In Queue — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 Position: **#{len(task_queue) + 1}** in queue\n"
            f"⏳ {len(active_tasks)} tasks running\n\n"
            f"💬 Your video will start processing soon!",
            parse_mode=ParseMode.MARKDOWN,
        )
        task_queue.append((chat_id, task_id, url, status_msg))
        return

    status_msg = await client.send_message(
        chat_id,
        f"⚡ **Processing — {site_name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔄 Analyzing page...\n"
        f"⬜ Extracting link\n"
        f"⬜ Downloading video\n"
        f"⬜ Uploading to Telegram",
        parse_mode=ParseMode.MARKDOWN,
    )

    task = asyncio.create_task(process_video(client, chat_id, task_id, url, status_msg))
    active_tasks[task_id] = task
    active_chat_tasks.setdefault(chat_id, set()).add(task_id)
    task.add_done_callback(lambda t, tid=task_id, cid=chat_id: on_task_done(tid, cid, client))


@app.on_message(filters.text & filters.private)
async def handle_message(client: Client, message: Message):
    text = message.text.strip()

    if text.startswith("/"):
        return

    text = re.sub(r"(https?://)", r" \1", text).strip()
    all_urls = re.findall(r"https?://[^\s]+", text)
    if not all_urls:
        await message.reply_text(
            "⛔ Please send a valid URL from a supported site."
        )
        return

    supported = [u for u in all_urls if is_supported_url(u)]
    unsupported = [u for u in all_urls if not is_supported_url(u)]

    if not supported:
        await message.reply_text(
            "⛔ **Unsupported site!**\n\n"
            "🌐 **Supported:**\n"
            "├ Luluvdo\n├ Vidara\n├ Brainzaps\n└ Streamtape",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if len(supported) > 1:
        sites = ", ".join(get_site_name(u) for u in supported)
        msg = (
            f"📋 **{len(supported)} links detected**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 Sites: {sites}\n"
            f"⚡ Processing all of them..."
        )
        if unsupported:
            msg += f"\n⚠️ {len(unsupported)} unsupported link(s) skipped."
        await message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    for i, url in enumerate(supported):
        task_id = message.id * 1000 + i
        await enqueue_url(client, message.chat.id, task_id, url)


async def process_video(client: Client, chat_id: int, msg_id: int, url: str, status_msg):
    try:
        await _process_video_inner(client, chat_id, msg_id, url, status_msg)
    except asyncio.CancelledError:
        cancelled_tasks.add(msg_id)
        try:
            await status_msg.edit_text(
                "🛑 **Cancelled**\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "💬 Task was cancelled.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Unhandled error processing {url}: {e}", exc_info=True)
        if msg_id in cancelled_tasks:
            try:
                await status_msg.edit_text(
                    "🛑 **Cancelled**\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "💬 Task was cancelled.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        else:
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

    cancel_btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_{msg_id}")]]
    )

    if msg_id in cancelled_tasks:
        cancelled_tasks.discard(msg_id)
        await status_msg.edit_text("🛑 **Cancelled**", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        await status_msg.edit_text(
            f"⚡ **Processing — {site_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔄 Analyzing page...\n"
            f"⬜ Extracting link\n"
            f"⬜ Downloading video\n"
            f"⬜ Uploading to Telegram",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_btn,
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
            reply_markup=cancel_btn,
        )
    except Exception:
        pass

    if msg_id in cancelled_tasks:
        cancelled_tasks.discard(msg_id)
        await status_msg.edit_text("🛑 **Cancelled**", parse_mode=ParseMode.MARKDOWN)
        return

    extract_start = time.time()
    logger.info(f"Starting extraction for {site_name}: {url}")
    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, extractor, url),
            timeout=60,
        )
    except asyncio.TimeoutError:
        logger.error(f"Extraction timed out after 60s for {url}")
        await status_msg.edit_text(
            f"⛔ **Extraction Timeout**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 Site: {site_name}\n"
            f"💬 The site took too long to respond.\n\n"
            f"🔁 Please try again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    logger.info(f"Extraction done for {site_name} in {time.time() - extract_start:.1f}s — success={bool(info.get('direct_url'))}")

    if msg_id in cancelled_tasks:
        cancelled_tasks.discard(msg_id)
        await status_msg.edit_text("🛑 **Cancelled**", parse_mode=ParseMode.MARKDOWN)
        return

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

    dl_progress = {}

    async def update_dl_status():
        last_update = 0
        while True:
            await asyncio.sleep(3)
            if msg_id in cancelled_tasks:
                return
            if not dl_progress or time.time() - dl_progress.get("updated", 0) > 10:
                continue
            if time.time() - last_update < 3:
                continue
            try:
                dl = dl_progress.get("downloaded", 0)
                total = dl_progress.get("total", 0)
                speed = dl_progress.get("speed", 0)
                eta = dl_progress.get("eta", 0)
                percent = dl_progress.get("percent", 0)
                is_ffmpeg = dl_progress.get("ffmpeg", False)

                if is_ffmpeg:
                    if total > 0 and percent > 0:
                        bar = make_progress_bar(percent)
                        bar_line = f"`[{bar}]` {percent:.1f}%"
                        size_line = f"📥 {format_size(dl)} / ~{format_size(total)}"
                    else:
                        bar_line = ""
                        size_line = f"📥 {format_size(dl)}"
                    speed_line = f"🚀 {format_speed(speed)}" if speed > 0 else ""
                    eta_line = f"⏳ ETA: {format_eta(eta)}" if eta > 0 else ""
                    lines = [
                        f"⚡ **Downloading — {site_name}**",
                        "━━━━━━━━━━━━━━━━━━━━━",
                        "",
                        "✅ Page analyzed",
                        "✅ Direct link extracted",
                        bar_line,
                        size_line,
                        speed_line,
                        eta_line,
                        "",
                        f"🎞️ __{title}__",
                    ]
                else:
                    bar = make_progress_bar(percent)
                    bar_line = f"`[{bar}]` {percent:.1f}%" if total > 0 else ""
                    size_line = f"📥 {format_size(dl)}" + (f" / {format_size(total)}" if total > 0 else "")
                    speed_line = f"🚀 {format_speed(speed)}" if speed > 0 else ""
                    eta_line = f"⏳ ETA: {format_eta(eta)}" if eta > 0 else ""
                    lines = [
                        f"⚡ **Downloading — {site_name}**",
                        "━━━━━━━━━━━━━━━━━━━━━",
                        "",
                        "✅ Page analyzed",
                        "✅ Direct link extracted",
                        bar_line,
                        size_line,
                        speed_line,
                        eta_line,
                        "",
                        f"🎞️ __{title}__",
                    ]
                text = "\n".join(l for l in lines if l is not None and l != "")
                await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_btn)
                last_update = time.time()
            except Exception:
                pass

    progress_task = asyncio.create_task(update_dl_status())

    try:
        await client.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
    except Exception:
        pass

    dl_start = time.time()
    logger.info(f"Starting download for {title} ({site_name})")
    try:
        filepath = await asyncio.wait_for(
            loop.run_in_executor(None, download_video, direct_url, site, url, dl_progress, msg_id, info.get("hls_duration", 0)),
            timeout=600,
        )
    except asyncio.TimeoutError:
        filepath = None
        logger.error(f"Download timed out after 600s for {url}")
    logger.info(f"Download finished in {time.time() - dl_start:.1f}s — file={'yes' if filepath else 'no'}")

    progress_task.cancel()

    if msg_id in cancelled_tasks:
        cancelled_tasks.discard(msg_id)
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        await status_msg.edit_text(
            "🛑 **Cancelled**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💬 Task was cancelled by user.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

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

    upload_last_edit = {"time": 0}

    async def upload_progress(current, total):
        now = time.time()
        if now - upload_last_edit["time"] < 3:
            return
        upload_last_edit["time"] = now
        try:
            percent = current / total * 100 if total > 0 else 0
            bar = make_progress_bar(percent)
            speed_val = 0
            if upload_last_edit.get("prev_current") is not None and upload_last_edit.get("prev_time"):
                dt = now - upload_last_edit["prev_time"]
                if dt > 0:
                    speed_val = (current - upload_last_edit["prev_current"]) / dt
            upload_last_edit["prev_current"] = current
            upload_last_edit["prev_time"] = now
            speed_line = f"🚀 {format_speed(speed_val)}" if speed_val > 0 else ""

            lines = [
                f"⚡ **Uploading — {site_name}**",
                "━━━━━━━━━━━━━━━━━━━━━",
                "",
                "✅ Page analyzed",
                "✅ Direct link extracted",
                "✅ Downloaded",
                f"`[{bar}]` {percent:.1f}%",
                f"📤 {format_size(current)} / {format_size(total)}",
                speed_line,
                "",
                f"🎞️ __{title}__",
            ]
            await status_msg.edit_text(
                "\n".join(l for l in lines if l),
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
    site_home = get_site_url(url)

    source_line = f"🌐 Source: [{site_name}]({site_home})" if site_home else f"🌐 Source: {site_name}"

    caption = (
        f"🎬 **{title}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{source_line}\n"
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
            progress=upload_progress,
        )
        if meta["width"] and meta["height"]:
            send_kwargs["width"] = meta["width"]
            send_kwargs["height"] = meta["height"]
        if meta["duration"]:
            send_kwargs["duration"] = meta["duration"]
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


async def cleanup_temp_files():
    while True:
        await asyncio.sleep(600)
        try:
            tmp_dir = tempfile.gettempdir()
            now = time.time()
            for f in os.listdir(tmp_dir):
                if f.startswith("tgbot_") and (f.endswith(".mp4") or f.endswith(".jpg") or f.endswith(".ts") or f.endswith(".stderr")):
                    fp = os.path.join(tmp_dir, f)
                    if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > 600:
                        os.remove(fp)
                        logger.info(f"Cleaned up temp file: {f}")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")


def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.error("Missing TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, or TELEGRAM_API_HASH!")
        return

    logger.info("Bot starting with Pyrogram (MTProto) - 2GB upload support!")

    async def run():
        async with app:
            logger.info("Bot connected! Starting cleanup task.")
            asyncio.ensure_future(cleanup_temp_files())
            from pyrogram import idle
            await idle()

    app.run(run())


if __name__ == "__main__":
    main()

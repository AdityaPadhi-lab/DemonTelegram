import sys
sys.setrecursionlimit(10000)
import re
import os
os.environ["PYROGRAM_NO_UPDATE_CHECK"] = "1"
os.environ["PYROGRAM_RAW_NO_SCAN"] = "1"

import mimetypes
mimetypes.init()   # normal initialization (SAFE)

import os
import sys
import shutil
import asyncio
import logging
from time import time
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
sys.dont_write_bytecode = True

from dotenv import load_dotenv
load_dotenv()

import psutil
import aiofiles  # noqa: F401 (kept for helpers that may use it)
import speedtest

from pyrogram.enums import ParseMode, ChatType
from pyrogram import Client, filters
filters.topic = filters.create(lambda _, __, msg: getattr(msg, "is_topic_message", False))
from pyrogram.errors import PeerIdInvalid, BadRequest, FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import PyroConf
from helpers.utils import processMediaGroup, progressArgs, send_media  # noqa: F401
from helpers.files import (
    get_download_path,  # noqa: F401
    fileSizeLimit,
    get_readable_file_size,
    get_readable_time,
    cleanup_download,
)
from helpers.msg import (
    getChatMsgID,
    get_file_name,
    get_parsed_msg,
)
from logger import LOGGER

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Force stdout/stderr encoding to UTF-8 (Python 3.7+)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# ---------------- Configuration ----------------
MAX_CONCURRENT_DOWNLOADS = 1
RETRY_LIMIT = 5
TEMP_DIR = Path("downloads")
TEMP_DIR.mkdir(exist_ok=True)
CHUNK_SIZE = 256 * 1024 * 1024  # 256 MiB chunk size for local reads (if used elsewhere)
MIN_FREE_SPACE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB
BOT_START_TIME = time()

# Per-chat settings (simple in-memory)
# mode: "all" -> respond to any message containing a t.me link
#       "mention" -> respond only when bot is mentioned or command is used
CHAT_SETTINGS = {}  # chat_id -> {"mode": "all"|"mention"}

# ---------------- Clients ----------------
bot = Client(
    "media_bot_pro",
    api_id=PyroConf.API_ID,
    api_hash=PyroConf.API_HASH,
    bot_token=PyroConf.BOT_TOKEN,
    workers=1000,
    parse_mode=ParseMode.MARKDOWN,
)

user = Client(
    "user_session",
    workers=1000,
    session_string=PyroConf.SESSION_STRING,
)

# ---------------- Globals ----------------
RUNNING_TASKS = set()
DOWNLOAD_SEM = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
PROGRESS_UPDATE_TIMES = {}

USER_LOCK = asyncio.Lock()  # single-call lock for user client


# ---------------- Helpers ----------------
def track_task(coro):
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    task.add_done_callback(lambda _: RUNNING_TASKS.discard(task))
    return task


async def safe_user_call(func, *args, **kwargs):
    async with USER_LOCK:
        return await func(*args, **kwargs)


async def check_disk_space(required_bytes: int = 0) -> bool:
    total, used, free = shutil.disk_usage(".")
    return (free - required_bytes) >= MIN_FREE_SPACE_BYTES


def readable_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emoji_filled(text: str) -> str:
    return f"🧿\n{text}\n🧿"


def create_small_bar(value, total=100, length=10, filled_sym="■", empty_sym="□"):
    try:
        filled = int(max(0, min(1.0, value / total)) * length)
    except Exception:
        filled = 0
    return f"{filled_sym * filled}{empty_sym * (length - filled)}"


async def racing_progress_bar(current, total, progress_message: Message, start_time, filename="Unknown"):
    if total == 0 or not progress_message:
        return

    now = time()
    msg_id = progress_message.id
    last_update = PROGRESS_UPDATE_TIMES.get(msg_id, 0)

    # Update every ~1s (or final)
    if (now - last_update < 1) and (current != total):
        return

    PROGRESS_UPDATE_TIMES[msg_id] = now

    percent = current * 100 / total
    speed = current / max(0.1, (now - start_time))
    eta = (total - current) / max(1.0, speed)
    eta_str = get_readable_time(eta)
    speed_str = f"{get_readable_file_size(speed)}/s"
    downloaded_mb = get_readable_file_size(current)
    file_size = get_readable_file_size(total)

    filled = int(percent / 5)
    bar = "☀️" * filled + "☁️" * (20 - filled)

    text = (
        "🧿" * 15 + "\n"
        f"🛜 Downloading: {filename}\n"
        f"{bar} {percent:.2f}%\n"
        f"⏬ {downloaded_mb}/{file_size}\n"
        f"📶 {speed_str} | ⏳ {eta_str}\n"
        + "🧿" * 15
    )

    try:
        await progress_message.edit(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except:
    pass

async def safe_download(chat_message: Message, download_path: str, progress_message: Message = None):
    last_exc = None
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            # size detection
            file_size = None
            if chat_message.document:
                file_size = chat_message.document.file_size
            elif chat_message.video:
                file_size = chat_message.video.file_size
            elif chat_message.audio:
                file_size = chat_message.audio.file_size
            elif chat_message.photo:
                file_size = getattr(chat_message.photo, "file_size", None)

            if file_size and not await check_disk_space(file_size):
                if progress_message:
                    await progress_message.edit("⚠️ Low disk space — aborting download.")
                raise RuntimeError("Insufficient disk space for download")

            start_time = time()
            if progress_message:
                PROGRESS_UPDATE_TIMES.pop(progress_message.id, None)

            media_path = await chat_message.download(
                file_name=download_path,
                progress=racing_progress_bar,
                progress_args=(progress_message, start_time, get_file_name(chat_message.id, chat_message))
            )

            if progress_message:
                PROGRESS_UPDATE_TIMES.pop(progress_message.id, None)

            return media_path

        except FloodWait as e:
            last_exc = e
            LOGGER(__name__).warning(f"Download FloodWait: waiting for {e.value} seconds.")
            if progress_message:
                try:
                    await progress_message.edit(f"⏳ FloodWait: Pausing for {e.value}s... ⏳")
                except Exception:
                    pass
            await asyncio.sleep(e.value + 1)

        except Exception as e:
            last_exc = e
            LOGGER(__name__).error(f"Download attempt {attempt} failed: {e}")
            if progress_message:
                try:
                    await progress_message.edit(f"⚠️ Attempt {attempt} failed — retrying... 💫")
                except Exception:
                    pass
            await asyncio.sleep(2 * attempt)

    if progress_message:
        PROGRESS_UPDATE_TIMES.pop(progress_message.id, None)
    raise last_exc


# ----------- Upload counter ----------
def get_upload_number():
    try:
        with open("upload_count.txt", "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 1


def save_upload_number(n):
    try:
        with open("upload_count.txt", "w", encoding="utf-8") as f:
            f.write(str(n))
    except Exception:
        pass


async def stream_send(bot_client: Client, chat_id: int, file_path: str, progress_message: Message = None):
    """
    Upload a file with a proper filename ending in '@AstroCollapse.ext'
    Includes caption, AI-generated description, progress bar, and fallback.
    """

    count = get_upload_number()
    file = Path(file_path)
    ext = file.suffix.lower()
    base_name = file.stem
    new_filename = f"{base_name} @AstroCollapse{ext}"

    # ✅ Track file in download history
    await add_to_history(new_filename)

    # ✅ Rename temporarily for upload
    renamed_path = file.parent / new_filename
    try:
        os.rename(file_path, renamed_path)
    except Exception as e:
        LOGGER(__name__).warning(f"Rename failed, using original: {e}")
        renamed_path = file_path

    # ✅ Final caption (stylish, bold & well-structured)
    import re

    # Extract clean name
    clean_name = Path(new_filename).stem.replace("@AstroCollapse", "")
    clean_name = re.sub(r"^\d+\)*\s*", "", clean_name).strip()
    clean_name = clean_name.replace("_", " ").replace("-", " ").strip()

    # Extract batch/course name (first meaningful word(s))
    # Example: "DSA Recursion" → "DSA Recursion"
    course_name = clean_name.split()[0]
    if len(clean_name.split()) > 1:
        course_name = " ".join(clean_name.split()[:2])  # first 2 words = course name

    # FINAL BOLD CAPTION
    full_caption = (
        f"**Name » {clean_name}**\n\n"
        f"**Batch / Course » {course_name}**\n\n"
        f"**Extracted by » @AstroCollapse**"
    )




    
    start_time = time()

    # Progress updater
    async def upload_progress(current, total, progress_message):
        if total == 0 or not progress_message:
            return
        now = time()
        msg_id = progress_message.id
        last_update = PROGRESS_UPDATE_TIMES.get(msg_id, 0)
        if (now - last_update < 1) and (current != total):
            return
        PROGRESS_UPDATE_TIMES[msg_id] = now

        percent = current * 100 / total
        speed = current / max(0.1, (now - start_time))
        eta = (total - current) / max(1.0, speed)
        eta_str = get_readable_time(eta)
        speed_str = f"{get_readable_file_size(speed)}/s"
        uploaded_mb = get_readable_file_size(current)
        file_size = get_readable_file_size(total)
        filled = int(percent / 5)
        bar = "🐬" * filled + "🐾" * (20 - filled)

        text = (
            "🧿" * 14 + "\n"
            f"🛜 Uploading: {new_filename}\n"
            f"{bar} {percent:.2f}%\n"
            f"♨️ {uploaded_mb}/{file_size}\n"
            f"📶 {speed_str} | ⏳ {eta_str}\n"
            + "🧿" * 14
        )
        try:
            await progress_message.edit(text)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            pass

    # ✅ Try to upload file as per type
    try:
        # ---------- VIDEO ----------
        if ext in [".mp4", ".mov", ".mkv", ".avi"]:
            thumb_path = "RestrictedContentDL/thumb0.jpg"
            if not os.path.exists(thumb_path):
                thumb_path = None

            await bot_client.send_video(
                chat_id=chat_id,
                video=str(renamed_path),
                caption=full_caption,
                thumb=thumb_path,
                supports_streaming=True,
                progress=upload_progress,
                progress_args=(progress_message,),
            )
        
        # ---------- MKV → Convert to MP4 then upload ----------
        elif ext == ".mkv":
            try:
                await progress_message.edit("🔄 Converting MKV to MP4...")
            except:
                pass

            mp4_path = str(Path(renamed_path).with_suffix(".mp4"))

            # Convert MKV → MP4 (libx264 + AAC) using moviepy if available, otherwise ffmpeg fallback
            try:
                convert_mkv_to_mp4(str(renamed_path), mp4_path)
            except Exception as e:
                LOGGER(__name__).warning(f"MKV conversion failed: {e}")
                # fallback: try sending as document (no streaming)
                try:
                    await bot_client.send_document(
                        chat_id=chat_id,
                        document=str(renamed_path),
                        caption=full_caption + "\n⚠️ Sent as file (conversion failed)"
                    )
                except Exception as e2:
                    LOGGER(__name__).error(f"Fallback send_document after conversion failure failed: {e2}")
                    if progress_message:
                        await progress_message.edit(f"❌ Upload failed: {e2}")
                # ensure partial converted file removed if present
                try:
                    if os.path.exists(mp4_path):
                        os.remove(mp4_path)
                except Exception:
                    pass
                # done (either sent fallback or failed)
                if progress_message:
                    PROGRESS_UPDATE_TIMES.pop(progress_message.id, None)
                return

            # Upload converted file
            await bot_client.send_video(
                chat_id=chat_id,
                video=mp4_path,
                caption=full_caption,
                supports_streaming=True,
                progress=upload_progress,
                progress_args=(progress_message,),
            )

            # Cleanup
            try:
                os.remove(mp4_path)
            except:
                pass

        # ---------- IMAGE (Compressed) ----------
        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
            await bot_client.send_photo(
                chat_id=chat_id,
                photo=str(renamed_path),
                caption=full_caption,
                progress=upload_progress,
                progress_args=(progress_message,),
            )

        # ---------- DOCUMENT (Other file types) ----------
        else:
            await bot_client.send_document(
                chat_id=chat_id,
                document=str(renamed_path),
                caption=full_caption,
                file_name=new_filename,
                progress=upload_progress,
                progress_args=(progress_message,),
            )

        if progress_message:
            await progress_message.edit("✅ Upload complete!")

    except Exception as e:
        LOGGER(__name__).warning(f"send_video/send_document failed: {e}")
        # fallback as file
        try:
            await bot_client.send_document(
                chat_id=chat_id,
                document=str(renamed_path),
                caption=full_caption + "\n⚠️ Sent as file (streaming unsupported)"
            )
        except Exception as e2:
            LOGGER(__name__).error(f"Fallback send_document failed: {e2}")
            if progress_message:
                await progress_message.edit(f"❌ Upload failed: {e2}")

    # ✅ revert filename back to original after upload (for cleanup)
    try:
        if renamed_path != file_path and os.path.exists(renamed_path):
            os.rename(renamed_path, file_path)
    except Exception as e:
        LOGGER(__name__).warning(f"Rename revert failed: {e}")

    if progress_message:
        PROGRESS_UPDATE_TIMES.pop(progress_message.id, None)

    save_upload_number(count + 1)

async def handle_download(bot_client: Client, message: Message, post_url: str):
    if "?" in post_url:
        post_url = post_url.split("?", 1)[0]

    try:
        chat_id, message_id = getChatMsgID(post_url)
        chat_message = await safe_user_call(user.get_messages, chat_id=chat_id, message_ids=message_id)

        LOGGER(__name__).info(f"[{readable_now()}] Download requested: {post_url}")

        # Media group
        if chat_message.media_group_id:
            ok = await processMediaGroup(chat_message, bot_client, message)
            if not ok:
                await message.reply("🧿\nCould not extract valid media from the media group.\n🧿")
            return

        if not (chat_message.media or chat_message.text or chat_message.caption):
            await message.reply("🧿**No media or text found in the post URL.**🧿")
            return

        # File size/limit
        file_size = None
        if chat_message.document:
            file_size = chat_message.document.file_size
        elif chat_message.video:
            file_size = chat_message.video.file_size
        elif chat_message.audio:
            file_size = chat_message.audio.file_size
        elif chat_message.photo:
            file_size = getattr(chat_message.photo, "file_size", None)

        if file_size:
            is_premium = False
            try:
                me = await user.get_me()
                is_premium = getattr(me, "is_premium", False)
            except Exception:
                pass

            allowed = await fileSizeLimit(file_size, message, "download", is_premium)
            if not allowed:
                return

        parsed_caption = await get_parsed_msg(chat_message.caption or "", chat_message.caption_entities)
        parsed_text = await get_parsed_msg(chat_message.text or "", chat_message.entities)

        if chat_message.media:
            async with DOWNLOAD_SEM:
                progress_message = await message.reply("⚽️ ⏭ Preparing to download... 🏀")

                filename = get_file_name(message_id, chat_message)
                filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
                download_path = str(TEMP_DIR / f"{message.id}_{filename}")

                try:
                    media_path = await safe_download(chat_message, download_path, progress_message)
                except Exception as e:
                    try:
                        await progress_message.edit(f"❌ Download failed: {e}")
                    except Exception:
                        pass
                    LOGGER(__name__).error(e)
                    return

                # After successful download, upload/send to user
                try:
                    await progress_message.edit("⚙️ Processing file for sending...")
                except Exception:
                    pass

                try:
                    await stream_send(bot_client, message.chat.id, media_path, progress_message)
                except Exception as e:
                    LOGGER(__name__).warning(f"stream_send failed, fallback to send_document: {e}")
                    try:
                        await bot_client.send_document(message.chat.id, media_path, caption=parsed_caption or parsed_text)
                    except Exception as e2:
                        LOGGER(__name__).error(f"Fallback send_document failed: {e2}")

                cleanup_download(media_path)
                try:
                    await progress_message.delete()
                except Exception:
                    pass

        else:
            # text-only
            await message.reply(parsed_text or parsed_caption or "No text.")

    except (PeerIdInvalid, BadRequest, KeyError) as e:
        LOGGER(__name__).error(e)
        await message.reply("🧿 **Make sure the user client is part of the chat.** 🌤 🧿")
    except Exception as e:
        LOGGER(__name__).error(e)
        await message.reply(f"**❌ {str(e)}**")


def in_group(chat: Message) -> bool:
    return chat.chat and chat.chat.type in (ChatType.SUPERGROUP, ChatType.GROUP)


def should_respond_in_group(msg: Message) -> bool:
    """Respect per-chat mode."""
    if not in_group(msg):
        return True
    mode = CHAT_SETTINGS.get(msg.chat.id, {}).get("mode", "all")
    if mode == "all":
        return True
    # mention mode
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "mention":
                # crude mention check for @botusername in text
                try:
                    me = bot.me
                except Exception:
                    me = None
                if me and f"@{me.username}".lower() in (msg.text or "").lower():
                    return True
    return False


# ---------------- Commands ----------------
@bot.on_message(filters.command("info") & (filters.private | filters.group | filters.topic))
async def info_cmd(_, message: Message):
    me = await bot.get_me()
    uptime = get_readable_time(time() - BOT_START_TIME)
    total, used, free = shutil.disk_usage(".")
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    await message.reply(
        f"🤖 **Bot Info**\n"
        f"• Name: `{me.first_name}`\n"
        f"• Username: @{me.username}\n"
        f"• Uptime: `{uptime}`\n"
        f"• CPU: `{cpu}%`\n"
        f"• RAM: `{ram}%`\n"
        f"• Disk Free: `{get_readable_file_size(free)}`"
    )
async def auto_cleanup():
    while True:
        now = time()
        for file in TEMP_DIR.glob("*"):
            if now - file.stat().st_mtime > 3600:  # older than 1 hour
                try:
                    if file.is_file():
                        file.unlink()
                except Exception:
                    pass
        await asyncio.sleep(1800)  # run every 30 min

# Launch in background
bot.loop.create_task(auto_cleanup())
PAUSED = False

@bot.on_message(filters.command("pause") & (filters.private | filters.group | filters.topic))
async def pause_all(_, message: Message):
    global PAUSED
    PAUSED = True
    await message.reply("⏸️ All downloads paused!")

OWNER_ID = 8241756083  # your Telegram ID

@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_, message: Message):
    if len(message.text.split(maxsplit=1)) < 2:
        return await message.reply("🗣️ Usage: `/broadcast message`")
    text = message.text.split(maxsplit=1)[1]
    sent = 0
    async for dialog in bot.get_dialogs():
        try:
            if dialog.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.PRIVATE]:
                await bot.send_message(dialog.chat.id, f"📢 Broadcast:\n{text}")
                sent += 1
                await asyncio.sleep(0.2)
        except Exception:
            pass
    await message.reply(f"✅ Message sent to `{sent}` chats.")

DOWNLOAD_HISTORY = []

async def add_to_history(file_name):
    DOWNLOAD_HISTORY.insert(0, file_name)
    if len(DOWNLOAD_HISTORY) > 5:
        DOWNLOAD_HISTORY.pop()

@bot.on_message(filters.command("history") & (filters.private | filters.group | filters.topic))
async def history_cmd(_, message: Message):
    if not DOWNLOAD_HISTORY:
        return await message.reply("📭 No recent downloads.")
    text = "🧾 **Last 5 Downloads:**\n" + "\n".join([f"• `{x}`" for x in DOWNLOAD_HISTORY])
    await message.reply(text)

@bot.on_message(filters.command("resume") & (filters.private | filters.group | filters.topic))
async def resume_all(_, message: Message):
    global PAUSED
    PAUSED = False
    await message.reply("▶️ All downloads resumed!")

# Integrate inside handle_download() — before download starts:
# if PAUSED: await message.reply("⏸️ Waiting for resume..."); while PAUSED: await asyncio.sleep(2)

@bot.on_message(filters.command("queue") & (filters.private | filters.group | filters.topic))
async def queue_cmd(_, message: Message):
    active = [t for t in RUNNING_TASKS if not t.done()]
    await message.reply(f"📦 Active tasks: `{len(active)}`\n"
                        f"{'🟢 Running' if active else '⚪ Idle'}")

@bot.on_message(filters.command("disk") & (filters.private | filters.group | filters.topic))
async def disk_cmd(_, message: Message):
    total, used, free = shutil.disk_usage(".")
    percent = used / total * 100
    bar = create_small_bar(percent, filled_sym="🟦", empty_sym="⬜")
    await message.reply(f"💾 **Disk Usage:** {percent:.2f}%\n{bar}\n"
                        f"Used: `{get_readable_file_size(used)}`\nFree: `{get_readable_file_size(free)}`")

@bot.on_message(filters.command("cleanfailed") & (filters.private | filters.group | filters.topic))
async def clean_failed(_, message: Message):
    removed = 0
    for f in TEMP_DIR.glob("*"):
        if f.stat().st_size == 0 or f.suffix == ".part":
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    await message.reply(f"🧹 Removed `{removed}` failed or partial downloads.")
import mimetypes

# moviepy is optional — provide safe fallbacks using ffmpeg/ffprobe if it's not installed.
try:
    import moviepy.editor as mp

    def convert_mkv_to_mp4(src_path, dst_path):
        """Convert using moviepy when available."""
        clip = mp.VideoFileClip(str(src_path))
        clip.write_videofile(str(dst_path), codec="libx264", audio_codec="aac")
        clip.close()
        return str(dst_path)
    

except Exception:
    mp = None
    import subprocess
    import json

    def convert_mkv_to_mp4(src_path, dst_path):
        """Convert using ffmpeg as a fallback (requires ffmpeg in PATH)."""
        cmd = [
            "ffmpeg", "-y", "-i", str(src_path),
            "-c:v", "libx264", "-c:a", "aac",
            "-map", "0", str(dst_path)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.decode(errors='ignore')}")
        return str(dst_path)

    def get_video_duration(path):
        """Get duration using ffprobe as a fallback (requires ffprobe in PATH)."""
        try:
            cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "format=duration", "-of", "json", str(path)
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode != 0:
                return None
            info = json.loads(proc.stdout.decode() or "{}")
            return float(info.get("format", {}).get("duration", 0.0) or 0.0)
        except Exception:
            return None
import cv2
from PIL import Image

@bot.on_message(filters.command("preview") & (filters.private | filters.group | filters.topic))
async def preview_cmd(_, message: Message):
    items = sorted(TEMP_DIR.glob("*"), key=os.path.getmtime, reverse=True)
    if not items:
        return await message.reply("⚠️ No file to preview.")
    latest = items[0]
    if latest.suffix.lower() not in [".mp4", ".mkv", ".mov"]:
        return await message.reply("🎞️ Preview works only for videos.")
    cap = cv2.VideoCapture(str(latest))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 100)
    ret, frame = cap.read()
    cap.release()
    if ret:
        Image.fromarray(frame).save("preview.jpg")
        await bot.send_photo(message.chat.id, "preview.jpg", caption=f"🎬 Preview of {latest.name}")
        os.remove("preview.jpg")

import mimetypes
import moviepy.editor as mp

@bot.on_message(filters.command("analyze") & (filters.private | filters.group | filters.topic))
async def analyze_cmd(_, message: Message):
    items = sorted(TEMP_DIR.glob("*"), key=os.path.getmtime, reverse=True)
    if not items:
        return await message.reply("⚠️ No file found.")
    f = items[0]
    size = get_readable_file_size(f.stat().st_size)
    mime = mimetypes.guess_type(f)[0] or "Unknown"
    dur = "—"
    if mime.startswith("video"):
        clip = mp.VideoFileClip(str(f))
        dur = get_readable_time(clip.duration)
        clip.close()
    await message.reply(f"📊 **File Info**\n📁 `{f.name}`\n💾 Size: `{size}`\n🧩 Type: `{mime}`\n⏱ Duration: `{dur}`")


@bot.on_message(filters.command("start") & (filters.private | filters.group | filters.topic))
async def start_cmd(_, message: Message):
    if in_group(message):
        txt = "👋 *Media Downloader Pro* is here.\nUse `/dl <t.me link>` or `/mode` to set reply behavior."
    else:
        welcome_texts = [
            "👋Hey there! Welcome to *Media Downloader Pro*!🦄",
            "🌸Loading your ultimate media hub…🌈",
            "⚡Fetching photos, videos, audio & huge docs — even *4GB+*💥",
            "🌊Ready to download Telegram posts like a boss!🐬"
        ]
        msg = await message.reply("🧿\n⏳ Preparing your ultimate experience... \n🧿")
        for txt in welcome_texts:
            try:
                await msg.edit(txt)
                await asyncio.sleep(0.8)
            except Exception:
                pass
        txt = (
            "👑 *Media Downloader Pro - Ultimate Edition* 👑\n"
            "☄️ Fetch photos, videos, audio & docs — even *4GB+* files!\n"
            "📌 Use `/dl <link>` to download a post 💫\n"
            "📚 Explore all commands with `/help` 🪪"
        )
        msg = msg
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔔 Channel Updates 🔔", url="https://t.me/+RlUfmWbxEoM5Zjll")]]
    )
    await message.reply(emoji_filled(txt), reply_markup=markup, disable_web_page_preview=True)


@bot.on_message(filters.command("help") & (filters.private | filters.group | filters.topic))
async def help_command(_, message: Message):
    help_text = (
        "**🧿 Media Downloader Pro — Full Command Guide 🧿**\n\n"

        "📦 **Basic Commands**\n"
        "• `/start` — Start or restart the bot.\n"
        "• `/help` — Show this help message.\n"
        "• `/ping` — Check bot speed.\n"
        "• `/uptime` — Display bot uptime.\n"
        "• `/status` — Full system usage and active tasks.\n"
        "• `/stats` — Show short system stats.\n\n"

        "📥 **Download Commands**\n"
        "• `/dl <t.me link>` — Download a single Telegram post.\n"
        "• `/bdl <start_link> <end_link>` — Batch download a range of posts.\n"
        "• `/killall` — Cancel all running downloads.\n"
        "• `/queue` — Show active downloads.\n\n"

        "📁 **File & Storage Commands**\n"
        "• `/history` — View last 5 downloaded files.\n"
        "• `/reupload` — Re-upload the most recent file.\n"
        "• `/zip` — Compress recent downloads into one ZIP.\n"
        "• `/clear` — Remove all temporary downloads.\n"
        "• `/purge <minutes>` — Delete files older than given time.\n"
        "• `/cleanfailed` — Delete failed or partial downloads.\n"
        "• `/disk` — Check live disk usage.\n\n"

        "☁️ **Cloud & Mirror**\n"
        "• `/mirror` — Upload last file to Google Drive.\n\n"

        "⚙️ **Group Settings**\n"
        "• `/mode all` — React to all Telegram links.\n"
        "• `/mode mention` — React only when bot is tagged.\n\n"

        "🚀 **Utilities**\n"
        "• `/speed` — Internet speed test.\n"
        "• `/logs` — Get bot logs file.\n\n"

        "🔗 **Useful Links**\n"
        "• [Join Channel](https://t.me/+RlUfmWbxEoM5Zjll)\n"
        "• Developer: @AstroCollapse\n"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Join Channel 🔔", url="https://t.me/+RlUfmWbxEoM5Zjll")],
        [InlineKeyboardButton("📢 Developer", url="https://t.me/AstroCollapse")]
    ])

    await message.reply(help_text, reply_markup=markup, disable_web_page_preview=True)

@bot.on_message(filters.command("ping") & (filters.private | filters.group | filters.topic))
async def ping_cmd(_, message: Message):
    start = time()
    m = await message.reply("🏓 Pong...")
    dt = (time() - start) * 1000
    await m.edit(f"🏓 Pong! `{dt:.1f} ms`")


@bot.on_message(filters.command("mode") & filters.group)
async def mode_cmd(_, message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        mode = CHAT_SETTINGS.get(message.chat.id, {}).get("mode", "all")
        return await message.reply(f"Current mode: `{mode}`. Use `/mode all` or `/mode mention`.")
    val = args[1].strip().lower()
    if val not in ("all", "mention"):
        return await message.reply("Choose `all` or `mention`.")
    CHAT_SETTINGS.setdefault(message.chat.id, {})["mode"] = val
    await message.reply(f"✅ Mode updated to `{val}`.")

@bot.on_message(filters.command("dl") & (filters.private | filters.group | filters.topic))
async def download_media(bot_client: Client, message: Message):
    if in_group(message) and not should_respond_in_group(message):
        return
    if len(message.command) < 2:
        await message.reply("🧿\n**Provide a post URL after the /dl command.**\n🧿")
        return
    post_url = message.command[1]
    track_task(handle_download(bot_client, message, post_url))

from pyrogram.errors import FloodWait

async def safe_reply(message, text):
    while True:
        try:
            return await message.reply(text)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception:
            return

@bot.on_message(filters.command("bdl") & (filters.private | filters.group | filters.topic))
async def download_range(bot_client: Client, message: Message):
    if in_group(message) and not should_respond_in_group(message):
        return
    
    await asyncio.sleep(1.5)
    args = message.text.split()
    if len(args) != 3 or not all(arg.startswith("https://t.me/") for arg in args[1:]):
        return await message.reply(
            "🧿\n🚀 Batch Download\n`/bdl start_link end_link`\n\n"
            "💡 Example: `/bdl https://t.me/mychannel/100 https://t.me/mychannel/120`\n🧿"
        )

    try:
        start_chat, start_id = getChatMsgID(args[1])
        end_chat, end_id = getChatMsgID(args[2])
    except Exception as e:
        return await message.reply(f"🧿\n ❌ Error parsing links:\n{e} \n🧿")

    if start_chat != end_chat:
        return await message.reply("🧿\n❌ Both links must be from the same channel.\n🧿")
    if start_id > end_id:
        return await message.reply("🧿\n❌ Invalid range: start ID cannot exceed end ID.\n🧿")

    try:
        await user.get_chat(start_chat)
    except Exception:
        pass
    
    prefix = args[1].rsplit("/", 1)[0]
    loading = await safe_reply(message,"text")

    downloaded = skipped = failed = 0

    for msg_id in range(start_id, end_id + 1):
        url = f"{prefix}/{msg_id}"
        try:
            chat_msg = await safe_user_call(user.get_messages, chat_id=start_chat, message_ids=msg_id)
            if not chat_msg:
                skipped += 1
                continue

            has_media = bool(chat_msg.media_group_id or chat_msg.media)
            has_text = bool(chat_msg.text or chat_msg.caption)
            if not (has_media or has_text):
                skipped += 1
                continue

            task = track_task(handle_download(bot_client, message, url))
            try:
                await task
                downloaded += 1
            except asyncio.CancelledError:
                await loading.delete()
                return await message.reply(
                    f"🧿\n❌ Batch canceled after downloading `{downloaded}` posts.\n🧿"
                )

        except Exception as e:
            failed += 1
            LOGGER(__name__).error(f"Error at {url}: {e}")

        await asyncio.sleep(0.5)

    try:
        await loading.delete()
    except Exception:
        pass

    await message.reply(
        "🧿\n**✅ Batch Process Complete!**\n\n"
        f"📥 **Downloaded** : `{downloaded}`\n"
        f"⏭️ **Skipped**    : `{skipped}`\n"
        f"❌ **Failed**     : `{failed}`\n"
        "🧿"
    )


@bot.on_message((filters.private | filters.group | filters.topic) & ~filters.command(["start", "help", "dl", "stats", "logs", "killall", "zip", "speed", "clear", "uptime", "status", "bdl", "mode", "ping"]))
async def handle_any_message(bot_client: Client, message: Message):
    # In groups, obey mode
    if in_group(message) and not should_respond_in_group(message):
        return

    # If pure text and contains a t.me link, try to handle
    text = (message.text or message.caption or "").strip()
    if text and "https://t.me/" in text:
        # pick first t.me-like token
        for token in text.split():
            if token.startswith("https://t.me/"):
                track_task(handle_download(bot_client, message, token))
                break


@bot.on_message(filters.command("status") & (filters.private | filters.group | filters.topic))
async def status_cmd(_, message: Message):
    uptime = get_readable_time(time() - BOT_START_TIME)
    total_b, used_b, free_b = shutil.disk_usage(".")
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    net = psutil.net_io_counters()
    upload_gb = net.bytes_sent / (1024 ** 3)
    download_gb = net.bytes_recv / (1024 ** 3)

    stats = (
        " ╭─🌸 BOT STATUS DASHBOARD 🌸─╮\n"
        f"│ 🕒 Uptime         : {uptime:<12} │\n"
        f"│ 💾 Disk Free/Total: {get_readable_file_size(free_b):<10} / {get_readable_file_size(total_b):<10} │\n"
        f"│ ⚙️ CPU Usage      : {cpu:>5.1f}% {create_small_bar(cpu)} │\n"
        f"│ 🧠 RAM Usage      : {mem:>5.1f}% {create_small_bar(mem, filled_sym='🟩', empty_sym='⬛')} │\n"
        f"│ 💽 Disk Usage     : {disk:>5.1f}% {create_small_bar(disk, filled_sym='🟦', empty_sym='⬜')} │\n"
        f"│ 📡 Net Upload     : {upload_gb:>6.2f} GB {create_small_bar(min(upload_gb,100), total=100, filled_sym='🟧', empty_sym='⬛')} │\n"
        f"│ 📡 Net Download   : {download_gb:>6.2f} GB {create_small_bar(min(download_gb,100), total=100, filled_sym='🟪', empty_sym='⬛')} │\n"
        f"│ 📂 Active Tasks   : {len([t for t in RUNNING_TASKS if not t.done()]):<5} {create_small_bar(min(len([t for t in RUNNING_TASKS if not t.done()])*5,100), total=100, filled_sym='🟫', empty_sym='⬜')} │\n"
        " ╰──────────────────────────────────────────╯"
    )
    await message.reply(stats)


@bot.on_message(filters.command("uptime") & (filters.private | filters.group | filters.topic))
async def uptime_cmd(_, message: Message):
    uptime = get_readable_time(time() - BOT_START_TIME)
    await message.reply(f"⏱️ Bot Uptime: `{uptime}`")


@bot.on_message(filters.command("logs") & (filters.private | filters.group | filters.topic))
async def logs_cmd(_, message: Message):
    if os.path.exists("logs.txt"):
        await message.reply_document(document="logs.txt", caption="**Logs**")
    else:
        await message.reply("**Not exists**")


@bot.on_message(filters.command("killall") & (filters.private | filters.group | filters.topic))
async def cancel_all_tasks(_, message: Message):
    cancelled = 0
    for task in list(RUNNING_TASKS):
        if not task.done():
            task.cancel()
            cancelled += 1
    await message.reply(f"🧿**Cancelled {cancelled} running task(s).**🧿")


@bot.on_message(filters.command("clear") & (filters.private | filters.group | filters.topic))
async def clear_temp(_, message: Message):
    files = list(TEMP_DIR.iterdir())
    removed = 0
    for f in files:
        try:
            if f.is_file():
                f.unlink()
                removed += 1
            elif f.is_dir():
                shutil.rmtree(f)
                removed += 1
        except Exception as e:
            LOGGER(__name__).warning(f"🧿Failed to remove {f}: {e}☄️")
    await message.reply(f"🧹 Cleared `{removed}` temporary item(s).☄️")


@bot.on_message(filters.command("zip") & (filters.private | filters.group | filters.topic))
async def create_zip(_, message: Message):
    items = list(TEMP_DIR.iterdir())
    if not items:
        return await message.reply("📦 No downloaded files to zip.☄️")

    zip_name = TEMP_DIR / f"archive_{int(time())}.zip"
    msg = await message.reply("📦 Creating zip archive — please wait...☄️")

    try:
        with ZipFile(zip_name, "w") as zf:
            for item in items:
                if item.is_file():
                    zf.write(item, arcname=item.name)
                elif item.is_dir():
                    for root, _, files in os.walk(item):
                        for file in files:
                            full = Path(root) / file
                            arc = full.relative_to(TEMP_DIR)
                            zf.write(full, arcname=str(arc))

        await _.send_document(message.chat.id, str(zip_name), caption="📦 Archive of recent downloads")
        await msg.delete()
    except Exception as e:
        LOGGER(__name__).error(e)
        await msg.edit(f"❌ Could not create zip: {e}")
    finally:
        try:
            zip_name.unlink()
        except Exception:
            pass


@bot.on_message(filters.command("speed") & (filters.private | filters.group | filters.topic))
async def speedtest_cmd(_, message: Message):
    msg = await message.reply("⚡ Running speed test — this may take 20–60s...")
    try:
        # running in thread would be nicer; kept simple per request
        st = speedtest.Speedtest()
        st.get_best_server()
        down = st.download()
        up = st.upload()
        ping = st.results.ping

        down_mbps = round(down / 1024 / 1024, 2)
        up_mbps = round(up / 1024 / 1024, 2)

        await msg.edit(f"🧿 Speed Test Results:\n• Download: `{down_mbps} Mbps`\n• Upload: `{up_mbps} Mbps`\n• Ping: `{ping} ms`🧿")
    except Exception as e:
        LOGGER(__name__).error(e)
        await msg.edit(f"⚠️ Speed test failed: {e}")


@bot.on_message(filters.command("stats") & (filters.private | filters.group | filters.topic))
async def stats_cmd(_, message: Message):
    total_b, used_b, free_b = shutil.disk_usage(".")
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory().percent
    m = (
        "🧿"
        f"📈 CPU: `{cpu}%` | RAM: `{mem}%`\n"
        f"💾 Disk Free: `{get_readable_file_size(free_b)}` | Total: `{get_readable_file_size(total_b)}`\n"
        f"📂 Active Tasks: `{len([t for t in RUNNING_TASKS if not t.done()])}`"
        "🧿"
    )
    await message.reply(m)


# ---------------- Main ----------------
if __name__ == "__main__":
    try:
        LOGGER(__name__).info("Media Downloader Pro - Bot Starting")
        # Start user client first
        user.start()
        # Run bot (blocks)
        bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as err:
        LOGGER(__name__).error(err)
    finally:
        LOGGER(__name__).info("Bot Stopped")
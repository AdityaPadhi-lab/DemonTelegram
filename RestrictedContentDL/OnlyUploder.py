import os
import re
import asyncio
import time
from pyrogram import Client
from pyrogram.types import Message

app = Client(
    "UploaderBot",
    api_id=26192516,
    api_hash="71484c4e476200b1c68b9024f354e935",
    bot_token="7969370400:AAHHGtR0WwdikXNT04YVVfqSdUXuEnuOH8w"
)

# --- Global Spinner State ---
upload_done = False
current_emoji = "🔥"
last_update_time = {}
last_uploaded_bytes = {}

# --- Natural Sort Helper ---
def natural_key(string):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', string)]

# --- Spinner Animation ---
async def spinner_animation(progress_msg):
    global current_emoji, upload_done
    spinner = [
        "🔥", "⚡", "💥", "💫", "✨", "🌟", "☄️", "🌠",
        "🚀", "🛸", "🛰️", "📡", "🌌", "🪐", "⭐", "🔭",
        "🌀", "🌪️", "💨", "🔄", "🔁", "♻️", "⏳", "🕛",
        "💎", "🎯", "💻", "🛠️", "🧩", "🤖", "🐉", "🌈",
        "💜", "💙", "💚", "💛", "🧡", "❤️"
    ]
    i = 0
    while not upload_done:
        current_emoji = spinner[i % len(spinner)]
        i += 1
        await asyncio.sleep(1)

# --- Progress Callback ---
async def progress_callback(current, total, progress_msg, video_name):
    global upload_done

    now = time.time()
    prev_time = last_update_time.get(video_name, now)
    prev_bytes = last_uploaded_bytes.get(video_name, current)

    elapsed = now - prev_time
    uploaded_bytes = current - prev_bytes

    if elapsed > 0:
        speed_bps = uploaded_bytes / elapsed
        speed_kbps = speed_bps / 1024
        speed_mbps = speed_kbps / 1024
        eta = (total - current) / speed_bps if speed_bps > 0 else 0
    else:
        speed_kbps = speed_mbps = eta = 0

    last_update_time[video_name] = now
    last_uploaded_bytes[video_name] = current

    percent = int(current * 100 / total)
    upload_done = percent >= 100

    bar = "█" * int(percent / 5) + "░" * (20 - int(percent / 5))
    eta_min = int(eta // 60)
    eta_sec = int(eta % 60)

    try:
        await progress_msg.edit_text(
            f"{current_emoji} **Uploading:** `{video_name}`\n"
            f"📊 `{bar}` {percent}%\n"
            f"⚡ **Speed:** {speed_mbps:.2f} Mbps | {speed_kbps:.2f} KB/s\n"
            f"⏳ **ETA:** {eta_min:02d}:{eta_sec:02d} (mm:ss)"
        )
    except:
        pass

# --- Upload All Videos ---
@app.on_message()
async def upload_all_videos(client, message: Message):
    global upload_done, current_emoji
    folder_path = r"C:\Users\asus\Downloads\SESSION"
    thumb_path = r"C:\Users\asus\Downloads\Uploder\thumb.jpeg"

    if not os.path.exists(folder_path):
        await message.reply_text("⚠️ Folder not found!")
        return
    if not os.path.exists(thumb_path):
        await message.reply_text("⚠️ Thumbnail not found!")
        return

    # Natural numeric sorting
    videos = sorted(
        [f for f in os.listdir(folder_path) if f.lower().endswith((".mp4", ".mkv", ".avi", ".mov"))],
        key=natural_key
    )

    if not videos:
        await message.reply_text("⚠️ No video files found in folder!")
        return

    progress_msg = await message.reply_text(f"📂 Found {len(videos)} videos. Starting upload sequentially...")

    for video in videos:
        video_path = os.path.join(folder_path, video)
        caption = (
            f"🎬 **{video}**\n\n"
            f"👤 Uploaded by [@AstroCollapse](https://t.me/AstroCollapse)\n"
            f"🔗 [Join Channel](https://t.me/+vRPr9SMdviY2YjM9)"
        )

        upload_done = False
        current_emoji = "🔥"

        spinner_task = asyncio.create_task(spinner_animation(progress_msg))

        try:
            await client.send_video(
                chat_id=message.chat.id,
                video=video_path,
                thumb=thumb_path,
                caption=caption,
                supports_streaming=True,
                progress=progress_callback,
                progress_args=(progress_msg, video)
            )
        except Exception as e:
            await message.reply_text(f"❌ Failed to upload {video}: {e}")

        upload_done = True
        await spinner_task
        await progress_msg.edit_text(f"✅ Uploaded: **{video}**")

    await progress_msg.edit_text("✅ All uploads completed successfully!")

app.run()

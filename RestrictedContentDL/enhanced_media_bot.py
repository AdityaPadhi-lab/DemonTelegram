import os
import asyncio
import logging
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ====== Logging Setup ======
LOG_FORMAT = "[%(asctime)s - %(levelname)s] - %(funcName)s() - Line %(lineno)d: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%d-%b-%y %I:%M:%S %p")

# ====== Configuration ======
API_ID = 21691724
API_HASH = "aaed0c61723d064fc51928efc54ba1df"
BOT_TOKEN = "8461849017:AAFvA9k0-FmkdTSKnU-Bo_7wU1Y4Pn4Tp8k"

ADMINS = [8241756083, 1302915882, 6531625222]
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ====== Initialize Bot ======
bot = Client("EnhancedMediaBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ====== Helper: Progress Bar ======
async def progress_bar(current, total, message, start_time):
    percent = (current / total) * 100
    bar = "▓" * int(percent / 5) + "░" * (20 - int(percent / 5))
    elapsed = (datetime.now() - start_time).seconds
    speed = current / (elapsed + 1)
    eta = (total - current) / (speed + 1)
    try:
        await message.edit_text(
            f"⏳ **Downloading...**\n\n"
            f"{bar} {percent:.1f}%\n"
            f"📦 **{current / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB**\n"
            f"⚡ **Speed:** {speed / 1024 / 1024:.2f} MB/s\n"
            f"⌛ **ETA:** {int(eta)}s"
        )
    except Exception:
        pass

# ====== Start Command ======
@bot.on_message(filters.command("start"))
async def start(_, msg):
    buttons = [
        [InlineKeyboardButton("💬 Support", url="https://t.me/+vRPr9SMdviY2YjM9")],
        [InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/MetalAtom")]
    ]
    await msg.reply_text(
        "🤖 **Welcome to Media Downloader Bot!**\n\n"
        "Just send me a **Telegram post link**, and I’ll download the full media — even from private or restricted channels!\n\n"
        "⚙️ *Supports:* Video, File, Photo, Audio\n\n"
        "🧑‍💼 Admins can manage requests & check logs.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ====== Download Command ======
@bot.on_message(filters.command("download") & filters.user(ADMINS))
async def handle_download(_, msg):
    try:
        url = msg.text.split(" ", 1)[1].strip()
    except IndexError:
        await msg.reply("❌ Please provide a valid Telegram post URL.")
        return

    reply = await msg.reply_text("🔍 **Fetching message... Please wait.**")
    try:
        parts = url.split("/")
        chat_id = f"-100{parts[-2]}" if "c" in parts else parts[-2]
        message_id = int(parts[-1])

        message = await bot.get_messages(chat_id, message_id)
        file_name = message.document.file_name if message.document else "video.mp4"
        download_path = os.path.join(DOWNLOAD_DIR, file_name)

        progress_msg = await msg.reply_text("🚀 Starting download...")
        start_time = datetime.now()

        # ✅ Fixed: Remove block_size, compatible with Pyrogram v2
        await message.download(
            file_name=download_path,
            progress=progress_bar,
            progress_args=(progress_msg, start_time)
        )

        await progress_msg.edit_text(
            f"✅ **Download complete!**\n\n📁 `{file_name}`\n"
            f"📦 **Size:** {os.path.getsize(download_path) / 1024 / 1024:.2f} MB"
        )

    except FloodWait as e:
        await asyncio.sleep(e.value)
        await msg.reply("⚠️ FloodWait triggered. Retrying...")
    except RPCError as e:
        logging.error(f"Telegram RPC Error: {e}")
        await msg.reply(f"❌ Telegram error: {e}")
    except Exception as e:
        logging.error(f"Download Error: {e}")
        await msg.reply(f"❌ Download failed: {e}")

# ====== Help Command ======
@bot.on_message(filters.command("help"))
async def help_command(_, msg):
    await msg.reply_text(
        "📖 **Bot Commands:**\n\n"
        "• `/start` — Welcome message\n"
        "• `/download <telegram_post_url>` — Download private/restricted media\n"
        "• `/help` — Command list\n"
        "• `/status` — Show bot uptime & usage\n"
        "• `/admins` — Show admin list"
    )

# ====== Admin Command ======
@bot.on_message(filters.command("admins"))
async def admin_list(_, msg):
    admin_text = "👑 **Bot Admins:**\n" + "\n".join([f"• `{uid}`" for uid in ADMINS])
    await msg.reply_text(admin_text)

# ====== Status Command ======
@bot.on_message(filters.command("status") & filters.user(ADMINS))
async def status(_, msg):
    total_files = len(os.listdir(DOWNLOAD_DIR))
    total_size = sum(os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) for f in os.listdir(DOWNLOAD_DIR)) / 1024 / 1024
    await msg.reply_text(
        f"📊 **Bot Status:**\n\n"
        f"📁 Files: {total_files}\n"
        f"💾 Storage Used: {total_size:.2f} MB\n"
        f"🕒 Active Since: {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
    )

# ====== Run Bot ======
logging.info("Bot started successfully ✅")
bot.run()

# Copyright (C) @TheSmartBisnu
# Channel: https://t.me/itsSmartDev

import os
import shutil
import psutil
import asyncio
import signal
from time import time

from dotenv import load_dotenv
load_dotenv()

from base64 import urlsafe_b64decode
from config import PyroConf

raw = PyroConf.SESSION_STRING or ""
clean_session = raw.strip().strip('"').strip("'")

def _validate_session(s: str):
    if not s:
        raise ValueError("SESSION_STRING missing. Generate with: python -m pyrogram")
    # Basic char set check (urlsafe base64)
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
    invalid = set(s) - allowed
    if invalid:
        raise ValueError(f"Invalid characters in SESSION_STRING: {''.join(sorted(invalid))}")
    core_len = len(s.rstrip("="))
    if core_len % 4 == 1:
        raise ValueError("Invalid SESSION_STRING (corrupt length mod 4 == 1). Regenerate with: python -m pyrogram")
    # Try urlsafe decode with padding fix
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    try:
        decoded = urlsafe_b64decode(padded.encode())
    except Exception:
        raise ValueError("Invalid SESSION_STRING (urlsafe base64 decode failed). Regenerate.")
    if len(decoded) < 200:
        raise ValueError("Invalid SESSION_STRING (decoded blob too small). Regenerate.")

_validate_session(clean_session)

from pyleaves import Leaves
from pyrogram.enums import ParseMode
from pyrogram import Client, filters, idle
from pyrogram.errors import PeerIdInvalid, BadRequest
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

from helpers.utils import (
    processMediaGroup,
    progressArgs,
    send_media
)

from helpers.files import (
    get_download_path,
    fileSizeLimit,
    get_readable_file_size,
    get_readable_time,
    cleanup_download
)

from helpers.msg import (
    getChatMsgID,
    get_file_name,
    get_parsed_msg
)

print("Loaded SESSION_STRING length:", len(PyroConf.SESSION_STRING) if PyroConf.SESSION_STRING else "None")
from logger import LOGGER

# Initialize the bot client
_WORKERS = int(os.getenv("BOT_WORKERS", "64"))  # tune for Heroku dyno size
bot = Client(
    "media_bot",
    api_id=PyroConf.API_ID,
    api_hash=PyroConf.API_HASH,
    bot_token=PyroConf.BOT_TOKEN,
    workers=_WORKERS,
    parse_mode=ParseMode.MARKDOWN,
)

# Client for user session
user = Client("user_session", workers=1000, session_string=clean_session)

RUNNING_TASKS = set()
from asyncio import Event
CANCEL_EVENT = Event()

def cancel_all_running():
    CANCEL_EVENT.set()
    for task in list(RUNNING_TASKS):
        if not task.done():
            task.cancel()

def reset_cancellation():
    if CANCEL_EVENT.is_set():
        CANCEL_EVENT.clear()
RECENT_DOWNLOADS = {}  # (chat_id, message_id) -> last_success_timestamp
ACTIVE_LOCKS = {}      # (chat_id, message_id) -> asyncio.Lock
RECENT_TTL = int(os.getenv("DOWNLOAD_DEDUP_TTL", "900"))  # seconds (default 15 min)

# Batch job state for pause/continue
class BatchJob:
    def __init__(self, *, name: str, start_id: int, end_id: int, prefix: str, candidates, chat_id: int, start_url: str, end_url: str, initiator_id: int):
        self.name = name
        self.start_id = start_id
        self.end_id = end_id
        self.next_id = start_id
        self.prefix = prefix
        self.candidates = candidates  # primary_candidates list
        self.chat_id = chat_id
        self.initiator_id = initiator_id
        self.start_url = start_url
        self.end_url = end_url
        self.downloaded = 0
        self.skipped = 0
        self.failed = 0
        self.active = True
        self.paused = False
        self.created_at = time()
        self.updated_at = time()

    def snapshot(self):
        return {
            "name": self.name,
            "range": f"{self.start_id}-{self.end_id}",
            "next_id": self.next_id,
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "progress": f"{self.next_id - self.start_id}/{self.end_id - self.start_id + 1}",
        }

PAUSED_JOBS = {}  # name -> BatchJob
ACTIVE_BATCH_JOB: BatchJob | None = None
_pause_name_counter = 0

def _is_recent(chat_id, message_id):
    ts = RECENT_DOWNLOADS.get((chat_id, message_id))
    if not ts:
        return False
    if time() - ts > RECENT_TTL:
        RECENT_DOWNLOADS.pop((chat_id, message_id), None)
        return False
    return True

def _mark_download(chat_id, message_id):
    RECENT_DOWNLOADS[(chat_id, message_id)] = time()
    if len(RECENT_DOWNLOADS) > 200:
        expired = [k for k, v in RECENT_DOWNLOADS.items() if time() - v > RECENT_TTL]
        for k in expired:
            RECENT_DOWNLOADS.pop(k, None)

async def _acquire_lock(chat_id, message_id):
    key = (chat_id, message_id)
    lock = ACTIVE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        ACTIVE_LOCKS[key] = lock
    await lock.acquire()
    return key, lock

def _release_lock(key, lock):
    if lock.locked():
        lock.release()
    ACTIVE_LOCKS.pop(key, None)

def track_task(coro):
    task = asyncio.create_task(coro)
    RUNNING_TASKS.add(task)
    def _remove(_):
        RUNNING_TASKS.discard(task)
    task.add_done_callback(_remove)
    return task

# Patch progress function to allow mid-transfer cancellation
_orig_progress = Leaves.progress_for_pyrogram
def _cancellable_progress(current, total, *args):
    if CANCEL_EVENT.is_set():
        raise asyncio.CancelledError()
    return _orig_progress(current, total, *args)
Leaves.progress_for_pyrogram = _cancellable_progress

BOT_COMMANDS = [
    ("start", "Start bot / greeting"),
    ("help", "Show help info"),
    ("dl", "Download a single post"),
    ("bdl", "Batch download range"),
    ("pause", "Pause active batch"),
    ("continue", "Resume paused batch"),
    ("killall", "Cancel active downloads"),
    ("logs", "Fetch recent logs"),
    ("stats", "Show resource stats"),
]

DEBUG_UPDATES = os.getenv("DEBUG_UPDATES", "0") == "1"

if DEBUG_UPDATES:
    @bot.on_message()
    async def _debug_all_updates(_, message: Message):
        try:
            LOGGER(__name__).info(
                "UPDATE chat=%s type=%s from=%s text=%r", 
                getattr(message.chat, 'id', None),
                getattr(message.chat, 'type', None),
                getattr(message.from_user, 'id', None) if message.from_user else None,
                message.text if message.text else (message.caption or None)
            )
        except Exception as e:
            LOGGER(__name__).error(f"Debug update logging failed: {e}")


@bot.on_message(filters.command("start") & (filters.private | filters.group))
async def start(_, message: Message):
    if message.chat.type != "private":
        return await message.reply("Send /help in PM for full usage.")
    
    welcome_text = (
        "👋 **Welcome to Media Downloader Bot!**\n\n"
        "I can grab photos, videos, audio, and documents from any Telegram post.\n"
        "Just send me a link (paste it directly or use `/dl <link>`),\n"
        "or reply to a message with `/dl`.\n\n"
        "ℹ️ Use `/help` to view all commands and examples.\n"
        "🔒 Make sure the user client is part of the chat.\n\n"
        "Ready? Send me a Telegram post link!"
    )

    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Update Channel", url="https://t.me/itsSmartDev")]]
    )
    await message.reply(welcome_text, reply_markup=markup, disable_web_page_preview=True)


@bot.on_message(filters.command("help") & (filters.private | filters.group))
async def help_command(_, message: Message):
    help_text = (
        "💡 **Media Downloader Bot Help**\n\n"
        "➤ **Download Media**\n"
        "   – Send `/dl <post_URL>` **or** just paste a Telegram post link to fetch photos, videos, audio, or documents.\n\n"
        "➤ **Batch Download**\n"
        "   – Send `/bdl start_link end_link` to grab a series of posts in one go.\n"
        "     💡 Example: `/bdl https://t.me/mychannel/100 https://t.me/mychannel/120`\n"
        "**It will download all posts from ID 100 to 120.**\n\n"
        "➤ **Requirements**\n"
        "   – Make sure the user client is part of the chat.\n\n"
        "➤ **If the bot hangs**\n"
        "   – Send `/killall` to cancel any pending downloads.\n\n"
        "➤ **Logs**\n"
        "   – Send `/logs` to download the bot’s logs file.\n\n"
        "➤ **Stats**\n"
        "   – Send `/stats` to view current status:\n\n"
    "➤ **Command Summary**\n"
    "   • `/start` – Welcome & basic usage.\n"
    "   • `/help` – This help message.\n"
    "   • `/dl <post_URL>` – Download single post media/text.\n"
    "   • `/bdl <start_link> <end_link>` – Batch range download.\n"
    "   • `/killall` – Cancel all active downloads.\n"
    "   • `/logs` – Get recent log file.\n"
    "   • `/stats` – Runtime & resource stats.\n\n"
        "**Example**:\n"
        "  • `/dl https://t.me/itsSmartDev/547`\n"
        "  • `https://t.me/itsSmartDev/547`"
    )

    if message.chat.type != "private":
        # In group, still show condensed pointer plus option to PM for details
        return await message.reply(
            "Use /dl <t.me/link> or reply /dl to a link. Full help below (send /help in PM for cleaner view):\n\n"
            + help_text
        )

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Update Channel", url="https://t.me/itsSmartDev")]])
    await message.reply(help_text, reply_markup=markup, disable_web_page_preview=True)


async def handle_download(bot: Client, message: Message, post_url: str):
    # Cut off URL at '?' if present
    if "?" in post_url:
        post_url = post_url.split("?", 1)[0]

    try:
        chat_candidates, message_id = getChatMsgID(post_url)
        last_error = None
        chat_message = None
        chosen_chat_id = None
        for candidate in chat_candidates:
            try:
                chat_message = await user.get_messages(chat_id=candidate, message_ids=message_id)
                if chat_message:
                    chosen_chat_id = candidate
                    break
            except Exception as e:
                last_error = e
                continue
        if not chat_message:
            raise last_error or ValueError("Failed to fetch message with any chat id variant")

        # Dedup check
        lock_key = lock_obj = None
        if chosen_chat_id is not None and _is_recent(chosen_chat_id, message_id):
            return await message.reply("**Cached:** Already downloaded recently.")
        if chosen_chat_id is not None:
            lock_key, lock_obj = await _acquire_lock(chosen_chat_id, message_id)

        LOGGER(__name__).info(f"Downloading media from URL: {post_url}")

        if chat_message.document or chat_message.video or chat_message.audio:
            file_size = (
                chat_message.document.file_size
                if chat_message.document
                else chat_message.video.file_size
                if chat_message.video
                else chat_message.audio.file_size
            )

            if not await fileSizeLimit(
                file_size, message, "download", user.me.is_premium
            ):
                return

        parsed_caption = await get_parsed_msg(
            chat_message.caption or "", chat_message.caption_entities
        )
        parsed_text = await get_parsed_msg(
            chat_message.text or "", chat_message.entities
        )

        if chat_message.media_group_id:
            if not await processMediaGroup(chat_message, bot, message):
                await message.reply(
                    "**Could not extract any valid media from the media group.**"
                )
            return

        elif chat_message.media:
            start_time = time()
            progress_message = await message.reply("**📥 Downloading Progress...**")

            filename = get_file_name(message_id, chat_message)
            download_path = get_download_path(message.id, filename)

            media_path = await chat_message.download(
                file_name=download_path,
                progress=Leaves.progress_for_pyrogram,
                progress_args=progressArgs(
                    "📥 Downloading Progress", progress_message, start_time
                ),
            )

            LOGGER(__name__).info(f"Downloaded media: {media_path}")

            media_type = (
                "photo"
                if chat_message.photo
                else "video"
                if chat_message.video
                else "audio"
                if chat_message.audio
                else "document"
            )
            await send_media(
                bot,
                message,
                media_path,
                media_type,
                parsed_caption,
                progress_message,
                start_time,
            )

            cleanup_download(media_path)
            if chosen_chat_id is not None:
                _mark_download(chosen_chat_id, message_id)
            await progress_message.delete()

        elif chat_message.text or chat_message.caption:
            await message.reply(parsed_text or parsed_caption)
        else:
            await message.reply("**No media or text found in the post URL.**")

    except (PeerIdInvalid, BadRequest, KeyError):
        await message.reply("**Make sure the user client is part of the chat.**")
    except Exception as e:
        error_message = f"**❌ {str(e)}**"
        await message.reply(error_message)
        LOGGER(__name__).error(e)
    except asyncio.CancelledError:
        await message.reply("**⛔ Download cancelled.**")
        LOGGER(__name__).info("Download task cancelled by /killall")
    finally:
        if 'lock_key' in locals() and lock_key and 'lock_obj' in locals() and lock_obj:
            _release_lock(lock_key, lock_obj)


async def handle_download_status(bot: Client, message: Message, post_url: str) -> str:
    """Batch-friendly variant of handle_download.

    Returns one of: 'downloaded', 'skipped', 'failed'.
    It will retry transient download failures a limited number of times
    (controlled by RETRY_DOWNLOADS env or default 2) before deciding.
    """
    retries = int(os.getenv("RETRY_DOWNLOADS", "2"))
    try:
        chat_candidates, message_id = getChatMsgID(post_url)
    except Exception:
        return "skipped"

    last_error = None
    chat_message = None
    chosen_chat_id = None
    for candidate in chat_candidates:
        try:
            chat_message = await user.get_messages(chat_id=candidate, message_ids=message_id)
            if chat_message:
                chosen_chat_id = candidate
                break
        except Exception as e:
            last_error = e
            continue
    if not chat_message:
        LOGGER(__name__).info(f"All candidates failed for {post_url}: {last_error}")
        return "skipped"

    if chosen_chat_id is not None and _is_recent(chosen_chat_id, message_id):
        return "skipped"
    lock_key = lock_obj = None
    if chosen_chat_id is not None:
        lock_key, lock_obj = await _acquire_lock(chosen_chat_id, message_id)

    # Nothing to process
    if not (chat_message.media_group_id or chat_message.media or chat_message.text or chat_message.caption):
        return "skipped"

    # Media group path reuses existing logic; failures count as failed
    if chat_message.media_group_id:
        try:
            ok = await processMediaGroup(chat_message, bot, message)
            if ok and chosen_chat_id is not None:
                _mark_download(chosen_chat_id, message_id)
            return "downloaded" if ok else "skipped"
        except Exception as e:
            LOGGER(__name__).error(f"Media group error: {e}")
            return "failed"

    # Text only
    if not chat_message.media:
        try:
            parsed_caption = await get_parsed_msg(chat_message.caption or "", chat_message.caption_entities)
            parsed_text = await get_parsed_msg(chat_message.text or "", chat_message.entities)
            await message.reply(parsed_text or parsed_caption or "")
            if chosen_chat_id is not None:
                _mark_download(chosen_chat_id, message_id)
            return "downloaded"
        except Exception as e:
            LOGGER(__name__).error(f"Reply text error: {e}")
            return "failed"

    # Media (single) with retry
    filename = get_file_name(message_id, chat_message)
    download_path = get_download_path(message.id, filename)
    parsed_caption = await get_parsed_msg(chat_message.caption or "", chat_message.caption_entities)
    start_time = time()
    progress_message = await message.reply("**📥 Downloading Progress...**")

    attempt = 0
    while attempt <= retries:
        attempt += 1
        try:
            chat_message_refreshed = chat_message
            if attempt > 1:
                # Re-fetch message to refresh file reference in case it's expired
                for candidate in chat_candidates:
                    try:
                        chat_message_refreshed = await user.get_messages(chat_id=candidate, message_ids=message_id)
                        if chat_message_refreshed:
                            break
                    except Exception:
                        continue
            media_path = await chat_message_refreshed.download(
                file_name=download_path,
                progress=Leaves.progress_for_pyrogram,
                progress_args=progressArgs(
                    f"📥 Downloading Progress (Attempt {attempt}/{retries+1})", progress_message, start_time
                ),
            )
            media_type = (
                "photo" if chat_message_refreshed.photo else
                "video" if chat_message_refreshed.video else
                "audio" if chat_message_refreshed.audio else
                "document"
            )
            await send_media(
                bot,
                message,
                media_path,
                media_type,
                parsed_caption,
                progress_message,
                start_time,
            )
            cleanup_download(media_path)
            if chosen_chat_id is not None:
                _mark_download(chosen_chat_id, message_id)
            await progress_message.delete()
            return "downloaded"
        except Exception as e:
            last_error = e
            LOGGER(__name__).info(f"Download attempt {attempt} failed for {post_url}: {e}")
            await asyncio.sleep(min(5, attempt * 2))
            continue
        except asyncio.CancelledError:
            await progress_message.delete()
            LOGGER(__name__).info(f"Cancelled download {post_url}")
            return "skipped"

    await progress_message.delete()
    # Decide skipped vs failed: treat typical file ref issues as skipped
    error_text = str(last_error) if last_error else "Unknown error"
    if any(k in error_text for k in ["FILE_REFERENCE_", "MEDIA_EMPTY", "ENTITY_BOUNDS"]):
        result = "skipped"
    else:
        result = "failed"
    if lock_key and lock_obj:
        _release_lock(lock_key, lock_obj)
    return result


@bot.on_message(filters.command("dl") & (filters.private | filters.group))
async def download_media(bot: Client, message: Message):
    if len(message.command) < 2:
        await message.reply("**Provide a post URL after the /dl command.**")
        return

    post_url = message.command[1]
    await track_task(handle_download(bot, message, post_url))


@bot.on_message(filters.command("bdl") & (filters.private | filters.group))
async def download_range(bot: Client, message: Message):
    args = message.text.split()

    if len(args) != 3 or not all(arg.startswith("https://t.me/") for arg in args[1:]):
        await message.reply(
            "🚀 **Batch Download Process**\n"
            "`/bdl start_link end_link`\n\n"
            "💡 **Example:**\n"
            "`/bdl https://t.me/mychannel/100 https://t.me/mychannel/120`"
        )
        return

    try:
        start_candidates, start_id = getChatMsgID(args[1])
        end_candidates,   end_id   = getChatMsgID(args[2])
    except Exception as e:
        return await message.reply(f"**❌ Error parsing links:\n{e}**")

    # Ensure there is at least one overlapping candidate
    overlap = [c for c in start_candidates if c in end_candidates]
    if not overlap:
        return await message.reply("**❌ Both links must be from the same channel (no overlap after normalization).**")
    # Use first overlapping as primary; keep entire candidate list for fallback fetches
    primary_candidates = overlap + [c for c in start_candidates if c not in overlap]
    if start_id > end_id:
        return await message.reply("**❌ Invalid range: start ID cannot exceed end ID.**")

    # Preload chat (best effort) using first candidate
    try:
        await user.get_chat(primary_candidates[0])
    except Exception:
        pass

    prefix = args[1].rsplit("/", 1)[0]
    global ACTIVE_BATCH_JOB
    if ACTIVE_BATCH_JOB and ACTIVE_BATCH_JOB.active:
        return await message.reply("**❌ A batch is already running. Pause or wait until it finishes.**")

    # Create and start job
    job_name = f"batch_{int(time())}"
    ACTIVE_BATCH_JOB = BatchJob(
        name=job_name,
        start_id=start_id,
        end_id=end_id,
        prefix=prefix,
        candidates=primary_candidates,
        chat_id=message.chat.id,
        start_url=args[1],
        end_url=args[2],
        initiator_id=message.from_user.id if message.from_user else 0,
    )
    loading = await message.reply(f"📥 **Downloading posts {start_id}–{end_id}… (job: {job_name})**")

    async def _run_batch(job: BatchJob, loading_msg: Message):
        try:
            while job.next_id <= job.end_id and not job.paused and not CANCEL_EVENT.is_set():
                msg_id = job.next_id
                url = f"{job.prefix}/{msg_id}"
                try:
                    status = await handle_download_status(bot, message, url)
                    if status == "downloaded":
                        job.downloaded += 1
                    elif status == "skipped":
                        job.skipped += 1
                    else:
                        job.failed += 1
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    job.failed += 1
                    LOGGER(__name__).error(f"Unhandled error at {url}: {e}")
                finally:
                    job.next_id += 1
                    job.updated_at = time()
                await asyncio.sleep(3)

            await loading_msg.delete()

            if job.paused:
                job.active = False
                PAUSED_JOBS[job.name] = job
                await message.reply(
                    "**⏸️ Batch Paused**\n"
                    f"Name: `{job.name}`\n"
                    f"Next ID: `{job.next_id}` of `{job.end_id}`\n"
                    f"Downloaded: `{job.downloaded}` | Skipped: `{job.skipped}` | Failed: `{job.failed}`\n"
                    f"Resume with `/continue {job.name}`"
                )
            else:
                summary = (
                    "**✅ Batch Process Complete!**\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"📥 **Downloaded** : `{job.downloaded}` post(s)\n"
                    f"⏭️ **Skipped**    : `{job.skipped}` (no content)\n"
                    f"❌ **Failed**     : `{job.failed}` error(s)"
                )
                await message.reply(summary)
        finally:
            # Clear active job if finished or paused
            if ACTIVE_BATCH_JOB is job:
                ACTIVE_BATCH_JOB = None

    track_task(_run_batch(ACTIVE_BATCH_JOB, loading))
    await message.reply(f"**🚀 Batch started.** Use `/pause [name]` to pause.")


@bot.on_message(filters.command("pause") & (filters.private | filters.group))
async def pause_batch(_, message: Message):
    global ACTIVE_BATCH_JOB, _pause_name_counter
    if not ACTIVE_BATCH_JOB or not ACTIVE_BATCH_JOB.active:
        return await message.reply("**No active batch to pause.**")
    # Only initiator (or chat admins?) For simplicity - allow initiator or same chat
    if message.from_user and ACTIVE_BATCH_JOB.initiator_id and message.from_user.id != ACTIVE_BATCH_JOB.initiator_id:
        return await message.reply("**Only the user who started the batch can pause it.**")

    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        desired_name = parts[1].strip()
    else:
        _pause_name_counter += 1
        desired_name = f"pause{_pause_name_counter}"
    if desired_name in PAUSED_JOBS:
        return await message.reply("**Name already used for a paused batch. Choose another.**")

    # Flag pause; loop will persist state after current item
    ACTIVE_BATCH_JOB.paused = True
    ACTIVE_BATCH_JOB.name = desired_name
    await message.reply(f"**Pausing batch...** Will store as `{desired_name}` shortly.")


@bot.on_message(filters.command("continue") & (filters.private | filters.group))
async def continue_batch(_, message: Message):
    global ACTIVE_BATCH_JOB
    if ACTIVE_BATCH_JOB and ACTIVE_BATCH_JOB.active:
        return await message.reply("**A batch is already running. Pause or wait until it finishes.**")

    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        name = parts[1].strip()
        job = PAUSED_JOBS.get(name)
        if not job:
            available = ", ".join(PAUSED_JOBS.keys()) or "(none)"
            return await message.reply(f"**Unknown paused name. Available:** {available}")
    else:
        # Pick most recently updated paused job
        if not PAUSED_JOBS:
            return await message.reply("**No paused batches available.**")
        job = max(PAUSED_JOBS.values(), key=lambda j: j.updated_at)
        name = job.name

    if job.next_id > job.end_id:
        return await message.reply("**This batch already finished. Start a new one.**")

    # Remove from paused and reactivate
    PAUSED_JOBS.pop(job.name, None)
    job.paused = False
    job.active = True
    ACTIVE_BATCH_JOB = job
    remaining = job.end_id - job.next_id + 1
    loading = await message.reply(f"▶️ **Resuming `{name}`** at `{job.next_id}` (remaining {remaining})")

    async def _resume(job: BatchJob, loading_msg: Message):
        try:
            while job.next_id <= job.end_id and not job.paused and not CANCEL_EVENT.is_set():
                msg_id = job.next_id
                url = f"{job.prefix}/{msg_id}"
                try:
                    status = await handle_download_status(bot, message, url)
                    if status == "downloaded":
                        job.downloaded += 1
                    elif status == "skipped":
                        job.skipped += 1
                    else:
                        job.failed += 1
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    job.failed += 1
                    LOGGER(__name__).error(f"Unhandled error at {url}: {e}")
                finally:
                    job.next_id += 1
                    job.updated_at = time()
                await asyncio.sleep(3)

            await loading_msg.delete()
            if job.paused:
                PAUSED_JOBS[job.name] = job
                await message.reply(
                    f"**⏸️ Re-paused `{job.name}`** at `{job.next_id}`. Resume with `/continue {job.name}`"
                )
            else:
                summary = (
                    "**✅ Batch Process Complete!**\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"📥 **Downloaded** : `{job.downloaded}` post(s)\n"
                    f"⏭️ **Skipped**    : `{job.skipped}` (no content)\n"
                    f"❌ **Failed**     : `{job.failed}` error(s)"
                )
                await message.reply(summary)
        finally:
            if ACTIVE_BATCH_JOB is job:
                ACTIVE_BATCH_JOB = None

    track_task(_resume(job, loading))
    await message.reply(f"**Resumed `{name}`.** Use `/pause` again to pause.")


@bot.on_message(filters.command("stats") & (filters.private | filters.group))
async def stats(_, message: Message):
    currentTime = get_readable_time(time() - PyroConf.BOT_START_TIME)
    total, used, free = shutil.disk_usage(".")
    total = get_readable_file_size(total)
    used = get_readable_file_size(used)
    free = get_readable_file_size(free)
    sent = get_readable_file_size(psutil.net_io_counters().bytes_sent)
    recv = get_readable_file_size(psutil.net_io_counters().bytes_recv)
    cpuUsage = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    process = psutil.Process(os.getpid())

    stats = (
        "**≧◉◡◉≦ Bot is Up and Running successfully.**\n\n"
        f"**➜ Bot Uptime:** `{currentTime}`\n"
        f"**➜ Total Disk Space:** `{total}`\n"
        f"**➜ Used:** `{used}`\n"
        f"**➜ Free:** `{free}`\n"
        f"**➜ Memory Usage:** `{round(process.memory_info()[0] / 1024**2)} MiB`\n\n"
        f"**➜ Upload:** `{sent}`\n"
        f"**➜ Download:** `{recv}`\n\n"
        f"**➜ CPU:** `{cpuUsage}%` | "
        f"**➜ RAM:** `{memory}%` | "
        f"**➜ DISK:** `{disk}%`"
    )
    await message.reply(stats)


@bot.on_message(filters.command("logs") & (filters.private | filters.group))
async def logs(_, message: Message):
    if os.path.exists("logs.txt"):
        await message.reply_document(document="logs.txt", caption="**Logs**")
    else:
        await message.reply("**Not exists**")


@bot.on_message(filters.command("killall") & (filters.private | filters.group))
async def cancel_all_tasks(_, message: Message):
    if not RUNNING_TASKS and not CANCEL_EVENT.is_set():
        return await message.reply("**No active tasks.**")
    cancel_all_running()
    await message.reply("**⛔ Cancellation requested. Stopping active downloads...**")
    # Allow tasks to process cancellation
    await asyncio.sleep(1)
    reset_cancellation()
    pending = sum(1 for t in RUNNING_TASKS if not t.done())
    await message.reply(f"**✅ Cancellation complete. Remaining active tasks: {pending}.**")


@bot.on_message(
    (filters.private | filters.group)
    & ~filters.command(["start","help","dl","bdl","stats","logs","killall"])
)
async def handle_any_message(bot: Client, message: Message):
    if message.text and not message.text.startswith("/"):
        await track_task(handle_download(bot, message, message.text))


if __name__ == "__main__":
    async def _main():
        LOGGER(__name__).info("Starting clients...")
        await user.start()
        LOGGER(__name__).info("User client started (is_connected=%s)", getattr(user, 'is_connected', False))
        await bot.start()
        LOGGER(__name__).info("Bot client started (is_connected=%s)", getattr(bot, 'is_connected', False))
        # Ensure no lingering webhook (Heroku should use long polling)
        try:
            await bot.delete_webhook(True)
            LOGGER(__name__).info("Cleared webhook (long polling mode)")
        except Exception as e:
            LOGGER(__name__).warning(f"Webhook clear failed: {e}")
        try:
            await bot.set_bot_commands([BotCommand(c, d[:256]) for c, d in BOT_COMMANDS])
            LOGGER(__name__).info("Bot commands registered")
        except Exception as e:
            LOGGER(__name__).error(f"Failed to register commands: {e}")
        LOGGER(__name__).info("Entering idle state")

        stop_event = asyncio.Event()

        def _handle_sig(*_):
            LOGGER(__name__).info("Signal received, initiating graceful shutdown")
            stop_event.set()
            CANCEL_EVENT.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                asyncio.get_running_loop().add_signal_handler(sig, _handle_sig)
            except NotImplementedError:
                pass
        idle_start = time()
        try:
            # Run idle until stop_event triggered
            idle_task = asyncio.create_task(idle())
            await asyncio.wait(
                {idle_task, asyncio.create_task(stop_event.wait())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not idle_task.done():
                idle_task.cancel()
            # If idle returns almost immediately (<2s), log diagnostic to help debugging Heroku loop issue
            if time() - idle_start < 2:
                LOGGER(__name__).warning("idle() returned too quickly (%.2fs) – clients may have disconnected early.", time() - idle_start)
        finally:
            LOGGER(__name__).info("Shutting down...")
            # Guarded stop to avoid cross-loop RuntimeError seen on Heroku
            for c, label in ((bot, 'bot'), (user, 'user')):
                try:
                    if getattr(c, 'is_connected', False):
                        await c.stop()
                        LOGGER(__name__).info("Stopped %s client", label)
                except RuntimeError as re:
                    if 'attached to a different loop' in str(re):
                        LOGGER(__name__).warning("Ignoring loop mismatch stopping %s: %s", label, re)
                    else:
                        LOGGER(__name__).error("Error stopping %s: %s", label, re)
                except Exception as e:
                    LOGGER(__name__).error("Unexpected error stopping %s: %s", label, e)
            LOGGER(__name__).info("Bot Stopped")

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        LOGGER(__name__).info("Interrupted by user")

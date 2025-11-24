import os
import logging
import random
import json
from pathlib import Path

from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from collections import deque

from telegram import Update, MessageEntity
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from telegram.ext import CommandHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def sanitize_log_value(s: str) -> str:
    """Sanitize string for safe logging: remove newlines and carriage returns."""
    if not isinstance(s, str):
        s = str(s)
    return s.replace('\r\n', '').replace('\n', '').replace('\r', '')
BOT_VERSION = "0.0.2"

# Per-chat config: whether to delete original messages
DELETE_ORIGINAL_BY_CHAT: dict[int, bool] = {}
CONFIG_FILE = Path(os.environ.get("CLEANLINKS_CONFIG_FILE", "cleanlinks_config.json"))

FUNNY_INTROS = [
    "🪲 Użyłem sprayu na wścibskie pluskwy",
    "🦠 Odkaziłem tę wiadomość z brudu marketingowego",
    "🧼 Zeskrobałem tracking jak starą farbę z okna",
    "🫧 Przepuściłem link przez pralkę na 90°C",
    "🐛 Wyczesałem wszy z tego URL-a",
    "🧹 Usunąłem cyfrowe glutki, proszę bardzo",
    "🥛 Wyprałem ten link w sodzie oczyszczonej",
    "🪱 Pozbawiłem ten link pasożytów śledzących",
    "🫧 Odrdzewiłem i wypolerowałem go na błysk",
    "🗑️  Wyrzuciłem śledzące robaczki do kosza",
]

DEDUP_CACHE_SIZE = 10
processed_queue = deque(maxlen=DEDUP_CACHE_SIZE)

def is_new_message(mid):
    if mid in processed_queue:
        return False
    processed_queue.append(mid)
    return True

# ------------ URL CLEANING LOGIC ------------ #

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
}

TWITTER_HOSTS = {
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "x.com",
    "www.x.com",
}

# For YouTube, we keep only parameters that actually affect video playback.
YOUTUBE_ALLOWED_PARAMS = {"v", "t", "time_continue", "list", "index"}

def load_config() -> None:
    """Load per-chat config from JSON file (if it exists)."""
    global DELETE_ORIGINAL_BY_CHAT

    if not CONFIG_FILE.is_file():
        logger.info("Config file %s not found, starting with defaults.", sanitize_log_value(CONFIG_FILE))
        DELETE_ORIGINAL_BY_CHAT = {}
        return

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        raw = data.get("delete_original_by_chat", {})
        # JSON keys are strings → convert to int
        DELETE_ORIGINAL_BY_CHAT = {int(k): bool(v) for k, v in raw.items()}

        logger.info("Loaded config from %s: %s", sanitize_log_value(CONFIG_FILE), DELETE_ORIGINAL_BY_CHAT)
    except Exception as e:
        logger.warning("Failed to load config from %s: %s", sanitize_log_value(CONFIG_FILE), e)
        DELETE_ORIGINAL_BY_CHAT = {}


def save_config() -> None:
    """Save per-chat config to JSON file (atomic write)."""
    try:
        data = {
            "delete_original_by_chat": {
                str(chat_id): bool(flag)
                for chat_id, flag in DELETE_ORIGINAL_BY_CHAT.items()
            }
        }

        tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        tmp.replace(CONFIG_FILE)
        logger.info("Saved config to %s", sanitize_log_value(CONFIG_FILE))
    except Exception as e:
        logger.warning("Failed to save config to %s: %s", sanitize_log_value(CONFIG_FILE), e)

def clean_youtube(url: str) -> str:
    parsed = urlparse(url)

    # Short links like https://youtu.be/VIDEOID?t=123&si=...
    if parsed.netloc in {"youtu.be"}:
        query = parse_qsl(parsed.query, keep_blank_values=True)
        # keep only timestamp "t" if present, drop everything else (si, utm_*, etc.)
        filtered = [(k, v) for (k, v) in query if k in {"t"}]
        new_query = urlencode(filtered, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # Regular youtube.com links
    query = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in query if k in YOUTUBE_ALLOWED_PARAMS]

    new_query = urlencode(filtered, doseq=True)

    return urlunparse(parsed._replace(query=new_query))


def clean_twitter(url: str) -> str:
    parsed = urlparse(url)
    # For Twitter/X, we can safely drop all query params (they're mostly tracking)
    return urlunparse(parsed._replace(query=""))


def clean_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return url  # if parse fails, leave as-is

    host = parsed.netloc.lower()

    if host in YOUTUBE_HOSTS:
        return clean_youtube(url)
    if host in TWITTER_HOSTS:
        return clean_twitter(url)

    # Not YouTube or Twitter/X -> leave unchanged
    return url


def extract_urls(text: str, entities: list[MessageEntity]) -> list[tuple[str, MessageEntity]]:
    """
    Return list of (url, entity) pairs from a message text using URL & TEXT_LINK entities.
    """
    urls = []
    for ent in entities:
        if ent.type == MessageEntity.URL:
            url = text[ent.offset : ent.offset + ent.length]
            urls.append((url, ent))
        elif ent.type == MessageEntity.TEXT_LINK and ent.url:
            urls.append((ent.url, ent))
    return urls


# ------------ TELEGRAM HANDLER ------------ #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return

    # Deduplication
    mid = getattr(message, "message_id", None)
    if mid is not None and not is_new_message(mid):
        logger.info(f"Already processed message_id {mid}, skipping.")
        return

    # Avoid reacting to our own messages
    if message.from_user and message.from_user.is_bot:
        return

    text = message.text or message.caption
    if not text:
        return

    entities = message.entities or message.caption_entities or []
    url_entities = extract_urls(text, entities)

    if not url_entities:
        return

    cleaned_mapping = {}
    for original_url, ent in url_entities:
        cleaned = clean_url(original_url)
        if cleaned != original_url:
            cleaned_mapping[original_url] = cleaned

    if not cleaned_mapping:
        return  # nothing to change

    # Build cleaned text by replacing URLs (only for URL entities)
    cleaned_text = text
    for original_url, ent in sorted(url_entities, key=lambda x: x[1].offset, reverse=True):
        if ent.type == MessageEntity.URL:
            cleaned = cleaned_mapping.get(original_url, original_url)
            cleaned_text = (
                cleaned_text[: ent.offset]
                + cleaned
                + cleaned_text[ent.offset + ent.length :]
            )

    # TEXT_LINK entities: append cleaned versions at the end
    extra_cleaned_links = [
        v
        for (orig, ent) in url_entities
        if ent.type == MessageEntity.TEXT_LINK and (v := cleaned_mapping.get(orig))
    ]

    if extra_cleaned_links:
        cleaned_text += "\n\nCleaned links:\n" + "\n".join(extra_cleaned_links)

    # If nothing effectively changed, bail
    if cleaned_text == text and not extra_cleaned_links:
        return

    # ---- Attribution with funny intro ----
    user = message.from_user
    if user:
        if user.username:
            author = f"@{user.username}"
        elif user.full_name:
            author = user.full_name
        else:
            author = "ktoś"
    else:
        author = "ktoś"

    intro = random.choice(FUNNY_INTROS)
    final_text = f"{intro}\nOd {author}\n{cleaned_text}"

    # ---- Per-chat behavior: delete original or not ----
    delete_original = DELETE_ORIGINAL_BY_CHAT.get(message.chat_id, False)

    if delete_original:
        # Try to delete original, then post cleaned as a fresh message
        try:
            await message.delete()
        except Exception as e:
            logger.warning("Failed to delete message: %s", e)

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=final_text,
        )
    else:
        # Default: keep original and reply with cleaned version
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=final_text,
            reply_to_message_id=message.message_id,
        )


async def ping(update, context):
    await update.effective_message.reply_text(f"pong (Wersja {BOT_VERSION})")

async def help_command(update, context):
    HELP_TEXT = (
        "🤖 *Pomoc Clean Links Bot*\n\n"
        "Ten bot skanuje wiadomości pod kątem linków do YouTube oraz Twitter/X i usuwa z nich zbędne lub śledzące parametry.\n"
        "Jeśli link da się oczyścić, bot odpowiada w wątku z oczyszczoną wersją oraz zabawnym intro.\n\n"
        "*Komendy:*\n"
        "/ping – Sprawdź czy bot żyje i poznaj jego wersję\n"
        "/help – Wyświetl tę pomoc\n\n"
        "*Jak działa bot:*\n"
        "- Działa tylko na czatach grupowych\n"
        "- Automatycznie odpowiada, jeśli wykryje możliwy do poprawienia link do YouTube lub Twitter/X\n"
        "- Podaje autora oryginalnej wiadomości\n"
        "- Używa pamięci podręcznej, by nie odpowiadać dwa razy na ten sam komunikat\n"
    )
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def set_delete_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    # This command only makes sense in groups
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Tę komendę możesz użyć tylko w grupie."
        )
        return

    # Only admins can change the setting
    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status not in ("administrator", "creator", "owner"):
        await update.effective_message.reply_text(
            "Tylko administratorzy tej grupy mogą zmieniać ten tryb."
        )
        return

    # Expect one argument: on/off (true/false/1/0 etc.)
    if not context.args:
        current = DELETE_ORIGINAL_BY_CHAT.get(chat.id, False)
        mode_txt = "włączone" if current else "wyłączone"
        await update.effective_message.reply_text(
            f"Usuwanie oryginalnych wiadomości jest teraz: {mode_txt}.\n"
            "Użyj: /cleanlinks_delete on lub /cleanlinks_delete off"
        )
        return

    arg = context.args[0].lower()
    if arg in ("on", "true", "1", "yes", "tak"):
        DELETE_ORIGINAL_BY_CHAT[chat.id] = True
        save_config()
        await update.effective_message.reply_text(
            "OK, od teraz będę USUWAŁ oryginalne wiadomości po wyczyszczeniu linków."
        )
    elif arg in ("off", "false", "0", "no", "nie"):
        DELETE_ORIGINAL_BY_CHAT[chat.id] = False
        save_config()
        await update.effective_message.reply_text(
            "OK, od teraz NIE będę usuwał oryginalnych wiadomości – tylko odpowiadam czystą wersją."
        )
    else:
        await update.effective_message.reply_text(
            "Nie rozumiem. Użyj: /cleanlinks_delete on lub /cleanlinks_delete off"
        )

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Please set TELEGRAM_BOT_TOKEN env variable.")

    # Load persisted config
    load_config()

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("help", help_command))

    # Command to configure delete mode (admins only)
    app.add_handler(CommandHandler("cleanlinks_delete", set_delete_mode))

    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
            handle_message,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()

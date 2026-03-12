"""
Discord Queue Auto-Joiner + Telegram Notifier
=============================================
Watches a Discord channel for queue messages, auto-clicks Join Queue.
If the queue is full (max 20), it stops and waits for the queue to reset.

SETUP:
  pip install discord.py-self requests python-dotenv

USAGE:
  1. Fill in your .env file
  2. python discord_queue_bot.py"""
Discord Queue Auto-Joiner + Telegram Notifier
=============================================
Watches a Discord channel for queue messages, auto-clicks Join Queue.
If the queue is full (max 20), it stops and waits for the queue to reset.

SETUP:
  pip install discord.py-self requests python-dotenv

USAGE:
  1. Fill in your .env file
  2. python discord_queue_bot.py

⚠️  WARNING: This uses a selfbot (user account token).
    This violates Discord's ToS. Use at your own risk.
"""

import re
import asyncio
import requests
import discord  # discord.py-self  (NOT regular discord.py)
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN", "YOUR_USER_TOKEN_HERE")
WATCH_CHANNEL_ID   = int(os.getenv("WATCH_CHANNEL_ID", "0"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
YOUR_USER_ID       = int(os.getenv("YOUR_USER_ID", "962048694293762108"))

# Keywords that indicate a queue message (case-insensitive)
QUEUE_KEYWORDS     = ["queue", "join queue", "waiting list", "spot", "position"]

# Button labels to look for and click (case-insensitive, partial match)
JOIN_BUTTON_LABELS = ["join queue", "join", "enter queue", "enter"]
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def send_telegram(message: str):
    """Send a Telegram message via Bot API."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        log("⚠️  Telegram not configured, skipping notification.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 200:
            log(f"📱 Telegram sent: {message[:60]}")
        else:
            log(f"❌ Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        log(f"❌ Telegram exception: {e}")


def is_queue_message(message: discord.Message) -> bool:
    """Return True if the message looks like a queue message."""
    content_lower = message.content.lower()
    if any(kw in content_lower for kw in QUEUE_KEYWORDS):
        return True
    for embed in message.embeds:
        embed_text = f"{embed.title or ''} {embed.description or ''}".lower()
        if any(kw in embed_text for kw in QUEUE_KEYWORDS):
            return True
    for component in message.components:
        for child in (component.children if hasattr(component, "children") else [component]):
            if hasattr(child, "label") and child.label:
                if any(lbl in child.label.lower() for lbl in JOIN_BUTTON_LABELS):
                    return True
    return False


def find_join_button(message: discord.Message):
    """Find and return the Join Queue button, or None."""
    for component in message.components:
        children = component.children if hasattr(component, "children") else [component]
        for child in children:
            if hasattr(child, "label") and child.label:
                if any(lbl in child.label.lower() for lbl in JOIN_BUTTON_LABELS):
                    return child
    return None


def user_is_mentioned(message: discord.Message, user_id: int) -> bool:
    """
    Check if the user ID appears anywhere in the message.
    Handles both <@USER_ID> and <@!USER_ID> formats.
    """
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"

    uid = str(user_id)
    # Match <@962...> and <@!962...>
    return bool(re.search(rf"<@!?{uid}>", full_text))


def find_queue_position(message: discord.Message, user_id: int) -> int | None:
    """
    Scan message for a numbered list containing the user's mention.
    Handles formats like:
      1. <@!962048694293762108>
      2. <@962048694293762108>
    Returns the position number or None.
    """
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"

    uid = str(user_id)
    lines = full_text.split("\n")
    for line in lines:
        # Check if this line contains the user's mention
        if re.search(rf"<@!?{uid}>", line):
            # Extract the leading number from the line e.g. "5. <@!123>"
            match = re.search(r"(\d+)[.):\s]", line.strip())
            if match:
                return int(match.group(1))
    return None


# ── SELFBOT CLIENT ────────────────────────────────────────────────────────────

class QueueBot(discord.Client):

    def __init__(self):
        super().__init__()
        # States:
        #   "waiting"  — watching for queue to open
        #   "full"     — queue was full, waiting for reset
        #   "joined"   — successfully in the queue
        self.state         = "waiting"
        self.queue_msg_id  = None
        self.last_position = None
        self._clicking     = False  # lock to prevent double-clicks

    async def on_ready(self):
        log(f"✅ Logged in as {self.user} ({self.user.id})")
        log(f"👀 Watching channel ID: {WATCH_CHANNEL_ID}")
        send_telegram(
            f"🤖 <b>Queue bot is online!</b>\n"
            f"Watching SMP Tierlist queue channel.\n"
            f"Will auto-join and notify you."
        )

    # ── New message ──────────────────────────────────────────────────────────
    async def on_message(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return

        # Detect "queue is full" ephemeral reply after a failed click
        if self._is_full_response(message):
            if self.state != "full":
                log("🚫 Got 'queue is full' response — stopping until queue resets.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self._clicking = False
            return

        await self._process(message, is_edit=False)

    # ── Edited message ───────────────────────────────────────────────────────
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.channel.id != WATCH_CHANNEL_ID:
            return

        # ── KEY FEATURE: detect when our user ID appears in the queue list ──
        # This fires when the queue message is edited and now includes our mention
        if self.state == "waiting" and user_is_mentioned(after, YOUR_USER_ID):
            position = find_queue_position(after, YOUR_USER_ID)
            if position and position != self.last_position:
                self.last_position = position
                log(f"📊 Detected in queue at position #{position}")
                send_telegram(f"📊 <b>You are in the queue!</b>\nPosition: <b>#{position}</b>")
                self.state = "joined"
                self.queue_msg_id = after.id
            return

        # If already joined, track position changes
        if self.state == "joined" and user_is_mentioned(after, YOUR_USER_ID):
            position = find_queue_position(after, YOUR_USER_ID)
            if position and position != self.last_position:
                self.last_position = position
                log(f"📊 Position updated: #{position}")
                send_telegram(f"📊 <b>Queue position update:</b> #{position}")
            return

        await self._process(after, is_edit=True)

    # ── Message deleted ──────────────────────────────────────────────────────
    async def on_message_delete(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return
        if message.id == self.queue_msg_id:
            log("🗑️  Queue message deleted — resetting.")
            await self._reset()

    # ── Reaction added ───────────────────────────────────────────────────────
    async def on_reaction_add(self, reaction: discord.Reaction, user):
        if reaction.message.channel.id != WATCH_CHANNEL_ID:
            return
        if self.state == "full":
            log("🔔 Reaction on queue channel — checking if reopened.")
            await self._process(reaction.message, is_edit=True)

    def _is_full_response(self, message: discord.Message) -> bool:
        """Detect the ephemeral 'queue is full' message after a failed click."""
        text = message.content.lower()
        for embed in message.embeds:
            text += f" {embed.title or ''} {embed.description or ''}".lower()
        return any(phrase in text for phrase in [
            "queue is full",
            "try again later",
            "no spots available",
            "already full",
            "maximum capacity",
        ])

    # ── Core logic ───────────────────────────────────────────────────────────
    async def _process(self, message: discord.Message, is_edit: bool):

        # Already joined — nothing to do here (handled in on_message_edit)
        if self.state == "joined":
            return

        # Not a queue message — ignore
        if not is_queue_message(message):
            return

        log(f"🔔 Queue message {'edited' if is_edit else 'detected'} (ID: {message.id})")

        btn = find_join_button(message)

        # ── Queue was full — check if reopened ───────────────────────────────
        if self.state == "full":
            if btn and not btn.disabled:
                log("🔄 Queue reopened! Trying to join...")
                send_telegram("🔄 <b>Queue reopened!</b> Trying to join...")
                self.state = "waiting"
                # fall through to attempt join
            else:
                log("⏸️  Still full or no active button. Waiting.")
                return

        # ── Waiting — try to join ────────────────────────────────────────────
        if self.state == "waiting":
            if not btn:
                log("ℹ️  Queue message found but no Join button yet.")
                return

            if btn.disabled:
                log("🚫 Button disabled — queue full. Waiting for reset.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self.queue_msg_id = message.id
                return

            await self._click_join(message, btn)

    async def _click_join(self, message: discord.Message, button):
        """Attempt to click the Join Queue button — with lock to prevent double clicks."""
        if self._clicking:
            log("⏳ Already clicking, skipping duplicate.")
            return

        self._clicking = True
        log("🖱️  Clicking Join Queue...")
        try:
            await button.click()
            # Don't mark as joined yet — wait for the queue message to be edited
            # with our user ID in it (handled in on_message_edit)
            log("✅ Click sent! Waiting for queue message to confirm position...")
            self.queue_msg_id = message.id
            # Give it 5 seconds, if no confirmation assume joined without position
            await asyncio.sleep(5)
            if self.state != "joined":
                self.state = "joined"
                send_telegram(
                    f"✅ <b>Joined the queue!</b>\n"
                    f"Position: <b>checking...</b>\n"
                    f"Server: SMP Tierlist\n"
                    f"Channel: #{message.channel.name}"
                )

        except discord.errors.Forbidden:
            log("🚫 Forbidden — queue is full or no permission.")
            send_telegram(
                "🚫 <b>Queue is full (20/20)</b>\n"
                "Waiting for it to reset automatically."
            )
            self.state = "full"
            self.queue_msg_id = message.id

        except Exception as e:
            error_str = str(e).lower()
            if any(x in error_str for x in ["full", "maximum", "no spots", "queue is full"]):
                log(f"🚫 Queue full on click — waiting for reset. ({e})")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self.queue_msg_id = message.id
            else:
                log(f"❌ Unexpected error: {e}")
                send_telegram(f"❌ <b>Error clicking Join Queue:</b>\n{e}")
        finally:
            self._clicking = False

    async def _reset(self):
        """Reset state when queue ends."""
        old_state = self.state
        self.state = "waiting"
        self.queue_msg_id = None
        self.last_position = None
        self._clicking = False
        log(f"🔁 Reset to waiting (was: {old_state})")
        if old_state != "waiting":
            send_telegram("🏁 <b>Queue ended.</b> Watching for the next one.")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if WATCH_CHANNEL_ID == 0:
        print("❌ Please set WATCH_CHANNEL_ID in your .env file!")
        exit(1)
    if DISCORD_TOKEN == "YOUR_USER_TOKEN_HERE":
        print("❌ Please set DISCORD_TOKEN in your .env file!")
        exit(1)

    client = QueueBot()
    log("🚀 Starting Discord Queue Bot...")
    client.run(DISCORD_TOKEN)


⚠️  WARNING: This uses a selfbot (user account token).
    This violates Discord's ToS. Use at your own risk.
"""

import re
import asyncio
import requests
import discord  # discord.py-self  (NOT regular discord.py)
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN", "YOUR_USER_TOKEN_HERE")
WATCH_CHANNEL_ID   = int(os.getenv("WATCH_CHANNEL_ID", "0"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
YOUR_USER_ID       = int(os.getenv("YOUR_USER_ID", "962048694293762108"))

# Keywords that indicate a queue message (case-insensitive)
QUEUE_KEYWORDS     = ["queue", "join queue", "waiting list", "spot", "position"]

# Button labels to look for and click (case-insensitive, partial match)
JOIN_BUTTON_LABELS = ["join queue", "join", "enter queue", "enter"]
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def send_telegram(message: str):
    """Send a Telegram message via Bot API."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        log("⚠️  Telegram not configured, skipping notification.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 200:
            log(f"📱 Telegram sent: {message[:60]}")
        else:
            log(f"❌ Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        log(f"❌ Telegram exception: {e}")


def is_queue_message(message: discord.Message) -> bool:
    """Return True if the message looks like a queue message."""
    content_lower = message.content.lower()
    if any(kw in content_lower for kw in QUEUE_KEYWORDS):
        return True
    for embed in message.embeds:
        embed_text = f"{embed.title or ''} {embed.description or ''}".lower()
        if any(kw in embed_text for kw in QUEUE_KEYWORDS):
            return True
    for component in message.components:
        for child in (component.children if hasattr(component, "children") else [component]):
            if hasattr(child, "label") and child.label:
                if any(lbl in child.label.lower() for lbl in JOIN_BUTTON_LABELS):
                    return True
    return False


def find_join_button(message: discord.Message):
    """Find and return the Join Queue button, or None."""
    for component in message.components:
        children = component.children if hasattr(component, "children") else [component]
        for child in children:
            if hasattr(child, "label") and child.label:
                if any(lbl in child.label.lower() for lbl in JOIN_BUTTON_LABELS):
                    return child
    return None


def user_is_mentioned(message: discord.Message, user_id: int) -> bool:
    """
    Check if the user ID appears anywhere in the message.
    Handles both <@USER_ID> and <@!USER_ID> formats.
    """
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"

    uid = str(user_id)
    # Match <@962...> and <@!962...>
    return bool(re.search(rf"<@!?{uid}>", full_text))


def find_queue_position(message: discord.Message, user_id: int) -> int | None:
    """
    Scan message for a numbered list containing the user's mention.
    Handles formats like:
      1. <@!962048694293762108>
      2. <@962048694293762108>
    Returns the position number or None.
    """
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"

    uid = str(user_id)
    lines = full_text.split("\n")
    for line in lines:
        # Check if this line contains the user's mention
        if re.search(rf"<@!?{uid}>", line):
            # Extract the leading number from the line e.g. "5. <@!123>"
            match = re.search(r"(\d+)[.):\s]", line.strip())
            if match:
                return int(match.group(1))
    return None


# ── SELFBOT CLIENT ────────────────────────────────────────────────────────────

class QueueBot(discord.Client):

    def __init__(self):
        super().__init__()
        # States:
        #   "waiting"  — watching for queue to open
        #   "full"     — queue was full, waiting for reset
        #   "joined"   — successfully in the queue
        self.state         = "waiting"
        self.queue_msg_id  = None
        self.last_position = None
        self._clicking     = False  # lock to prevent double-clicks

    async def on_ready(self):
        log(f"✅ Logged in as {self.user} ({self.user.id})")
        log(f"👀 Watching channel ID: {WATCH_CHANNEL_ID}")
        send_telegram(
            f"🤖 <b>Queue bot is online! V2! </b>\n"
            f"Watching SMP Tierlist queue channel.\n"
            f"Will auto-join and notify you."
        )

    # ── New message ──────────────────────────────────────────────────────────
    async def on_message(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return

        # Detect "queue is full" ephemeral reply after a failed click
        if self._is_full_response(message):
            if self.state != "full":
                log("🚫 Got 'queue is full' response — stopping until queue resets.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self._clicking = False
            return

        await self._process(message, is_edit=False)

    # ── Edited message ───────────────────────────────────────────────────────
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.channel.id != WATCH_CHANNEL_ID:
            return

        # ── KEY FEATURE: detect when our user ID appears in the queue list ──
        # This fires when the queue message is edited and now includes our mention
        if self.state == "waiting" and user_is_mentioned(after, YOUR_USER_ID):
            position = find_queue_position(after, YOUR_USER_ID)
            if position and position != self.last_position:
                self.last_position = position
                log(f"📊 Detected in queue at position #{position}")
                send_telegram(f"📊 <b>You are in the queue!</b>\nPosition: <b>#{position}</b>")
                self.state = "joined"
                self.queue_msg_id = after.id
            return

        # If already joined, track position changes
        if self.state == "joined" and user_is_mentioned(after, YOUR_USER_ID):
            position = find_queue_position(after, YOUR_USER_ID)
            if position and position != self.last_position:
                self.last_position = position
                log(f"📊 Position updated: #{position}")
                send_telegram(f"📊 <b>Queue position update:</b> #{position}")
            return

        await self._process(after, is_edit=True)

    # ── Message deleted ──────────────────────────────────────────────────────
    async def on_message_delete(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return
        if message.id == self.queue_msg_id:
            log("🗑️  Queue message deleted — resetting.")
            await self._reset()

    # ── Reaction added ───────────────────────────────────────────────────────
    async def on_reaction_add(self, reaction: discord.Reaction, user):
        if reaction.message.channel.id != WATCH_CHANNEL_ID:
            return
        if self.state == "full":
            log("🔔 Reaction on queue channel — checking if reopened.")
            await self._process(reaction.message, is_edit=True)

    def _is_full_response(self, message: discord.Message) -> bool:
        """Detect the ephemeral 'queue is full' message after a failed click."""
        text = message.content.lower()
        for embed in message.embeds:
            text += f" {embed.title or ''} {embed.description or ''}".lower()
        return any(phrase in text for phrase in [
            "queue is full",
            "try again later",
            "no spots available",
            "already full",
            "maximum capacity",
        ])

    # ── Core logic ───────────────────────────────────────────────────────────
    async def _process(self, message: discord.Message, is_edit: bool):

        # Already joined — nothing to do here (handled in on_message_edit)
        if self.state == "joined":
            return

        # Not a queue message — ignore
        if not is_queue_message(message):
            return

        log(f"🔔 Queue message {'edited' if is_edit else 'detected'} (ID: {message.id})")

        btn = find_join_button(message)

        # ── Queue was full — check if reopened ───────────────────────────────
        if self.state == "full":
            if btn and not btn.disabled:
                log("🔄 Queue reopened! Trying to join...")
                send_telegram("🔄 <b>Queue reopened!</b> Trying to join...")
                self.state = "waiting"
                # fall through to attempt join
            else:
                log("⏸️  Still full or no active button. Waiting.")
                return

        # ── Waiting — try to join ────────────────────────────────────────────
        if self.state == "waiting":
            if not btn:
                log("ℹ️  Queue message found but no Join button yet.")
                return

            if btn.disabled:
                log("🚫 Button disabled — queue full. Waiting for reset.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self.queue_msg_id = message.id
                return

            await self._click_join(message, btn)

    async def _click_join(self, message: discord.Message, button):
        """Attempt to click the Join Queue button — with lock to prevent double clicks."""
        if self._clicking:
            log("⏳ Already clicking, skipping duplicate.")
            return

        self._clicking = True
        log("🖱️  Clicking Join Queue...")
        try:
            await button.click()
            # Don't mark as joined yet — wait for the queue message to be edited
            # with our user ID in it (handled in on_message_edit)
            log("✅ Click sent! Waiting for queue message to confirm position...")
            self.queue_msg_id = message.id
            # Give it 5 seconds, if no confirmation assume joined without position
            await asyncio.sleep(5)
            if self.state != "joined":
                self.state = "joined"
                send_telegram(
                    f"✅ <b>Joined the queue!</b>\n"
                    f"Position: <b>checking...</b>\n"
                    f"Server: SMP Tierlist\n"
                    f"Channel: #{message.channel.name}"
                )

        except discord.errors.Forbidden:
            log("🚫 Forbidden — queue is full or no permission.")
            send_telegram(
                "🚫 <b>Queue is full (20/20)</b>\n"
                "Waiting for it to reset automatically."
            )
            self.state = "full"
            self.queue_msg_id = message.id

        except Exception as e:
            error_str = str(e).lower()
            if any(x in error_str for x in ["full", "maximum", "no spots", "queue is full"]):
                log(f"🚫 Queue full on click — waiting for reset. ({e})")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self.queue_msg_id = message.id
            else:
                log(f"❌ Unexpected error: {e}")
                send_telegram(f"❌ <b>Error clicking Join Queue:</b>\n{e}")
        finally:
            self._clicking = False

    async def _reset(self):
        """Reset state when queue ends."""
        old_state = self.state
        self.state = "waiting"
        self.queue_msg_id = None
        self.last_position = None
        self._clicking = False
        log(f"🔁 Reset to waiting (was: {old_state})")
        if old_state != "waiting":
            send_telegram("🏁 <b>Queue ended.</b> Watching for the next one.")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if WATCH_CHANNEL_ID == 0:
        print("❌ Please set WATCH_CHANNEL_ID in your .env file!")
        exit(1)
    if DISCORD_TOKEN == "YOUR_USER_TOKEN_HERE":
        print("❌ Please set DISCORD_TOKEN in your .env file!")
        exit(1)

    client = QueueBot()
    log("🚀 Starting Discord Queue Bot...")
    client.run(DISCORD_TOKEN)

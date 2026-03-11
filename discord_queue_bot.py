"""
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

# Your Discord user ID — used to find your position in the queue
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


def find_queue_position(message: discord.Message, user_id: int) -> int | None:
    """
    Scan the message content/embeds for a list of user mentions and return
    the 1-based position of user_id, or None if not found.
    Also handles plain text patterns like '1. <@123>' or '1) username'.
    """
    # Collect all text from the message
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"

    # Look for the user mention (<@USER_ID>) and find its line number
    lines = full_text.split("\n")
    for i, line in enumerate(lines, start=1):
        if str(user_id) in line:
            # Try to extract an explicit number from the line first
            match = re.search(r"^(\d+)[.):\s]", line.strip())
            if match:
                return int(match.group(1))
            # Otherwise use line index as position (rough estimate)
            return i
    return None


# ── SELFBOT CLIENT ────────────────────────────────────────────────────────────

class QueueBot(discord.Client):

    def __init__(self):
        super().__init__()
        # States:
        #   "waiting"  — no queue active, watching for one
        #   "full"     — tried to join but queue was full, waiting for reset
        #   "joined"   — successfully joined the queue
        self.state        = "waiting"
        self.queue_msg_id = None
        self.last_position = None  # track last known position to avoid duplicate alerts

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

        # Detect the "queue is full" ephemeral reply sent to us after clicking
        if self._is_full_response(message):
            if self.state != "full":
                log("🚫 Got 'queue is full' response — stopping until queue resets.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
            return

        await self._process(message, is_edit=False)

    # ── Edited message (queue resets / reopens often via edits) ─────────────
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.channel.id != WATCH_CHANNEL_ID:
            return
        await self._process(after, is_edit=True)

    # ── Message deleted ──────────────────────────────────────────────────────
    async def on_message_delete(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return
        if message.id == self.queue_msg_id:
            log("🗑️  Queue message deleted — resetting to waiting.")
            await self._reset()

    # ── Reaction added (queue bots often use reactions to signal open/reset) ─
    async def on_reaction_add(self, reaction: discord.Reaction, user):
        if reaction.message.channel.id != WATCH_CHANNEL_ID:
            return
        if self.state == "full":
            log(f"🔔 Reaction detected on queue channel — checking if queue reopened.")
            await self._process(reaction.message, is_edit=True)

    def _is_full_response(self, message: discord.Message) -> bool:
        """Detect the ephemeral 'queue is full' message sent after a failed click."""
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

        # Already in queue — just track position updates
        if self.state == "joined":
            await self._check_position(message)
            return

        # Not a queue message — ignore
        if not is_queue_message(message):
            return

        log(f"🔔 Queue message {'edited' if is_edit else 'detected'} (ID: {message.id})")

        btn = find_join_button(message)

        # ── Queue was full — check if it has reopened ────────────────────────
        if self.state == "full":
            if btn and not btn.disabled:
                log("🔄 Queue reopened! Trying to join...")
                send_telegram("🔄 <b>Queue reopened!</b> Trying to join...")
                self.state = "waiting"
                # fall through to attempt join
            else:
                log("⏸️  Queue still full or no active button. Continuing to wait.")
                return

        # ── Waiting — try to join ────────────────────────────────────────────
        if self.state == "waiting":
            if not btn:
                log("ℹ️  Queue message found but no Join button yet.")
                return

            if btn.disabled:
                log("🚫 Join button is disabled — queue is full (20/20). Waiting for reset.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self.queue_msg_id = message.id
                return

            # Button is active — try to click it
            await self._click_join(message, btn)

    async def _click_join(self, message: discord.Message, button):
        """Attempt to click the Join Queue button."""
        log("🖱️  Clicking Join Queue...")
        try:
            await button.click()
            self.state = "joined"
            self.queue_msg_id = message.id
            log("✅ Successfully joined the queue!")
            position = find_queue_position(message, YOUR_USER_ID)
            pos_text = f"#{position}" if position else "checking..."
            self.last_position = position
            send_telegram(
                f"✅ <b>Joined the queue!</b>\n"
                f"Your position: <b>{pos_text}</b>\n"
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
                log(f"❌ Unexpected error clicking button: {e}")
                send_telegram(f"❌ <b>Error clicking Join Queue:</b>\n{e}")

    async def _check_position(self, message: discord.Message):
        """Check if user's queue position is in the message and notify if changed."""
        position = find_queue_position(message, YOUR_USER_ID)
        if position is not None and position != self.last_position:
            self.last_position = position
            log(f"📊 Queue position updated: #{position}")
            send_telegram(f"📊 <b>Your queue position: #{position}</b>")

    async def _reset(self):
        """Reset state when queue message is deleted (queue ended)."""
        old_state = self.state
        self.state = "waiting"
        self.queue_msg_id = None
        self.last_position = None
        log(f"🔁 State reset to waiting (was: {old_state})")
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
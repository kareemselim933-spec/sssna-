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

WARNING: This uses a selfbot (user account token).
This violates Discord ToS. Use at your own risk.
"""

import re
import asyncio
import requests
import discord  # discord.py-self  (NOT regular discord.py)
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

# CONFIG
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN", "YOUR_USER_TOKEN_HERE")
WATCH_CHANNEL_ID   = int(os.getenv("WATCH_CHANNEL_ID", "0"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
YOUR_USER_ID       = int(os.getenv("YOUR_USER_ID", "962048694293762108"))

QUEUE_KEYWORDS     = ["queue", "join queue", "waiting list", "spot", "position"]
JOIN_BUTTON_LABELS = ["join queue", "join", "enter queue", "enter"]

# How long to wait after clicking before giving up on confirmation (seconds)
CONFIRM_TIMEOUT    = 15


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        log("Telegram not configured, skipping.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 200:
            log(f"Telegram sent: {message[:60]}")
        else:
            log(f"Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        log(f"Telegram exception: {e}")


def is_queue_message(message: discord.Message) -> bool:
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
    for component in message.components:
        children = component.children if hasattr(component, "children") else [component]
        for child in children:
            if hasattr(child, "label") and child.label:
                if any(lbl in child.label.lower() for lbl in JOIN_BUTTON_LABELS):
                    return child
    return None


def user_is_mentioned(message: discord.Message, user_id: int) -> bool:
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"
    uid = str(user_id)
    return bool(re.search(rf"<@!?{uid}>", full_text))


def find_queue_position(message: discord.Message, user_id: int) -> int | None:
    full_text = message.content
    for embed in message.embeds:
        full_text += f"\n{embed.title or ''}\n{embed.description or ''}"
        for field in embed.fields:
            full_text += f"\n{field.value}"
    uid = str(user_id)
    for line in full_text.split("\n"):
        if re.search(rf"<@!?{uid}>", line):
            match = re.search(r"(\d+)[.):\s]", line.strip())
            if match:
                return int(match.group(1))
    return None


def is_full_response(message: discord.Message) -> bool:
    text = message.content.lower()
    for embed in message.embeds:
        text += f" {embed.title or ''} {embed.description or ''}".lower()
    return any(phrase in text for phrase in [
        "queue is full", "try again later", "no spots available",
        "already full", "maximum capacity",
    ])


# SELFBOT CLIENT

class QueueBot(discord.Client):

    def __init__(self):
        super().__init__()
        # States:
        #   "waiting"  — watching for queue to open
        #   "clicking" — just clicked, IGNORING all events until confirmed or timeout
        #   "full"     — queue was full, waiting for reset
        #   "joined"   — successfully in the queue
        self.state         = "waiting"
        self.queue_msg_id  = None
        self.last_position = None

    async def on_ready(self):
        log(f"Logged in as {self.user} ({self.user.id})")
        log(f"Watching channel ID: {WATCH_CHANNEL_ID}")
        send_telegram(
            "🤖 <b>Queue bot is online! V2</b>\n"
            "Watching SMP Tierlist queue channel.\n"
            "Will auto-join and notify you."
        )

    # NEW MESSAGE
    async def on_message(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return

        # Detect "queue is full" ephemeral reply — only act on it if we just clicked
        if is_full_response(message):
            if self.state == "clicking":
                log("Queue is full response received after click — setting full.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
            return

        # Ignore everything while we are waiting for click confirmation
        if self.state == "clicking":
            return

        await self._process(message, is_edit=False)

    # EDITED MESSAGE
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.channel.id != WATCH_CHANNEL_ID:
            return

        # While clicking — ONLY look for our user ID appearing in the queue list
        # This is the confirmation that we actually joined
        if self.state == "clicking":
            if user_is_mentioned(after, YOUR_USER_ID):
                position = find_queue_position(after, YOUR_USER_ID)
                self.last_position = position
                self.state = "joined"
                self.queue_msg_id = after.id
                pos_text = f"#{position}" if position else "checking..."
                log(f"Confirmed in queue! Position: {pos_text}")
                send_telegram(
                    f"✅ <b>Joined the queue!</b>\n"
                    f"Position: <b>{pos_text}</b>\n"
                    f"Server: SMP Tierlist\n"
                    f"Channel: #{after.channel.name}"
                )
            return  # ignore everything else while clicking

        # Already joined — track position updates
        if self.state == "joined":
            if user_is_mentioned(after, YOUR_USER_ID):
                position = find_queue_position(after, YOUR_USER_ID)
                if position and position != self.last_position:
                    self.last_position = position
                    log(f"Position updated: #{position}")
                    send_telegram(f"📊 <b>Queue position update:</b> #{position}")
            return

        await self._process(after, is_edit=True)

    # MESSAGE DELETED
    async def on_message_delete(self, message: discord.Message):
        if message.channel.id != WATCH_CHANNEL_ID:
            return
        if message.id == self.queue_msg_id:
            log("Queue message deleted — resetting.")
            await self._reset()

    # REACTION ADDED
    async def on_reaction_add(self, reaction: discord.Reaction, user):
        if reaction.message.channel.id != WATCH_CHANNEL_ID:
            return
        if self.state == "full":
            log("Reaction on queue channel — checking if reopened.")
            await self._process(reaction.message, is_edit=True)

    # CORE LOGIC
    async def _process(self, message: discord.Message, is_edit: bool):
        if self.state in ("joined", "clicking"):
            return

        if not is_queue_message(message):
            return

        log(f"Queue message {'edited' if is_edit else 'detected'} (ID: {message.id})")

        btn = find_join_button(message)

        # Queue was full — check if reopened
        if self.state == "full":
            if btn and not btn.disabled:
                log("Queue reopened! Trying to join...")
                send_telegram("🔄 <b>Queue reopened!</b> Trying to join...")
                self.state = "waiting"
            else:
                log("Still full or no active button. Waiting.")
                return

        # Waiting — try to join
        if self.state == "waiting":
            if not btn:
                log("Queue message found but no Join button yet.")
                return

            if btn.disabled:
                log("Button disabled — queue full. Waiting for reset.")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
                self.queue_msg_id = message.id
                return

            await self._click_join(message, btn)

    async def _click_join(self, message: discord.Message, button):
        log("Clicking Join Queue...")
        # Set state to clicking BEFORE the await so no other events sneak through
        self.state = "clicking"
        self.queue_msg_id = message.id

        try:
            await button.click()
            log("Click sent! Waiting up to 15s for confirmation (user ID in queue list)...")

            # Wait for on_message_edit to confirm by switching state to "joined"
            for _ in range(CONFIRM_TIMEOUT):
                await asyncio.sleep(1)
                if self.state == "joined":
                    return  # confirmed via on_message_edit
                if self.state == "full":
                    return  # confirmed full via on_message

            # Timeout — we never saw our ID in the list
            # Could mean we joined but position tracking failed, or it silently failed
            log("Confirmation timeout. Checking if we are actually in the queue...")
            # Stay as joined to be safe — better to assume joined than to click again
            self.state = "joined"
            send_telegram(
                "✅ <b>Joined the queue!</b>\n"
                "Position: <b>not found</b> (may update soon)\n"
                f"Server: SMP Tierlist\n"
                f"Channel: #{message.channel.name}"
            )

        except discord.errors.Forbidden:
            log("Forbidden — queue is full or no permission.")
            send_telegram(
                "🚫 <b>Queue is full (20/20)</b>\n"
                "Waiting for it to reset automatically."
            )
            self.state = "full"

        except Exception as e:
            error_str = str(e).lower()
            if any(x in error_str for x in ["full", "maximum", "no spots", "queue is full"]):
                log(f"Queue full on click — waiting for reset. ({e})")
                send_telegram(
                    "🚫 <b>Queue is full (20/20)</b>\n"
                    "Waiting for it to reset automatically."
                )
                self.state = "full"
            else:
                log(f"Unexpected error: {e}")
                send_telegram(f"❌ <b>Error clicking Join Queue:</b>\n{e}")
                self.state = "waiting"

    async def _reset(self):
        old_state = self.state
        self.state = "waiting"
        self.queue_msg_id = None
        self.last_position = None
        log(f"Reset to waiting (was: {old_state})")
        if old_state != "waiting":
            send_telegram("🏁 <b>Queue ended.</b> Watching for the next one.")


# ENTRY POINT

if __name__ == "__main__":
    if WATCH_CHANNEL_ID == 0:
        print("Please set WATCH_CHANNEL_ID in your .env file!")
        exit(1)
    if DISCORD_TOKEN == "YOUR_USER_TOKEN_HERE":
        print("Please set DISCORD_TOKEN in your .env file!")
        exit(1)

    client = QueueBot()
    log("Starting Discord Queue Bot...")
    client.run(DISCORD_TOKEN)

"""Tiny Telegram relay for human-in-the-loop login: send the captcha image / prompts to the user
and wait for their replies (captcha text, OTP code)."""
import os
import time

import requests

from . import config

BOT = os.environ.get("TG_BOT_TOKEN")
CHAT = os.environ.get("TG_CHAT_ID")
API = "https://api.telegram.org/bot{token}/{method}"


class TelegramRelay:
    def __init__(self, bot=None, chat=None):
        self.bot = bot or BOT
        self.chat = chat or CHAT
        if not self.bot or not self.chat:
            raise RuntimeError("set TG_BOT_TOKEN and TG_CHAT_ID (e.g. in .env)")
        self._offset = None
        self._drain()  # ignore any backlog so we only read replies that come after a prompt

    def _url(self, method):
        return API.format(token=self.bot, method=method)

    def _drain(self):
        try:
            r = requests.get(self._url("getUpdates"), params={"timeout": 0}, timeout=10).json()
            ups = r.get("result", [])
            if ups:
                self._offset = ups[-1]["update_id"] + 1
        except Exception:
            pass

    def send_text(self, text):
        requests.post(self._url("sendMessage"),
                      data={"chat_id": self.chat, "text": text}, timeout=20)

    def send_photo(self, path, caption=""):
        with open(path, "rb") as f:
            requests.post(self._url("sendPhoto"),
                          data={"chat_id": self.chat, "caption": caption},
                          files={"photo": f}, timeout=30)

    def wait_reply(self, timeout=300):
        """Block until the user sends a text message; return its text (trimmed)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(self._url("getUpdates"),
                                 params={"timeout": 25, "offset": self._offset},
                                 timeout=30).json()
            except Exception:
                time.sleep(2); continue
            for up in r.get("result", []):
                self._offset = up["update_id"] + 1
                msg = up.get("message") or up.get("edited_message") or {}
                if str(msg.get("chat", {}).get("id")) != str(self.chat):
                    continue
                txt = (msg.get("text") or "").strip()
                if txt:
                    return txt
        raise TimeoutError("no Telegram reply within timeout")

    def ask(self, prompt, timeout=300):
        self.send_text(prompt)
        return self.wait_reply(timeout)

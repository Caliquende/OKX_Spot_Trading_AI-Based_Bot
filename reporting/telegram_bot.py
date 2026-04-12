from __future__ import annotations

"""
TELEGRAM TRANSPORT KATMANI
==========================

Bu dosya mesaj gönderme ve update polling işini taşır.
Karar üretmez; sadece Telegram API ile konuşur.

Komutların yorumlanması main.py içindedir. Burada sadece update'ler çekilir ve normalize edilir.
"""

from typing import Any
import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        # Token = bot kimliği, chat_id = mesajın gideceği sohbet.
        self.token = token
        self.chat_id = str(chat_id)

    @property
    def enabled(self) -> bool:
        # Gerekli bilgiler doluysa Telegram aktif sayılır.
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        # Telegram'a tek mesaj gönderir.
        if not self.enabled:
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:4000]},
                timeout=10,
            )
            return r.ok
        except Exception:
            return False

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict[str, Any]]:
        # Telegram'dan yeni mesaj/update çek.
        if not self.enabled:
            return []
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params=payload,
                timeout=max(timeout + 5, 10),
            )
            if not r.ok:
                return []
            data = r.json()
            if not data.get("ok"):
                return []
            results = data.get("result") or []
            return results if isinstance(results, list) else []
        except Exception:
            return []

    def poll_commands(self, offset: int | None = None, timeout: int = 0) -> tuple[list[dict[str, Any]], int | None]:
        # Gelen update'ler içinden sadece slash komutlarını ayıklar.
        updates = self.get_updates(offset=offset, timeout=timeout)
        commands: list[dict[str, Any]] = []
        next_offset = offset

        for update in updates:
            update_id = int(update.get("update_id") or 0)
            next_offset = update_id + 1

            message = update.get("message") or update.get("edited_message") or {}
            text = str(message.get("text") or "").strip()
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "")

            if not text:
                continue
            if self.chat_id and chat_id != self.chat_id:
                continue
            if not text.startswith("/"):
                continue

            commands.append(
                {
                    "update_id": update_id,
                    "chat_id": chat_id,
                    "text": text,
                    "message": message,
                }
            )

        return commands, next_offset

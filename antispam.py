import time
from collections import defaultdict, deque
import logging
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message

log = logging.getLogger("shmonza2bot.antispam")

class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, limit=6, interval=8):
        self.limit, self.interval = limit, interval
        self.events = defaultdict(deque)

    async def __call__(self, handler, event: TelegramObject, data: dict):
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)
        q = self.events[event.from_user.id]
        now = time.monotonic()
        while q and now - q[0] > self.interval:
            q.popleft()
        if len(q) >= self.limit:
            log.warning("rate_limited user_id=%s", event.from_user.id)
            await event.answer("⏳ Слишком много сообщений. Подожди немного.")
            return
        q.append(now)
        return await handler(event, data)

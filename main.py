"""
Точка входа. Запускает bot.py и регистрирует автопринятие заявок.
"""
import asyncio

import bot
from aiogram.types import ChatJoinRequest


@bot.router.chat_join_request()
async def approve_join_request(event: ChatJoinRequest):
    await asyncio.sleep(1)
    try:
        await bot.bot.approve_chat_join_request(
            chat_id=event.chat.id,
            user_id=event.from_user.id,
        )
        bot.log.info("Заявка принята: пользователь %s, чат %s", event.from_user.id, event.chat.id)
    except Exception:
        bot.log.exception("Не удалось принять заявку: пользователь %s, чат %s", event.from_user.id, event.chat.id)


_original_start_polling = bot.dp.start_polling


async def start_polling_with_join_requests(*args, **kwargs):
    allowed_updates = kwargs.get("allowed_updates")
    if allowed_updates is not None and "chat_join_request" not in allowed_updates:
        kwargs["allowed_updates"] = [*allowed_updates, "chat_join_request"]
    return await _original_start_polling(*args, **kwargs)


bot.dp.start_polling = start_polling_with_join_requests


if __name__ == "__main__":
    asyncio.run(bot.main())

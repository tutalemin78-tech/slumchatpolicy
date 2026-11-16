"""
Точка входа. Запускает bot.py
"""
import bot
import asyncio
from aiogram.types import ChatJoinRequest


@bot.router.chat_join_request()
async def approve_join_request(event: ChatJoinRequest):
    await asyncio.sleep(1)
    try:
        await bot.bot.approve_chat_join_request(
            chat_id=event.chat.id,
            user_id=event.from_user.id,
        )
    except Exception as e:
        bot.log.warning(f"Не удалось принять заявку в чат {event.chat.id}: {e}")


_original_start_polling = bot.dp.start_polling


async def start_polling_with_join_requests(*args, **kwargs):
    allowed_updates = kwargs.get("allowed_updates")
    if allowed_updates is not None and "chat_join_request" not in allowed_updates:
        allowed_updates.append("chat_join_request")
    return await _original_start_polling(*args, **kwargs)


bot.dp.start_polling = start_polling_with_join_requests

if __name__ == "__main__":
    asyncio.run(bot.main())

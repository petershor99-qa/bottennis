"""
Кнопка меню Telegram слева от строки ввода (v2.163.0). По умолчанию там
список команд бота; для Mini App Telegram позволяет заменить её на кнопку с
подписью «Клуб», открывающую приложение. Подпись одна на чат и задаётся
через Bot API, не через код сообщений.

Состояние выставляется при каждом старте бота по `.env` (`sync_menu_button`):
- `WEBAPP_URL` пуст — кнопка возвращается к списку команд (откат без релиза);
- `WEBAPP_AUDIENCE=admin` — приложение только в чате администратора, у
  остальных кнопка по умолчанию;
- `WEBAPP_AUDIENCE=all` — приложение у всех.
Вызовы идемпотентны, сбой Telegram только пишется в лог и бота не роняет.
"""
import logging

from aiogram import Bot
from aiogram.types import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from bot.utils import env_int
from bot.webapp.config import WEBAPP_MENU_BUTTON_TEXT, webapp_audience, webapp_base_url

logger = logging.getLogger(__name__)


async def sync_menu_button(bot: Bot) -> None:
    url = webapp_base_url()
    admin_id = env_int("ADMIN_ID")
    app_button = (
        MenuButtonWebApp(text=WEBAPP_MENU_BUTTON_TEXT, web_app=WebAppInfo(url=url)) if url else None
    )
    default_button = MenuButtonCommands()

    try:
        if app_button is not None and webapp_audience() == "all":
            await bot.set_chat_menu_button(menu_button=app_button)
        else:
            await bot.set_chat_menu_button(menu_button=default_button)
        if admin_id:
            await bot.set_chat_menu_button(
                chat_id=admin_id, menu_button=app_button if app_button is not None else default_button,
            )
    except Exception:
        logger.warning("Mini App: не удалось настроить кнопку меню Telegram", exc_info=True)

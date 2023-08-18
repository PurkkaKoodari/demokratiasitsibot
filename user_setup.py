from datetime import datetime
from functools import wraps
from typing import cast, Coroutine, Callable

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.ext import ConversationHandler

from db import db, get_user, DbUser
from filters import ADMIN
from help import send_help, user_commands
from langs import lang_icons, locale, loc
from shared import log_errors
from typings import AppContext


REG_LANG = "reg_lang"
REG_CODE = "reg_code"
CHANGE_LANG = "change_lang"
END = ConversationHandler.END

lang_keyboard = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(lang_icons["fi"], callback_data="lang_fi"),
            InlineKeyboardButton(lang_icons["en"], callback_data="lang_en"),
        ]
    ]
)


async def handle_language(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    await message.chat.send_message(
        locale["fi"]["choose_lang"],
        reply_markup=lang_keyboard,
        parse_mode=ParseMode.HTML,
    )
    return CHANGE_LANG


async def handle_start(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    user = get_user(update)
    if user:
        context.user_data.lang = user["language"]
        await send_help(message.chat, user, context)
        await mark_not_absent(update, context, user)
        return END
    await message.chat.send_message(
        locale["fi"]["welcome"],
        reply_markup=lang_keyboard,
        parse_mode=ParseMode.HTML,
    )
    return REG_LANG


async def lang_callback(update: Update, context: AppContext):
    callback_query = cast(CallbackQuery, update.callback_query)
    match callback_query.data:
        case "lang_fi" | "lang_en":
            await callback_query.answer()
            new_lang = callback_query.data.removeprefix("lang_")
            assert new_lang in locale
            context.user_data.lang = new_lang
            await callback_query.edit_message_text(
                loc(context)["lang_set"],
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            if not ADMIN.filter(update):
                async with log_errors(context):
                    await context.bot.set_my_commands(
                        [BotCommand(cmd, desc) for cmd, _, desc in user_commands[new_lang]],
                        scope=BotCommandScopeChat(chat_id=callback_query.from_user.id),
                    )
            user = get_user(update)
            if user is None:
                return await ask_code(callback_query.from_user, context)
            else:
                with db:
                    db.execute(
                        "UPDATE users SET language=? WHERE id = ?",
                        [new_lang, user["id"]],
                    )
                await send_help(callback_query.from_user, user, context)
                return END
        case _:
            await callback_query.answer()


async def ask_code(user: User, context: AppContext):
    await user.send_message(
        loc(context)["enter_code"],
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(input_field_placeholder=loc(context)["code_placeholder"]),
    )
    return REG_CODE


async def save_code(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    code = cast(str, message.text).strip().upper()
    with db:
        cur = db.cursor()
        # I'd like to do this, but it's not thread or task safe
        # cur.execute("BEGIN EXCLUSIVE")
        user = cur.execute("SELECT * FROM users WHERE passcode = ?", [code]).fetchone()
        if user is None:
            await message.chat.send_message(
                loc(context)["invalid_code"],
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(input_field_placeholder=loc(context)["code_placeholder"]),
            )
            return REG_CODE
        tg_user = cast(User, message.from_user)
        if user["tgUserId"] is not None or (
            user["tgUsername"] is not None and user["tgUsername"].lower() != (tg_user.username or "").lower()
        ):
            await message.chat.send_message(
                loc(context)["used_code"],
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(input_field_placeholder=loc(context)["code_placeholder"]),
            )
            return REG_CODE
        cur.execute(
            "UPDATE users SET tgUserId=?, tgUsername=?, tgDisplayName=?, language=?, present=1 WHERE id = ?",
            [tg_user.id, tg_user.username, tg_user.full_name.strip(), context.user_data.lang, user["id"]],
        )
        await send_help(message.chat, user, context)
    return END


def require_setup(func: Callable[[Update, AppContext, DbUser], Coroutine]):
    @wraps(func)
    async def handle(update: Update, context: AppContext):
        user = get_user(update)
        if not context.user_data.lang:
            if user:
                context.user_data.lang = user["language"]
            elif update.callback_query:
                await update.callback_query.answer("Please choose a language with /start.", show_alert=True)
                return
            else:
                return await handle_language(update, context)
        if not user:
            if update.callback_query:
                await update.callback_query.answer("Please register with /start.", show_alert=True)
                return
            return await ask_code(cast(User, update.effective_user), context)
        await mark_not_absent(update, context, user)
        return await func(update, context, user)

    return handle


@require_setup
async def handle_absent(update: Update, context: AppContext, user: DbUser):
    with db:
        db.execute(
            "UPDATE users SET present=0 WHERE id = ?",
            [user["id"]],
        )
    await cast(User, update.effective_user).send_message(loc(context)["absent"])
    return END


async def mark_not_absent(update: Update, context: AppContext, user: DbUser):
    if not user["present"]:
        with db:
            db.execute(
                "UPDATE users SET present=1 WHERE id = ?",
                [user["id"]],
            )
        await cast(User, update.effective_user).send_message(loc(context)["unabsent"])

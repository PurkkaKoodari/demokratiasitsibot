from datetime import datetime
from functools import wraps
from typing import cast, Coroutine, Callable
from time import time
import re

from telegram import (
    Chat,
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler
from telegram.ext.filters import COMMAND, TEXT, ChatType, UpdateType

from config import config
from db import db, get_user, DbUser
from filters import ADMIN
from help import send_help
from langs import lang_icons, locale, loc
from typings import AppContext, PendingInitiative
from util import escape

REG_LANG = "reg_lang"
REG_CODE = "reg_code"
CHANGE_LANG = "change_lang"
INIT_TITLE = "init_title"
INIT_DESC = "init_desc"
INIT_CHECK = "init_check"
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
        if user["tgUserId"] is not None:
            await message.chat.send_message(
                loc(context)["used_code"],
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(input_field_placeholder=loc(context)["code_placeholder"]),
            )
            return REG_CODE
        tg_user = cast(User, message.from_user)
        full_name = f"{tg_user.first_name} {tg_user.last_name}".strip()
        cur.execute(
            "UPDATE users SET tgUserId=?, name=?, language=?, present=1 WHERE id = ?",
            [tg_user.id, full_name, context.user_data.lang, user["id"]],
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
        return await func(update, context, user)

    return handle


@require_setup
async def handle_current(update: Update, context: AppContext, user: DbUser):
    await cast(Message, update.effective_message).reply_text(":)")
    return END


def initiative_create_allowed(user: DbUser, context: AppContext):
    if user["initiativeBanUntil"] and datetime.fromisoformat(user["initiativeBanUntil"]) > datetime.now():
        return loc(context)["init_banned"]
    existing = db.execute(
        "SELECT titleFi, titleEn FROM initiatives WHERE userId = ? AND status = 'submitted'",
        [user["id"]],
    ).fetchone()
    if existing is not None:
        return loc(context)["init_in_review"]
    return None


@require_setup
async def handle_initiative(update: Update, context: AppContext, user: DbUser):
    message = cast(Message, update.effective_message)
    reason = initiative_create_allowed(user, context)
    if reason:
        await message.chat.send_message(reason)
        return END
    await message.chat.send_message(
        loc(context)["init_title"].format(length=config["initiatives"]["title_max_len"]),
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(input_field_placeholder=loc(context)["init_title_placeholder"]),
    )
    context.user_data.init_pending = {"id": int(time() * 1000)}
    context.user_data.init_edit = False
    return INIT_TITLE


def clean_whitespace(s: str):
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


@require_setup
async def initiative_save_title(update: Update, context: AppContext, user: DbUser):
    new_title = clean_whitespace(cast(str, cast(Message, update.message).text))
    if not new_title:
        return INIT_TITLE
    if len(new_title) > config["initiatives"]["title_max_len"]:
        await cast(Message, update.effective_message).reply_text(
            loc(context)["init_title_length"].format(length=config["initiatives"]["title_max_len"]),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(input_field_placeholder=loc(context)["init_title_placeholder"]),
        )
        return INIT_TITLE
    context.user_data.init_pending["title"] = new_title
    if context.user_data.init_edit:
        context.user_data.init_edit = False
        return await initiative_checkup(update, context)
    else:
        await cast(Message, update.effective_message).chat.send_message(
            loc(context)["init_desc"].format(length=config["initiatives"]["desc_max_len"]),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(input_field_placeholder=loc(context)["init_desc_placeholder"]),
        )
        return INIT_DESC


@require_setup
async def initiative_save_desc(update: Update, context: AppContext, user: DbUser):
    new_desc = clean_whitespace(cast(str, cast(Message, update.message).text))
    if not new_desc:
        return INIT_DESC
    if len(new_desc) > config["initiatives"]["desc_max_len"]:
        await cast(Message, update.effective_message).reply_text(
            loc(context)["init_desc_length"].format(length=config["initiatives"]["desc_max_len"]),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(input_field_placeholder=loc(context)["init_desc_placeholder"]),
        )
        return INIT_DESC
    context.user_data.init_pending["desc"] = new_desc
    context.user_data.init_edit = False
    return await initiative_checkup(update, context)


async def initiative_checkup(update: Update, context: AppContext):
    pending = context.user_data.init_pending
    iid = pending.get("id")
    title = pending.get("title")
    desc = pending.get("desc")
    assert title and desc
    await cast(Chat, update.effective_chat).send_message(
        loc(context)["init_checkup"].format(
            title=escape(title),
            desc=escape(desc),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(loc(context)["init_send"], callback_data=f"init_send:{iid}")],
                [InlineKeyboardButton(loc(context)["init_edit_title"], callback_data=f"init_edit_title:{iid}")],
                [InlineKeyboardButton(loc(context)["init_edit_desc"], callback_data=f"init_edit_desc:{iid}")],
                [InlineKeyboardButton(loc(context)["init_cancel"], callback_data=f"init_cancel:{iid}")],
            ]
        ),
    )
    return INIT_CHECK


@require_setup
async def initiative_callback(update: Update, context: AppContext, user: DbUser):
    callback_query = cast(CallbackQuery, update.callback_query)
    action, iid = (callback_query.data or "").split(":")
    iid = int(iid)
    if iid != context.user_data.init_pending.get("id"):
        await callback_query.answer()
        await callback_query.edit_message_text(
            loc(context)["init_broken"],
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        return END
    match action:
        case "init_send":
            reason = initiative_create_allowed(user, context)
            if reason:
                await callback_query.answer(reason, show_alert=True)
                return INIT_CHECK
            await callback_query.answer()
            initiative_create(user, context.user_data.init_pending)
            await callback_query.edit_message_text(
                loc(context)["init_sent"],
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
            # TODO notify
            return END
        case "init_edit_title":
            await callback_query.answer()
            context.user_data.init_edit = True
            await callback_query.edit_message_reply_markup(None)
            await callback_query.from_user.send_message(
                loc(context)["init_editing_title"].format(length=config["initiatives"]["title_max_len"]),
                reply_markup=ForceReply(input_field_placeholder=loc(context)["init_title_placeholder"]),
                parse_mode=ParseMode.HTML,
            )
            return INIT_TITLE
        case "init_edit_desc":
            await callback_query.answer()
            context.user_data.init_edit = True
            await callback_query.edit_message_reply_markup(None)
            await callback_query.from_user.send_message(
                loc(context)["init_editing_desc"].format(length=config["initiatives"]["desc_max_len"]),
                reply_markup=ForceReply(input_field_placeholder=loc(context)["init_desc_placeholder"]),
                parse_mode=ParseMode.HTML,
            )
            return INIT_DESC
        case "init_cancel":
            await callback_query.answer()
            await callback_query.edit_message_text(
                loc(context)["init_canceled"],
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            context.user_data.init_pending = {}
            return END
        case _:
            await callback_query.answer()
            return END


def initiative_create(user: DbUser, data: PendingInitiative):
    assert all(key in data for key in ("title", "desc"))
    with db:
        lang_suffix = user["language"].capitalize()
        lang_cols = f"title{lang_suffix}, desc{lang_suffix}"
        db.execute(
            f"INSERT INTO initiatives (userId, {lang_cols}) VALUES (?, ?, ?)",
            [user["id"], data["title"], data["desc"]],
        )


@require_setup
async def initiative_cancel(update: Update, context: AppContext, user: DbUser):
    await cast(Message, update.effective_message).chat.send_message(
        loc(context)["init_cancel"],
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return END


@require_setup
async def handle_initiatives(update: Update, context: AppContext, user: DbUser):
    await cast(Message, update.effective_message).reply_text(":)")
    return END


@require_setup
async def initiatives_callback(update: Update, context: AppContext, user: DbUser):
    pass


@require_setup
async def handle_inotifications(update: Update, context: AppContext, user: DbUser):
    new_setting = not user["initiativeNotifs"]
    with db:
        db.execute(
            "UPDATE users SET initiativeNotifs=? WHERE id = ?",
            [new_setting, user["id"]],
        )
    await cast(Message, update.effective_message).chat.send_message(
        loc(context)["init_notifs_on" if new_setting else "init_notifs_off"],
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return END


user_entry = [
    CommandHandler("start", handle_start, ~ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("start_user", handle_start, ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("help", handle_start, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("language", handle_language, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("current", handle_current, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("initiative", handle_initiative, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("initiatives", handle_initiatives, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("inotifications", handle_inotifications, ChatType.PRIVATE & ~UpdateType.EDITED),
    CallbackQueryHandler(lang_callback, pattern=r"^lang_\w+$"),
    CallbackQueryHandler(initiative_callback, pattern=r"^init_\w+:\d+$"),
    CallbackQueryHandler(initiatives_callback, pattern=r"^inits_\w+:\d+$"),
]

user_states = {
    REG_LANG: [],  # could kinda use END here
    CHANGE_LANG: [],
    REG_CODE: [
        MessageHandler(TEXT & ~COMMAND, save_code),
    ],
    INIT_TITLE: [
        MessageHandler(TEXT & ~COMMAND, initiative_save_title),
        CommandHandler("cancel", initiative_cancel, ~UpdateType.EDITED),
    ],
    INIT_DESC: [
        MessageHandler(TEXT & ~COMMAND, initiative_save_desc),
        CommandHandler("cancel", initiative_cancel, ~UpdateType.EDITED),
    ],
}

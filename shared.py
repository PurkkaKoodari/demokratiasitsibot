from random import shuffle
from typing import cast, Any

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, User, Message, ForceReply, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import config
from db import db, DbUser, get_kv, DbPoll, DbInitiative
from langs import locale
from typings import AppContext
from util import escape, user_link, grouplist


GROUP_REGEX = r"^[a-z0-9_-]{1,32}$"


class ignore_errors:
    def __init__(self, filter: str | None = None):
        self.filter = filter

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val: Exception, exc_tb):
        if exc_val:
            if self.filter is None or self.filter in str(exc_val):
                return True


class log_errors:
    def __init__(self, context: AppContext, filter: str | None = None):
        self.context = context
        self.filter = filter

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val: Exception, exc_tb):
        if exc_val:
            if self.filter is None or self.filter in str(exc_val):
                await self.context.application.process_error(None, exc_val)
                return True


async def admin_log(
    action: str,
    update: Update | None,
    context: AppContext,
    parse_mode: ParseMode | None = ParseMode.HTML,
    extra_target: int | None = None,
):
    target = get_kv("admin_log", None)
    user = cast(User, update.effective_user) if update else None
    message = f"{user_link(user)} {action}" if user else action
    if user and user.id != config["admins"][0]:
        async with log_errors(context):
            await context.bot.send_message(config["admins"][0], message, parse_mode=parse_mode)
    if target:
        async with log_errors(context):
            await context.bot.send_message(target, message, parse_mode=parse_mode)
    if extra_target and extra_target != target and extra_target != config["admins"][0]:
        async with log_errors(context):
            await context.bot.send_message(extra_target, message, parse_mode=parse_mode)


def is_member(group: str, user: DbUser | int) -> bool:
    if group == "everyone":
        return True
    if group in ("present", "absent"):
        present = group == "present"
        if isinstance(user, int):
            row = db.execute("SELECT 1 FROM users WHERE id = ? AND present = ?", [user, present]).fetchone()
            return row is not None
        return user["present"]
    uid = user if isinstance(user, int) else user["id"]
    row = db.execute("SELECT 1 FROM groupMembers WHERE `group` = ? AND userId = ?", [group, uid]).fetchone()
    return row is not None


def get_group_member_ids(group: str) -> list[int]:
    if group == "everyone":
        return [row["id"] for row in db.execute("SELECT id FROM users").fetchall()]
    if group in ("present", "absent"):
        return [
            row["id"] for row in db.execute("SELECT id FROM users WHERE present = ?", [group == "present"]).fetchall()
        ]
    return [
        row["userId"] for row in db.execute("SELECT userId FROM groupMembers WHERE `group` = ?", [group]).fetchall()
    ]


def get_group_member_users(group: str) -> list[DbUser]:
    if group == "everyone":
        return db.execute("SELECT * FROM users").fetchall()
    if group in ("present", "absent"):
        return db.execute("SELECT * FROM users WHERE present = ?", [group == "present"]).fetchall()
    return db.execute(
        """
        SELECT users.*
        FROM groupMembers
        INNER JOIN users ON users.id = groupMembers.userId
        WHERE groupMembers.`group` = ?
        """,
        [group],
    ).fetchall()


async def update_menu(
    update: Update, text: str, reply_markup: InlineKeyboardMarkup | ForceReply | ReplyKeyboardRemove | None
):
    if update.callback_query is not None:
        if reply_markup is not None and not isinstance(reply_markup, InlineKeyboardMarkup):
            await update.callback_query.edit_message_reply_markup(None)
            await update.callback_query.from_user.send_message(
                text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
        else:
            with ignore_errors(filter="not modified"):
                await update.callback_query.edit_message_text(
                    text, reply_markup=cast(Any, reply_markup), parse_mode=ParseMode.HTML
                )
    else:
        await cast(Message, update.effective_message).reply_text(
            text, reply_markup=reply_markup or ReplyKeyboardRemove(), parse_mode=ParseMode.HTML
        )

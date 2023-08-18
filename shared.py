from random import shuffle
from sqlite3 import Row
from typing import cast

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, User
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import config
from db import db, DbUser, get_kv
from langs import locale
from typings import AppContext
from util import escape, user_link, grouplist


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


def get_poll(poll_id: int | Row) -> Row:
    if isinstance(poll_id, int):
        return db.execute("SELECT * FROM polls WHERE id = ?", [poll_id]).fetchone()
    else:
        return poll_id


def format_poll(
    poll: int | Row,
    langs: tuple[str, ...] = ("fi", "en"),
) -> tuple[Row, dict[str, str], dict[str | tuple[str, str | None], InlineKeyboardMarkup]]:
    poll = get_poll(poll)
    if not poll:
        raise ValueError("poll missing")
    options = db.execute(
        "SELECT id, textFi, textEn, area FROM options WHERE pollId = ? ORDER BY orderNo ASC", [poll["id"]]
    ).fetchall()
    messages = {lang: escape(poll[f"text{lang.capitalize()}"]) for lang in langs}
    if poll["perArea"]:
        areas = grouplist(options, lambda opt: opt["area"])
        keyboards = {
            (lang, area): InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(opt[f"text{lang.capitalize()}"], callback_data=f"vote_vote:{opt['id']}")]
                    for opt in area_opts
                ]
            )
            for lang in langs
            for area, area_opts in areas.items()
        }
    else:
        keyboards = {
            lang: InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(opt[f"text{lang.capitalize()}"], callback_data=f"vote_vote:{opt['id']}")]
                    for opt in options
                ]
            )
            for lang in langs
        }
    return poll, messages, cast(dict, keyboards)


async def send_poll(context: AppContext, poll: int | Row, user: DbUser | None = None):
    langs = (user["language"],) if user else ("fi", "en")
    poll, messages, keyboards = format_poll(poll, langs)
    targets = [user] if user is not None else get_group_member_users(poll["voterGroup"])
    if user:
        votes = db.execute(
            "SELECT voterId FROM votes WHERE pollId = ? AND voterId = ?", [poll["id"], user["id"]]
        ).fetchall()
    else:
        votes = db.execute("SELECT voterId FROM votes WHERE pollId = ?", [poll["id"]]).fetchall()
    votes = {vote["voterId"] for vote in votes}
    shuffle(targets)
    attempted = 0
    success = 0
    absent = 0
    voted = 0
    for target in targets:
        if not target["tgUserId"] or not target["language"] or (not user and not target["present"]):
            absent += 1
            continue
        lang = target["language"]
        opts_key = (lang, target["area"]) if poll["perArea"] else lang
        if opts_key not in keyboards:
            opts_key = (lang, None)  # non-elections don't have per-area options
        prefix = ""
        if user is None:
            key = "new_poll" if poll["type"] != "election" else "new_election"
            prefix = f"<b>{locale[lang][key]}</b>\n\n"
        suffix = ""
        if target["id"] in votes:
            if not user:
                voted += 1
                continue
            key = "poll_already_voted" if poll["type"] != "election" else "election_already_voted"
            suffix = f"\n\n<b>{locale[lang][key]}</b>"
        attempted += 1
        try:
            msg = await context.bot.send_message(
                target["tgUserId"],
                prefix + messages[lang] + suffix,
                parse_mode=ParseMode.HTML,
                reply_markup=None if target["id"] in votes else keyboards[opts_key],
            )
        except TelegramError as err:
            await context.application.process_error(None, err)
        else:
            with db:
                cur = db.cursor()
                cur.execute(
                    "INSERT INTO sentMessages (chatId, messageId, userId, pollId, language, isAdmin, status) VALUES (?, ?, ?, ?, ?, FALSE, 'open')",
                    [msg.chat_id, msg.message_id, target["id"], poll["id"], lang],
                )
            success += 1
    if not user:
        await admin_log(
            f"Poll <b>{escape(poll['textFi'])}</b> sent successfully to {success} of {attempted} present users. "
            f"{absent} absent users and {voted} already voted users skipped.",
            None,
            context,
        )


async def close_poll(context: AppContext, poll: int | Row):
    poll = get_poll(poll)
    messages = db.execute(
        f"SELECT chatId, messageId, language FROM sentMessages WHERE pollId = ? AND isAdmin = FALSE", [poll["id"]]
    ).fetchall()
    success = 0
    attempted = 0
    for db_msg in messages:
        lang = db_msg["language"]
        question = poll[f"text{lang.capitalize()}"]
        closed = locale[lang]["poll_closed" if poll["type"] != "election" else "election_closed"]
        attempted += 1
        try:
            with ignore_errors(filter="not modified"):
                await context.bot.edit_message_text(
                    f"{escape(question)}\n\n<b>{closed}</b>",
                    chat_id=db_msg["chatId"],
                    message_id=db_msg["messageId"],
                    reply_markup=None,
                    parse_mode=ParseMode.HTML,
                )
        except TelegramError as err:
            await context.application.process_error(None, err)
        else:
            success += 1
    await admin_log(
        f"Poll <b>{escape(poll['textFi'])}</b> closed successfully in {success} of {attempted} messages.",
        None,
        context,
    )


async def reopen_poll(context: AppContext, poll: int | Row):
    poll, messages, keyboards = format_poll(poll)
    db_messages = db.execute(
        f"""
        SELECT sentMessages.chatId, sentMessages.messageId, sentMessages.userId, users.language, users.area
        FROM sentMessages
        INNER JOIN users ON sentMessages.userId = users.id
        WHERE pollId = ? AND isAdmin = FALSE
        """,
        [poll["id"]],
    ).fetchall()
    votes = db.execute("SELECT voterId FROM votes WHERE pollId = ?", [poll["id"]]).fetchall()
    votes = {vote["voterId"] for vote in votes}
    success = 0
    attempted = 0
    for db_msg in db_messages:
        lang = db_msg["language"]
        opts_key = (lang, db_msg["area"]) if poll["perArea"] else lang
        if opts_key not in keyboards:
            opts_key = (lang, None)  # non-elections don't have per-area options
        suffix = ""
        if db_msg["userId"] in votes:
            key = "poll_already_voted" if poll["type"] != "election" else "election_already_voted"
            suffix = f"\n\n<b>{locale[lang][key]}</b>"
        attempted += 1
        try:
            with ignore_errors(filter="not modified"):
                await context.bot.edit_message_text(
                    messages[lang] + suffix,
                    chat_id=db_msg["chatId"],
                    message_id=db_msg["messageId"],
                    reply_markup=None if db_msg["userId"] in votes else keyboards[opts_key],
                    parse_mode=ParseMode.HTML,
                )
        except TelegramError as err:
            await context.application.process_error(None, err)
        else:
            success += 1
    await admin_log(
        f"Poll <b>{escape(poll['textFi'])}</b> reopened successfully in {success} of {attempted} messages.",
        None,
        context,
    )

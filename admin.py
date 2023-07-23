from typing import Any, cast

from telegram import (
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler
from telegram.ext.filters import COMMAND, TEXT, ChatType, UpdateType

from config import config
from db import db
from filters import ADMIN
from langs import lang_icons
from util import escape

NP_QUESTION = "np_question"
NP_OPTIONS = "np_options"
NP_MENU = "np_menu"
END = ConversationHandler.END


async def handle_chat_member(update: Update, context: CallbackContext):
    member = cast(ChatMemberUpdated, update.my_chat_member)
    chat = cast(Chat, update.effective_chat)
    # only relevant if wasn't a member previously
    if member.old_chat_member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        return
    # handle leaving from special groups
    if member.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        # TODO
        return
    # only allow for admins
    if member.from_user.id not in config["admins"]:
        try:
            await chat.send_message("nah")
        except:
            pass
        await chat.leave()
        return
    # only allow adding as admin
    if member.new_chat_member.status != ChatMemberStatus.ADMINISTRATOR:
        try:
            await chat.send_message("please add me directly as admin (manage group -> admins -> add)")
        except:
            pass
        await chat.leave()
        return
    await handle_admin_start(update, context)


async def handle_admin_start(update: Update, context: CallbackContext):
    await cast(Chat, update.effective_chat).send_message(
        """\
Welcome! Things you can do as admin:

/broadcast - broadcast a message to all participants
/poll_log - show log of new polls here
/initiative_log - handle initiatives here
/newpoll - create a poll (only in private chat)
/start_user - register as a sitsi participant"""
    )


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
            await update.callback_query.edit_message_text(
                text, reply_markup=cast(Any, reply_markup), parse_mode=ParseMode.HTML
            )
    else:
        await cast(Message, update.effective_message).reply_text(
            text, reply_markup=reply_markup or ReplyKeyboardRemove(), parse_mode=ParseMode.HTML
        )


async def newpoll_start(update: Update, context: CallbackContext):
    user_data = cast(dict, context.user_data)
    user_data["poll_edit"] = None
    user_data["poll_pending"] = {}
    return await newpoll_ask_question(update, context, "fi")


async def newpoll_ask_question(update: Update, context: CallbackContext, lang: str):
    await update_menu(update, f"Enter the poll question in {lang_icons[lang]} (or /cancel)", reply_markup=ForceReply())
    cast(dict, context.user_data)["poll_lang"] = lang
    return NP_QUESTION


async def newpoll_save_question(update: Update, context: CallbackContext):
    user_data = cast(dict, context.user_data)
    new_question = cast(str, cast(Message, update.message).text).strip()
    if not new_question:
        return NP_QUESTION
    lang = user_data["poll_lang"]
    assert lang in ("fi", "en")
    user_data["poll_pending"][f"question_{lang}"] = new_question
    if (pid := user_data.get("poll_edit")) is not None:
        # editing
        return await newpoll_main_menu(update, context, pid)
    else:
        # creating
        if lang == "en":
            return await newpoll_ask_options(update, context, "fi")
        else:
            return await newpoll_ask_question(update, context, "en")


async def newpoll_ask_options(update: Update, context: CallbackContext, lang: str, prefix=""):
    await update_menu(
        update,
        f"{prefix}Enter poll options in {lang_icons[lang]}, one per line (or /cancel)",
        reply_markup=ForceReply(),
    )
    cast(dict, context.user_data)["poll_lang"] = lang
    return NP_OPTIONS


async def newpoll_save_options(update: Update, context: CallbackContext):
    user_data = cast(dict, context.user_data)
    new_opts = [opt.strip() for opt in cast(str, cast(Message, update.message).text).split("\n") if not opt.isspace()]
    if not new_opts:
        return NP_OPTIONS
    pid = user_data["poll_edit"]
    pending = user_data["poll_pending"]
    lang = user_data["poll_lang"]
    assert lang in ("fi", "en")
    pending[f"opts_{lang}"] = new_opts
    # if editing and not already given opts in other lang, get them from db
    other_lang = "fi" if lang == "en" else "en"
    other_opts = pending.get(f"opts_{other_lang}")
    if pid is not None and other_opts is None:
        other_opts = [
            opt[0]
            for opt in db.execute(
                f"SELECT text{other_lang.capitalize()} FROM options WHERE pollId = ?", [pid]
            ).fetchall()
        ]
        pending[f"opts_{other_lang}"] = other_opts
    # number of opts mismatch?
    if other_opts is not None and len(new_opts) != len(other_opts):
        return await newpoll_ask_options(
            update, context, other_lang, f"<b>Different number of options than in {lang_icons[other_lang]}!</b>\n\n"
        )
    if pid is not None:
        # editing
        return await newpoll_main_menu(update, context, pid)
    else:
        # creating
        if other_opts is not None:
            pid = newpoll_create(pending)
            user_data["poll_pending"] = {}
            return await newpoll_created(update, context, pid)
        else:
            return await newpoll_ask_options(update, context, other_lang)


async def newpoll_created(update: Update, context: CallbackContext, pid: int):
    return await newpoll_main_menu(update, context, pid, top=f"<b>Poll created! Id:</b> <code>{pid}</code>")


def newpoll_create(pending: dict):
    assert all(key in pending for key in ("question_fi", "question_en", "opts_fi", "opts_en"))
    with db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO polls (textFi, textEn) VALUES (?, ?)", [pending["question_fi"], pending["question_en"]]
        )
        pid = cur.lastrowid
        assert pid
        cur.executemany(
            "INSERT INTO options (pollId, textFi, textEn, orderNo) VALUES (?, ?, ?, ?)",
            [[pid, fi, en, num] for num, (fi, en) in enumerate(zip(pending["opts_fi"], pending["opts_en"]))],
        )
    return pid


def newpoll_commit(pid: int, pending: dict):
    if not pending:
        return
    with db:
        cur = db.cursor()
        fields = []
        values = []
        if "question_fi" in pending:
            fields.append("textFi")
            values.append(pending["question_fi"])
        if "question_en" in pending:
            fields.append("textEn")
            values.append(pending["question_en"])
        if fields:
            cur.execute(f"UPDATE polls SET {', '.join(f'{field}=?' for field in fields)} WHERE id=?", [*values, pid])
        if "opts_fi" in pending:
            assert "opts_en" in pending
            cur.execute("DELETE FROM options WHERE pollId = ?", [pid])
            cur.executemany(
                "INSERT INTO options (pollId, textFi, textEn, orderNo) VALUES (?, ?, ?, ?)",
                [[pid, fi, en, num] for num, (fi, en) in enumerate(zip(pending["opts_fi"], pending["opts_en"]))],
            )


async def newpoll_cancel(update: Update, context: CallbackContext):
    user_data = cast(dict, context.user_data)
    if (pid := user_data.get("poll_edit")) is None:
        await update_menu(update, "Poll creation cancelled.", reply_markup=None)
        return END
    else:
        user_data["poll_edit"] = None
        user_data["poll_pending"] = {}
        return await newpoll_main_menu(update, context, pid, top="<b>Edits discarded.</b>")


def newpoll_menu_text(poll, top=None, bottom=None, pending: dict = {}):
    if "opts_fi" in pending:
        opts = list(zip(pending["opts_fi"], pending["opts_en"]))
    else:
        opts = db.execute(
            "SELECT textFi, textEn FROM options WHERE pollId = ? ORDER BY orderNo ASC", [poll["id"]]
        ).fetchall()
    text = f"{escape(pending.get('question_fi', poll['textFi']))}\n\n{escape(pending.get('question_en', poll['textEn']))}\n"
    if not opts:
        text += "\nNo options!"
    for fi, en in opts:
        text += f"\n- {escape(fi)} / {escape(en)}"
    if top:
        text = f"{top}\n\n{text}"
    if bottom:
        text = f"{text}\n\n{bottom}"
    return text


async def newpoll_callback(update: Update, context: CallbackContext):
    callback_query = cast(CallbackQuery, update.callback_query)
    user_data = cast(dict, context.user_data)
    if callback_query.from_user.id not in config["admins"]:
        return END
    action, pid = (callback_query.data or "").split(":")
    pid = int(pid)
    poll = db.execute("SELECT * FROM polls WHERE id = ?", [pid]).fetchone()
    if poll is None:
        await callback_query.answer("Poll not found!")
        await update_menu(update, "Poll not found!", reply_markup=None)
        return END
    user_data["poll_edit"] = pid
    if not action.startswith("np_edit") and action not in ("np_commit", "np_revert"):
        user_data["poll_pending"] = {}
    match action:
        case "np_edit_qfi" | "np_edit_qen" | "np_edit_ofi" | "np_edit_oen" | "np_edit" | "np_commit" if poll[
            "status"
        ] != "created":
            await callback_query.answer("Poll already active!")
            user_data["poll_pending"] = {}
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_edit_qfi":
            await callback_query.answer()
            return await newpoll_ask_question(update, context, "fi")
        case "np_edit_qen":
            await callback_query.answer()
            return await newpoll_ask_question(update, context, "en")
        case "np_edit_ofi":
            await callback_query.answer()
            return await newpoll_ask_options(update, context, "fi")
        case "np_edit_oen":
            await callback_query.answer()
            return await newpoll_ask_options(update, context, "en")
        case "np_edit":
            await callback_query.answer()
            return await newpoll_edit_menu(update, context, pid, poll)
        case "np_revert":
            await callback_query.answer("Edits discarded.")
            user_data["poll_pending"] = {}
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Edits discarded.</b>")
        case "np_commit":
            await callback_query.answer("Poll saved.")
            newpoll_commit(pid, user_data["poll_pending"])
            user_data["poll_pending"] = {}
            return await newpoll_main_menu(update, context, pid, top="<b>Poll saved.</b>")
        case "np_activate" | "np_activate2" if poll["status"] == "active":
            await callback_query.answer("Poll already active!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_activate":
            await callback_query.answer()
            bottom = (
                "<b>Are you sure you want to ACTIVATE this poll?</b>"
                if poll["status"] == "created"
                else "<b>Are you sure you want to REOPEN this poll?</b>"
            )
            await update_menu(
                update,
                newpoll_menu_text(poll, bottom=bottom),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Yes, activate!", callback_data=f"np_activate2:{pid}")],
                        [InlineKeyboardButton("Cancel", callback_data=f"np_menu:{pid}")],
                    ]
                ),
            )
            return NP_MENU
        case "np_activate2":
            with db:
                db.execute("UPDATE polls SET status='active' WHERE pid=?", [pid])
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Poll activated.</b>")
        case "np_announce" | "np_announce2" | "np_close" | "np_close2" if poll["status"] != "active":
            await callback_query.answer("Poll is not active!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_announce":
            await callback_query.answer()
            await update_menu(
                update,
                newpoll_menu_text(poll, bottom="<b>Are you sure you want to ANNOUNCE this poll to all voters?</b>"),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Yes, announce!", callback_data=f"np_announce2:{pid}")],
                        [InlineKeyboardButton("Cancel", callback_data=f"np_menu:{pid}")],
                    ]
                ),
            )
            return NP_MENU
        case "np_announce2":
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Poll announced.</b>")
        case "np_close":
            await callback_query.answer()
            await update_menu(
                update,
                newpoll_menu_text(poll, bottom="<b>Are you sure you want to close this poll and get results?</b>"),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Yes, close!", callback_data=f"np_close2:{pid}")],
                        [InlineKeyboardButton("Cancel", callback_data=f"np_menu:{pid}")],
                    ]
                ),
            )
            return NP_MENU
        case "np_close2":
            with db:
                db.execute("UPDATE polls SET status='closed' WHERE pid=?", [pid])
            # TODO: close polls for users
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Poll closed.</b>")
        case _:
            await callback_query.answer()
            return await newpoll_main_menu(update, context, pid, poll)


async def newpoll_edit_menu(update: Update, context: CallbackContext, pid: int, poll: Any):
    pending = cast(dict, context.user_data).get("poll_pending", {})
    bottom = "<b>Unsaved changes!</b>" if pending else "<b>What should be edited?</b>"
    await update_menu(
        update,
        newpoll_menu_text(poll, bottom=bottom, pending=pending),
        reply_markup=InlineKeyboardMarkup(
            [
                *(([InlineKeyboardButton("Save changes", callback_data=f"np_commit:{pid}")],) if pending else ()),
                [
                    InlineKeyboardButton("Question 🇫🇮", callback_data=f"np_edit_qfi:{pid}"),
                    InlineKeyboardButton("Question 🇬🇧", callback_data=f"np_edit_qen:{pid}"),
                ],
                [
                    InlineKeyboardButton("Options 🇫🇮", callback_data=f"np_edit_ofi:{pid}"),
                    InlineKeyboardButton("Options 🇬🇧", callback_data=f"np_edit_oen:{pid}"),
                ],
                [InlineKeyboardButton("Discard changes", callback_data=f"np_revert:{pid}")]
                if pending
                else [InlineKeyboardButton("Cancel", callback_data=f"np_menu:{pid}")],
            ]
        ),
    )
    return NP_MENU


async def newpoll_main_menu(
    update: Update,
    context: CallbackContext,
    pid: int,
    poll: Any = None,
    *,
    top: str | None = None,
    bottom: str | None = None,
):
    if poll is None:
        poll = db.execute("SELECT * FROM polls WHERE id = ?", [pid]).fetchone()
        if poll is None:
            await update_menu(update, "Poll not found!", reply_markup=None)
            return END
    if cast(dict, context.user_data).get("poll_pending"):
        return await newpoll_edit_menu(update, context, pid, poll)
    await update_menu(
        update,
        newpoll_menu_text(poll, top=top, bottom=bottom),
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Edit", callback_data=f"np_edit:{pid}")],
                [
                    InlineKeyboardButton("Activate", callback_data=f"np_activate:{pid}")
                    if poll["status"] == "created"
                    else InlineKeyboardButton("Close & Results", callback_data=f"np_close:{pid}")
                    if poll["status"] == "active"
                    else InlineKeyboardButton("Reopen", callback_data=f"np_activate:{pid}"),
                ],
                *(
                    ([InlineKeyboardButton("Announce", callback_data=f"np_announce:{pid}")],)
                    if poll["status"] == "active"
                    else ()
                ),
            ]
        ),
    )
    return NP_MENU


admin_entry = [
    CommandHandler("start", handle_admin_start, ADMIN & ~UpdateType.EDITED),
    CommandHandler("newpoll", newpoll_start, ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CallbackQueryHandler(newpoll_callback, pattern=r"^np_\w+:\d+$"),
]

admin_states = {
    NP_QUESTION: [
        MessageHandler(TEXT & ~COMMAND, newpoll_save_question),
        CommandHandler("cancel", newpoll_cancel, ~UpdateType.EDITED),
    ],
    NP_OPTIONS: [
        MessageHandler(TEXT & ~COMMAND, newpoll_save_options),
        CommandHandler("cancel", newpoll_cancel, ~UpdateType.EDITED),
    ],
    NP_MENU: [
        CallbackQueryHandler(newpoll_callback, pattern=r"^np_\w+:\d+$"),
        CommandHandler("cancel", newpoll_cancel, ~UpdateType.EDITED),
    ],
}

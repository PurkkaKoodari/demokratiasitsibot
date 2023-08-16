import re
from sqlite3 import IntegrityError
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
    User,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler
from telegram.ext.filters import COMMAND, TEXT, ChatType, UpdateType

from config import config
from db import db, get_kv, set_kv
from filters import ADMIN, CONFIG_ADMIN, db_admins, AdminCallbackQueryHandler, config_admins, banned_admins
from langs import lang_icons
from typings import AppContext, PendingPoll
from util import escape, user_link

NP_QUESTION = "np_question"
NP_OPTIONS = "np_options"
NP_GROUP = "np_group"
NP_MENU = "np_menu"
END = ConversationHandler.END

GROUP_REGEX = r"^[a-z0-9_-]{1,32}$"


async def handle_chat_member(update: Update, context: AppContext):
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
    if member.from_user.id not in config_admins:
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
    db_admins.add(chat.id)
    set_kv("admin_groups", sorted(db_admins))
    await handle_admin_start(update, context)


async def handle_admin_start(update: Update, context: AppContext):
    chat = cast(Chat, update.effective_chat)
    await chat.send_message(
        """\
Welcome! Things you can do in admin-approved chats:

/grant - get admin privileges for DMs
/broadcast - broadcast a message to all participants
/admin_log - show log of admin actions here
/initiative_log - handle initiatives here
/polls - manage existing polls (only in private chat)
/newpoll - create a poll (only in private chat)
/newelection - create an election (only in private chat)
/unassign_code [code] - unassign a seat code from its Telegram user
/group_list - list all groups
/group_view [group] - view members of a group
/group_add [group] [uid...] - add people to a group
/group_remove [group] [uid...] - remove people from a group
/start_user - register as a sitsi participant (only in private chat)"""
    )
    return await handle_grant(update, context, True)


async def handle_grant(update: Update, context: AppContext, start=False):
    chat = cast(Chat, update.effective_chat)
    uid = cast(User, update.effective_user).id
    if uid in config_admins and context.args:
        try:
            uid = int(context.args[0])
        except Exception:
            return
        db_admins.add(uid)
        banned_admins.discard(uid)
        set_kv("admin_groups", sorted(db_admins))
        set_kv("banned_admins", sorted(banned_admins))
        await chat.send_message(f"Granted admin rights to <code>{uid}</code>.", parse_mode=ParseMode.HTML)
        return
    if uid not in db_admins:
        db_admins.add(uid)
        set_kv("admin_groups", sorted(db_admins))
        await admin_log(
            f"(<code>{uid}</code>) received admin rights via {escape(chat.effective_name or 'unnamed chat')} ({chat.id}).",
            update,
            context,
        )
        await chat.send_message("You can now use admin commands in private chats as well.")
    elif not start:
        await chat.send_message("You can already use admin commands in private chats.")


async def handle_deny(update: Update, context: AppContext, start=False):
    chat = cast(Chat, update.effective_chat)
    uid = cast(User, update.effective_user).id
    if uid in config_admins and context.args:
        try:
            uid = int(context.args[0])
        except Exception:
            return
        db_admins.discard(uid)
        banned_admins.add(uid)
        set_kv("admin_groups", sorted(db_admins))
        set_kv("banned_admins", sorted(banned_admins))
        await chat.send_message(f"Removed admin rights from <code>{uid}</code>.", parse_mode=ParseMode.HTML)


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


async def newpoll_start(update: Update, context: AppContext):
    context.user_data.poll_edit = None
    context.user_data.poll_pending = {}
    context.user_data.poll_is_election = False
    return await newpoll_ask_question(update, context, "fi")


async def newpoll_start_election(update: Update, context: AppContext):
    context.user_data.poll_edit = None
    context.user_data.poll_pending = {}
    context.user_data.poll_is_election = True
    return await newpoll_ask_question(update, context, "fi")


async def newpoll_ask_question(update: Update, context: AppContext, lang: str):
    await update_menu(update, f"Enter the poll question in {lang_icons[lang]} (or /cancel)", reply_markup=ForceReply())
    context.user_data.poll_lang = lang
    return NP_QUESTION


async def newpoll_save_question(update: Update, context: AppContext):
    new_question = cast(str, cast(Message, update.message).text).strip()
    if not new_question:
        return NP_QUESTION
    pid = context.user_data.poll_edit
    pending = context.user_data.poll_pending
    lang = context.user_data.poll_lang
    assert lang in ("fi", "en")
    pending["textFi" if lang == "fi" else "textEn"] = new_question
    if pid is not None:
        # editing
        return await newpoll_main_menu(update, context, pid)
    else:
        # creating
        if lang == "en":
            if context.user_data.poll_is_election:
                assert "textFi" in pending
                pid = newpoll_create(pending, is_election=True)
                await admin_log(f"created the election <b>{escape(cast(str, pending['textFi']))}</b>.", update, context)
                context.user_data.poll_pending = {}
                return await newpoll_created(update, context, pid, is_election=True)
            else:
                return await newpoll_ask_options(update, context, "fi")
        else:
            return await newpoll_ask_question(update, context, "en")


async def newpoll_ask_options(update: Update, context: AppContext, lang: str, prefix=""):
    await update_menu(
        update,
        f"{prefix}Enter poll options in {lang_icons[lang]}, one per line (or /cancel)",
        reply_markup=ForceReply(),
    )
    context.user_data.poll_lang = lang
    return NP_OPTIONS


async def newpoll_save_options(update: Update, context: AppContext):
    message = cast(Message, update.message)
    new_opts = [opt.strip() for opt in cast(str, message.text).split("\n") if not opt.isspace()]
    if not new_opts:
        return NP_OPTIONS
    if context.user_data.poll_is_election:
        await message.reply_text("Something is fucky wucky, this is an election.")
        return END
    pid = context.user_data.poll_edit
    pending = context.user_data.poll_pending
    lang = context.user_data.poll_lang
    assert lang in ("fi", "en")
    pending["opts_fi" if lang == "fi" else "opts_en"] = new_opts
    # if editing and not already given opts in other lang, get them from db
    other_lang = "en" if lang == "fi" else "fi"
    other_opts = pending.get(f"opts_{other_lang}")
    if pid is not None and other_opts is None:
        other_opts = [
            opt[0]
            for opt in db.execute(
                f"SELECT text{other_lang.capitalize()} FROM options WHERE pollId = ?", [pid]
            ).fetchall()
        ]
        pending["opts_en" if lang == "fi" else "opts_fi"] = other_opts
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
            pid = newpoll_create(pending, is_election=False)
            await admin_log(
                f"created the poll <b>{escape(cast(str, pending.get('question_fi')))}</b>.", update, context
            )
            context.user_data.poll_pending = {}
            return await newpoll_created(update, context, pid, is_election=False)
        else:
            return await newpoll_ask_options(update, context, other_lang)


async def newpoll_ask_group(update: Update, context: AppContext, group: str, complain=None):
    match group:
        case "voterGroup":
            title = "voter group"
            desc = "This group of users will see the poll and can vote. Send <code>everyone</code> for everyone."
        case "sourceGroup":
            title = "candidate group"
            desc = "This group of users will be candidates for the election."
        case "electedGroup":
            title = "elected group"
            desc = "The elected candidates will be added to this group, optionally replacing all existing members."
        case _:
            raise AssertionError("bad group")
    prefix = ""
    if complain == "everyone":
        prefix = f"<b>Cannot use everyone as {title}!</b>\n\n"
    elif complain:
        prefix = "<b>Invalid group name!</b> Group names must be 1-32 of <code>a-z 0-9 _ -</code>.\n\n"
    await update_menu(update, f"{prefix}Enter the new {title} name (or /cancel).\n\n{desc}", reply_markup=ForceReply())
    context.user_data.poll_group = group
    return NP_GROUP


async def newpoll_save_group(update: Update, context: AppContext):
    key = cast(str, context.user_data.poll_group)
    new_group = cast(str, cast(Message, update.message).text).strip().lower()
    if not re.match(GROUP_REGEX, new_group):
        return await newpoll_ask_group(update, context, key, "")
    if not new_group or new_group == "everyone":
        # can't put NULL into sourceGroup or electedGroup
        if key != "voterGroup":
            return await newpoll_ask_group(update, context, key, True)
        new_group = None
    pid = context.user_data.poll_edit
    pending = context.user_data.poll_pending
    # always editing
    assert pid is not None
    assert key in ("voterGroup", "sourceGroup", "electedGroup")
    cast(dict, pending)[key] = new_group
    return await newpoll_main_menu(update, context, pid)


async def newpoll_created(update: Update, context: AppContext, pid: int, *, is_election: bool):
    return await newpoll_main_menu(
        update, context, pid, top=f"<b>{'Election' if is_election else 'Poll'} created! Id:</b> <code>{pid}</code>"
    )


def newpoll_create(pending: PendingPoll, *, is_election: bool):
    text_fi = pending.get("textFi")
    text_en = pending.get("textEn")
    assert text_fi and text_en
    opts_fi = cast(list[str], pending.get("opts_fi"))
    opts_en = cast(list[str], pending.get("opts_en"))
    if not is_election:
        assert opts_fi and opts_en
    with db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO polls (type, perArea, textFi, textEn) VALUES (?, ?, ?, ?)",
            ["election" if is_election else "question", is_election, text_fi, text_en],
        )
        pid = cur.lastrowid
        assert pid
        if not is_election:
            cur.executemany(
                "INSERT INTO options (pollId, textFi, textEn, orderNo) VALUES (?, ?, ?, ?)",
                [[pid, fi, en, num] for num, (fi, en) in enumerate(zip(opts_fi, opts_en))],
            )
    return pid


def newpoll_commit(pid: int, is_election: bool, pending: PendingPoll):
    if not pending:
        return
    with db:
        cur = db.cursor()
        fields = ["updatedAt=CURRENT_TIMESTAMP"]
        values = []
        field_names = ["textFi", "textEn", "perArea", "voterGroup"]
        if is_election:
            field_names.extend(
                ["sourceGroup", "electedGroup", "replaceElectedGroup", "electedGroupEligible", "electedGroupVoting"]
            )
        for field in field_names:
            if field in pending:
                fields.append(f"{field}=?")
                values.append(pending[field])

        cur.execute(f"UPDATE polls SET {', '.join(fields)} WHERE id=?", [*values, pid])
        if (not is_election) and "opts_fi" in pending:
            assert "opts_en" in pending
            cur.execute("DELETE FROM options WHERE pollId = ?", [pid])
            cur.executemany(
                "INSERT INTO options (pollId, textFi, textEn, orderNo) VALUES (?, ?, ?, ?)",
                [[pid, fi, en, num] for num, (fi, en) in enumerate(zip(pending["opts_fi"], pending["opts_en"]))],
            )


async def newpoll_cancel_ask(update: Update, context: AppContext):
    if (pid := context.user_data.poll_edit) is not None:
        return await newpoll_main_menu(update, context, pid, force_edit=True)
    else:
        return await newpoll_cancel(update, context)


async def newpoll_cancel(update: Update, context: AppContext):
    if (pid := context.user_data.poll_edit) is None:
        await update_menu(update, "Poll creation cancelled.", reply_markup=None)
        return END
    else:
        context.user_data.poll_edit = None
        context.user_data.poll_pending = {}
        return await newpoll_main_menu(update, context, pid, top="<b>Edits discarded.</b>")


def newpoll_menu_text(poll, top=None, bottom=None, pending: PendingPoll = {}):
    is_election = poll["type"] == "election"
    merged = {**poll, **pending}
    text = f"{escape(merged['textFi'])}\n\n{escape(merged['textEn'])}\n\n"
    if not is_election:
        if "opts_fi" in pending:
            assert "opts_en" in pending
            opts = list(zip(pending["opts_fi"], pending["opts_en"]))
        else:
            opts = db.execute(
                "SELECT textFi, textEn FROM options WHERE pollId = ? ORDER BY orderNo ASC", [poll["id"]]
            ).fetchall()
        if not opts:
            text += "No options!"
        text += "\n".join(f"- {escape(fi)} / {escape(en)}" for fi, en in opts)
        text += "\n\n"
    text += f"Voting per area: <b>{'yes' if merged['perArea'] else 'no'}</b>"
    text += f"\nVoting: "
    text += f"<code>{escape(merged['voterGroup'])}</code>" if merged["voterGroup"] else "<b>everyone</b>"
    if is_election:
        elected_group = merged["electedGroup"]
        text += f"\nCandidates: "
        text += f"<code>{escape(merged['sourceGroup'])}</code>" if merged["sourceGroup"] else "<b>everyone</b>"
        text += f"\n{'Elected <b>replace members of</b>' if merged['replaceElectedGroup'] else 'Elected <b>added to</b>'}: <code>{escape(elected_group)}</code>"
        text += f"\nCurrent <code>{escape(elected_group)}</code> eligible: <b>{'yes' if merged['electedGroupEligible'] else 'no'}</b>"
        text += f"\nCurrent <code>{escape(elected_group)}</code> can vote: <b>{'yes' if merged['electedGroupVoting'] else 'no'}</b>"
    if top:
        text = f"{top}\n\n{text}"
    if bottom:
        text = f"{text}\n\n{bottom}"
    return text


async def newpoll_callback(update: Update, context: AppContext):
    callback_query = cast(CallbackQuery, update.callback_query)
    action, pid = (callback_query.data or "").split(":")
    pid = int(pid)

    # read data of poll from db
    poll = db.execute("SELECT * FROM polls WHERE id = ?", [pid]).fetchone()
    if poll is None:
        await callback_query.answer("Poll not found!")
        await update_menu(update, "Poll not found!", reply_markup=None)
        return END
    assert poll["type"] in ("election", "question")
    context.user_data.poll_edit = pid
    context.user_data.poll_is_election = is_election = poll["type"] == "election"

    # reset pending edits unless edit-related action
    if not (action.startswith("np_edit") or action in ("np_commit", "np_revert")):
        context.user_data.poll_pending = {}

    # don't allow editing polls after opening
    if (action.startswith("np_edit") or action == "np_commit") and poll["status"] != "created":
        await callback_query.answer("Poll already active!")
        context.user_data.poll_pending = {}
        return await newpoll_main_menu(update, context, pid, poll)

    merged = {
        **poll,
        **context.user_data.poll_pending,
    }
    match action:
        case "np_edit_qfi":
            await callback_query.answer()
            return await newpoll_ask_question(update, context, "fi")
        case "np_edit_qen":
            await callback_query.answer()
            return await newpoll_ask_question(update, context, "en")
        case "np_edit_vg":
            await callback_query.answer()
            return await newpoll_ask_group(update, context, "voterGroup")
        case "np_edit_pa":
            await callback_query.answer()
            context.user_data.poll_pending["perArea"] = not merged["perArea"]
            poll = {**poll, "perArea": not merged["perArea"]}
            return await newpoll_main_menu(update, context, pid, poll)

        case "np_edit_ofi" | "np_edit_oen" if is_election:
            await callback_query.answer("Can't edit options on election!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_edit_ofi":
            await callback_query.answer()
            return await newpoll_ask_options(update, context, "fi")
        case "np_edit_oen":
            await callback_query.answer()
            return await newpoll_ask_options(update, context, "en")

        case "np_edit_sg" | "np_edit_eg" | "np_edit_reg" | "np_edit_ege" | "np_edit_egv" if not is_election:
            await callback_query.answer("Can't edit options on poll!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_edit_sg":
            await callback_query.answer()
            return await newpoll_ask_group(update, context, "sourceGroup")
        case "np_edit_eg":
            await callback_query.answer()
            return await newpoll_ask_group(update, context, "electedGroup")
        case "np_edit_reg":
            await callback_query.answer()
            context.user_data.poll_pending["replaceElectedGroup"] = not merged["replaceElectedGroup"]
            poll = {**poll, "replaceElectedGroup": not merged["replaceElectedGroup"]}
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_edit_ege":
            await callback_query.answer()
            context.user_data.poll_pending["electedGroupEligible"] = not merged["electedGroupEligible"]
            poll = {**poll, "electedGroupEligible": not merged["electedGroupEligible"]}
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_edit_egv":
            await callback_query.answer()
            context.user_data.poll_pending["electedGroupVoting"] = not merged["electedGroupVoting"]
            poll = {**poll, "electedGroupVoting": not merged["electedGroupVoting"]}
            return await newpoll_main_menu(update, context, pid, poll)

        case "np_edit":
            await callback_query.answer()
            return await newpoll_edit_menu(update, context, pid, poll)
        case "np_revert":
            await callback_query.answer("Edits discarded.")
            context.user_data.poll_pending = {}
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Edits discarded.</b>")
        case "np_commit":
            await callback_query.answer("Poll saved.")
            newpoll_commit(pid, is_election, context.user_data.poll_pending)
            await admin_log(f"edited the poll <b>{escape(poll['textFi'])}</b>.", update, context)
            context.user_data.poll_pending = {}
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
                db.execute("UPDATE polls SET status='active', updatedAt=CURRENT_TIMESTAMP WHERE pid=?", [pid])
            await admin_log(f"activated the poll <b>{escape(poll['textFi'])}</b>.", update, context)
            # TODO: reopen polls for users
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
            await admin_log(f"announced the poll <b>{escape(poll['textFi'])}</b>.", update, context)
            # TODO: announce polls for users
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
            await admin_log(f"closed the poll <b>{escape(poll['textFi'])}</b>.", update, context)
            # TODO: close polls for users
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Poll closed.</b>")
        case _:
            await callback_query.answer()
            return await newpoll_main_menu(update, context, pid, poll)


async def newpoll_edit_menu(update: Update, context: AppContext, pid: int, poll: Any):
    is_election = poll["type"] == "election"
    pending = context.user_data.poll_pending
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
                *(
                    (
                        [
                            InlineKeyboardButton("Options 🇫🇮", callback_data=f"np_edit_ofi:{pid}"),
                            InlineKeyboardButton("Options 🇬🇧", callback_data=f"np_edit_oen:{pid}"),
                        ],
                    )
                    if not is_election
                    else ()
                ),
                [
                    InlineKeyboardButton("Voter group", callback_data=f"np_edit_vg:{pid}"),
                    InlineKeyboardButton("Per-area", callback_data=f"np_edit_pa:{pid}"),
                ],
                *(
                    (
                        [
                            InlineKeyboardButton("Cand. group", callback_data=f"np_edit_sg:{pid}"),
                            InlineKeyboardButton("Elected group", callback_data=f"np_edit_eg:{pid}"),
                        ],
                        [
                            InlineKeyboardButton("Replace elected group", callback_data=f"np_edit_reg:{pid}"),
                        ],
                        [
                            InlineKeyboardButton("Curr. eligible", callback_data=f"np_edit_ege:{pid}"),
                            InlineKeyboardButton("Curr. voting", callback_data=f"np_edit_egv:{pid}"),
                        ],
                    )
                    if is_election
                    else ()
                ),
                [InlineKeyboardButton("Discard changes", callback_data=f"np_revert:{pid}")]
                if pending
                else [InlineKeyboardButton("Cancel", callback_data=f"np_menu:{pid}")],
            ]
        ),
    )
    return NP_MENU


async def newpoll_main_menu(
    update: Update,
    context: AppContext,
    pid: int,
    poll: Any = None,
    *,
    top: str | None = None,
    bottom: str | None = None,
    force_edit=False,
):
    if poll is None:
        poll = db.execute("SELECT * FROM polls WHERE id = ?", [pid]).fetchone()
        if poll is None:
            await update_menu(update, "Poll not found!", reply_markup=None)
            return END
    if force_edit or context.user_data.poll_pending:
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


async def admin_log(
    action: str, update: Update, context: AppContext, parse_mode=ParseMode.HTML, extra_target: int | None = None
):
    target = get_kv("admin_log", None)
    user = cast(User, update.effective_user)
    message = f"{user_link(user)} {action}"
    if user.id != config["admins"][0]:
        await context.bot.send_message(config["admins"][0], message, parse_mode=parse_mode)
    if target:
        await context.bot.send_message(target, message, parse_mode=parse_mode)
    if extra_target and extra_target != target and extra_target != config["admins"][0]:
        await context.bot.send_message(extra_target, message, parse_mode=parse_mode)


async def set_admin_log(update: Update, context: AppContext):
    message = cast(Message, update.message)
    chat = cast(Chat, update.effective_chat)
    current = get_kv("admin_log", config["admins"][0])
    if chat.id == current:
        await message.reply_text("Admin actions are already logged here!")
    else:
        await admin_log(
            f"moved the admin action log to {escape(chat.effective_name or 'unnamed')} ({chat.id}).",
            update,
            context,
        )
        set_kv("admin_log", message.chat_id)
        await message.reply_text("Admin actions will now be logged here.")


async def set_initiative_log(update: Update, context: AppContext):
    message = cast(Message, update.message)
    chat = cast(Chat, update.effective_chat)
    current = get_kv("initiative_log", config["admins"][0])
    if message.chat_id == current:
        await message.reply_text("Initiatives are already handled here!")
    else:
        set_kv("initiative_log", message.chat_id)
        await admin_log(
            f"moved initiative handling to {escape(chat.effective_name or 'unnamed')} ({chat.id}).",
            update,
            context,
            extra_target=current,
        )
        await message.reply_text("Initiatives will now be handled here.")


CHOOSER_PAGE_SIZE = 5


async def poll_chooser(update: Update, context: AppContext):
    if update.callback_query:
        offset = int((update.callback_query.data or "").removeprefix("polls:"))
    else:
        offset = 0
    (poll_count,) = db.execute("SELECT COUNT(*) FROM polls").fetchone()
    polls = db.execute(
        "SELECT * FROM polls ORDER BY updatedAt DESC LIMIT ? OFFSET ?", [CHOOSER_PAGE_SIZE, offset]
    ).fetchall()
    paging: list[InlineKeyboardButton] = []
    if offset > 0:
        paging.append(InlineKeyboardButton("<<", callback_data=f"polls:{max(0, offset - CHOOSER_PAGE_SIZE)}"))
    if poll_count > offset + CHOOSER_PAGE_SIZE:
        paging.append(InlineKeyboardButton(">>", callback_data=f"polls:{max(0, offset + CHOOSER_PAGE_SIZE)}"))
    await update_menu(
        update,
        "Choose a poll to edit.",
        reply_markup=InlineKeyboardMarkup(
            [
                *([InlineKeyboardButton(poll["textFi"], callback_data=f"np_menu:{poll['id']}")] for poll in polls),
                *((paging,) if paging else ()),
            ]
        ),
    )
    return END


async def unassign_code_start(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args:
        await message.reply_text("<b>Usage:</b> <code>/unassign_code CODE</code>", parse_mode=ParseMode.HTML)
        return END
    with db:
        cur = db.cursor()
        code = context.args[0]
        user = cur.execute("SELECT * FROM users WHERE passcode = ?", [code]).fetchone()
        if user is None:
            await message.reply_text(f"No user found with code {escape(code)}!", parse_mode=ParseMode.HTML)
            return END
        if user["tgUserId"] is None:
            await message.reply_text(f"Code {escape(code)} is already unassigned!", parse_mode=ParseMode.HTML)
            return END
        cur.execute(
            "UPDATE users SET tgUserId=NULL, tgUsername=NULL, tgDisplayName=NULL, language=NULL, present=0 WHERE id = ?",
            [user["id"]],
        )
        await message.reply_text(f"Unassigned code {escape(code)} from user.", parse_mode=ParseMode.HTML)
        await admin_log(f"unassigned code {escape(code)} from user.", update, context, parse_mode=ParseMode.HTML)
    return END


async def group_list(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    groups = db.execute("SELECT `group`, COUNT(userId) AS `count` FROM groupMembers GROUP BY `group`").fetchall()
    if not groups:
        await message.reply_text("No groups currently exist.")
    else:
        await message.reply_text(
            "\n".join(f"<code>{escape(row['group'])}</code> ({row['count']} members)" for row in groups),
            parse_mode=ParseMode.HTML,
        )
    return END


async def group_view(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args:
        await message.reply_text("<b>Usage:</b> <code>/group_view group_name</code>", parse_mode=ParseMode.HTML)
        return END
    group = context.args[0]
    if not re.match(GROUP_REGEX, group):
        await message.reply_text(
            "Invalid group name! Group names must be 1-32 of <code>a-z 0-9 _ -</code>.", parse_mode=ParseMode.HTML
        )
        return END
    members = db.execute(
        """
        SELECT users.*
        FROM groupMembers
        INNER JOIN users ON users.id = groupMembers.userId
        WHERE groupMembers.`group` = ?
        """,
        [group],
    ).fetchall()
    if not members:
        await message.reply_text(f"No members currently in <code>{escape(group)}</code>.", parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(
            "\n".join(f"ID <code>{row['id']}</code> {escape(row['name'])}" for row in members),
            parse_mode=ParseMode.HTML,
        )
    return END


async def group_manip_args(message: Message, args: list[str]):
    group = args[0].strip().lower()
    if not re.match(GROUP_REGEX, group):
        await message.reply_text(
            "Invalid group name! Group names must be 1-32 of <code>a-z 0-9 _ -</code>.", parse_mode=ParseMode.HTML
        )
        return None, None
    elif group == "everyone":
        await message.reply_text("Cannot use everyone as group name.", parse_mode=ParseMode.HTML)
        return None, None
    try:
        uids = [int(uid) for uid in args[1:]]
    except Exception:
        await message.reply_text(
            "Invalid user IDs. User IDs should be numbers in the participant sheet.", parse_mode=ParseMode.HTML
        )
        return None, None
    return group, uids


async def group_add(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args or len(context.args) < 2:
        await message.reply_text("<b>Usage:</b> <code>/group_add group_name uid...</code>", parse_mode=ParseMode.HTML)
        return END
    group, uids = await group_manip_args(message, context.args)
    if group and uids:
        try:
            with db:
                cur = db.cursor()
                cur.executemany(
                    "INSERT OR IGNORE INTO groupMembers (`userId`, `group`) VALUES (?, ?)",
                    [[uid, group] for uid in uids],
                )
                changed = cur.rowcount
        except IntegrityError:
            await message.reply_text(
                "Some nonexistent user IDs. Check your IDs from the participant sheet.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text(
                f"Added {changed} users to <code>{escape(group)}</code>.", parse_mode=ParseMode.HTML
            )
            await admin_log(f"added {changed} users to <code>{escape(group)}</code>.", update, context)
    return END


async def group_remove(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args or len(context.args) < 2:
        await message.reply_text(
            "<b>Usage:</b> <code>`/group_remove group_name uid...</code>", parse_mode=ParseMode.HTML
        )
        return END
    group, uids = await group_manip_args(message, context.args)
    if group and uids:
        with db:
            cur = db.cursor()
            cur.execute(
                f"DELETE FROM groupMembers WHERE `group`=? AND userId IN ({', '.join('?' * len(uids))})",
                [group, *uids],
            )
            changed = cur.rowcount
            await message.reply_text(
                f"Removed {changed} users from <code>{escape(group)}</code>.", parse_mode=ParseMode.HTML
            )
            await admin_log(f"removed {changed} users from <code>{escape(group)}</code>.", update, context)
    return END


admin_entry = [
    CommandHandler("start", handle_admin_start, ADMIN & ~UpdateType.EDITED),
    CommandHandler("grant", handle_grant, ADMIN & ~UpdateType.EDITED),
    CommandHandler("deny", handle_deny, CONFIG_ADMIN & ~UpdateType.EDITED),
    CommandHandler("newpoll", newpoll_start, ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("newelection", newpoll_start_election, ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("polls", poll_chooser, ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("admin_log", set_admin_log, ADMIN & ~UpdateType.EDITED),
    CommandHandler("initiative_log", set_initiative_log, ADMIN & ~UpdateType.EDITED),
    CommandHandler("unassign_code", unassign_code_start, ADMIN & ~UpdateType.EDITED),
    CommandHandler("group_list", group_list, ADMIN & ~UpdateType.EDITED),
    CommandHandler("group_view", group_view, ADMIN & ~UpdateType.EDITED),
    CommandHandler("group_add", group_add, ADMIN & ~UpdateType.EDITED),
    CommandHandler("group_remove", group_remove, ADMIN & ~UpdateType.EDITED),
    AdminCallbackQueryHandler(newpoll_callback, pattern=r"^np_\w+:\d+$"),
    AdminCallbackQueryHandler(poll_chooser, pattern=r"^polls:\d+$"),
]

admin_states = {
    NP_QUESTION: [
        MessageHandler(TEXT & ~COMMAND, newpoll_save_question),
        CommandHandler("cancel", newpoll_cancel_ask, ~UpdateType.EDITED),
    ],
    NP_OPTIONS: [
        MessageHandler(TEXT & ~COMMAND, newpoll_save_options),
        CommandHandler("cancel", newpoll_cancel_ask, ~UpdateType.EDITED),
    ],
    NP_GROUP: [
        MessageHandler(TEXT & ~COMMAND, newpoll_save_group),
        CommandHandler("cancel", newpoll_cancel_ask, ~UpdateType.EDITED),
    ],
    NP_MENU: [
        CommandHandler("cancel", newpoll_cancel, ~UpdateType.EDITED),
    ],
}

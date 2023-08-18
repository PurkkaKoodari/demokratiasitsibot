import re
from collections import Counter
from sqlite3 import IntegrityError
from time import time
from typing import Any, cast

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    ReplyKeyboardRemove,
    Update,
    User,
)
from telegram.constants import ChatMemberStatus, ParseMode, ChatType as ChatTypeEnum
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler
from telegram.ext.filters import COMMAND, TEXT, ChatType, UpdateType

from config import config
from db import db, get_kv, set_kv
from filters import ADMIN, CONFIG_ADMIN, db_admins, AdminCallbackQueryHandler, config_admins, banned_admins
from help import admin_commands, user_commands, admin_help, special_groups_help
from langs import lang_icons
from shared import (
    get_group_member_ids,
    admin_log,
    send_poll,
    close_poll,
    reopen_poll,
    get_group_member_users,
    ignore_errors,
)
from typings import AppContext, PendingPoll, PendingBroadcast, PollState
from util import escape, grouplist

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
        admin_help,
        parse_mode=ParseMode.HTML,
    )
    commands = (admin_commands + user_commands["en"][1:]) if chat.type == ChatTypeEnum.PRIVATE else admin_commands
    await context.bot.set_my_commands(
        [BotCommand(cmd, desc) for cmd, _, desc in commands], scope=BotCommandScopeChat(chat_id=chat.id)
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
            with ignore_errors(filter="not modified"):
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
            await admin_log(f"created the poll <b>{escape(cast(str, pending.get('textFi')))}</b>.", update, context)
            context.user_data.poll_pending = {}
            return await newpoll_created(update, context, pid, is_election=False)
        else:
            return await newpoll_ask_options(update, context, other_lang)


async def newpoll_ask_group(update: Update, context: AppContext, group: str, complain=False):
    match group:
        case "voterGroup":
            title = "voter group"
            desc = "This group of users will see the poll and can vote."
        case "sourceGroup":
            title = "candidate group"
            desc = "This group of users will be candidates for the election."
        case _:
            raise AssertionError("bad group")
    prefix = ""
    if complain:
        prefix = "<b>Invalid group name!</b> Group names must be 1-32 of <code>a-z 0-9 _ -</code>.\n\n"
    await update_menu(
        update,
        f"{prefix}Enter the new {title} name (or /cancel).\n\n{desc}\n\n{special_groups_help}",
        reply_markup=ForceReply(),
    )
    context.user_data.poll_group = group
    return NP_GROUP


async def newpoll_save_group(update: Update, context: AppContext):
    key = cast(str, context.user_data.poll_group)
    new_group = cast(str, cast(Message, update.message).text).strip().lower()
    if not re.match(GROUP_REGEX, new_group):
        return await newpoll_ask_group(update, context, key, True)
    pid = context.user_data.poll_edit
    pending = context.user_data.poll_pending
    # always editing
    assert pid is not None
    assert key in ("voterGroup", "sourceGroup")
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
            field_names.append("sourceGroup")
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
    text += f"\nVoting: <code>{escape(merged['voterGroup'])}</code>"
    if is_election:
        text += f"\nCandidates: <code>{escape(merged['sourceGroup'])}</code>"
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
    if (action.startswith("np_edit") or action == "np_commit") and poll["status"] != PollState.created:
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

        case "np_edit_sg" if not is_election:
            await callback_query.answer("Can't edit options on poll!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_edit_sg":
            await callback_query.answer()
            return await newpoll_ask_group(update, context, "sourceGroup")

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

        case "np_activate" | "np_activate2" if poll["status"] == PollState.active:
            await callback_query.answer("Poll already active!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_activate":
            await callback_query.answer()
            bottom = (
                "<b>Are you sure you want to ACTIVATE this poll?</b>"
                if poll["status"] == PollState.created
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
                if poll["status"] == PollState.created and is_election:
                    # generate options
                    db.execute("DELETE FROM options WHERE pollId = ?", [poll["id"]])
                    candidates = get_group_member_users(poll["sourceGroup"])
                    candidates = [cand for cand in candidates if cand["candidateNumber"]]
                    if poll["perArea"]:
                        voters = get_group_member_users(poll["voterGroup"])
                        cand_areas = Counter(cand["area"] for cand in candidates)
                        voter_areas = {voter["area"] for voter in voters}
                        missing_areas = voter_areas - set(cand_areas)
                        if missing_areas:
                            await callback_query.answer(
                                f"Some areas don't have candidates: " + ", ".join(missing_areas),
                                show_alert=True,
                            )
                            return NP_MENU
                        elif (max_cands := max(cand_areas.values())) > config["election"]["max_candidates"]:
                            await callback_query.answer(
                                f"There are too many candidates for an area: {max_cands} > {config['election']['max_candidates']}",
                                show_alert=True,
                            )
                            return NP_MENU
                    elif not candidates:
                        await callback_query.answer(
                            f"There are no candidates!",
                            show_alert=True,
                        )
                        return NP_MENU
                    elif len(candidates) > config["election"]["max_candidates"]:
                        await callback_query.answer(
                            f"There are too many candidates: {len(candidates)} > {config['election']['max_candidates']}",
                            show_alert=True,
                        )
                        return NP_MENU
                    candidates.sort(key=lambda cand: int(cand["candidateNumber"]))
                    options = [
                        (
                            cand["id"],
                            cand["area"] if poll["perArea"] else None,
                            f"{cand['candidateNumber']} {cand['name']}",
                        )
                        for cand in candidates
                    ]
                    db.executemany(
                        "INSERT INTO options (pollId, candidateId, area, textFi, textEn, orderNo) VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            [poll["id"], cand_id, cand_area, cand_text, cand_text, num]
                            for num, (cand_id, cand_area, cand_text) in enumerate(options)
                        ],
                    )
                db.execute(
                    f"UPDATE polls SET status='{PollState.active}', updatedAt=CURRENT_TIMESTAMP WHERE id=?", [pid]
                )
            verb = "reactivated" if poll["status"] != PollState.created else "activated"
            await callback_query.answer(f"Poll {verb}.")
            await admin_log(
                f"{verb} the poll <b>{escape(poll['textFi'])}</b>.",
                update,
                context,
            )
            if poll["status"] != PollState.created:
                context.application.create_task(reopen_poll(context, pid))
            poll = {**poll, "status": PollState.active}
            return await newpoll_main_menu(update, context, pid, poll, top=f"<b>Poll {verb}.</b>")

        case "np_announce" | "np_announce2" | "np_close" | "np_close2" if poll["status"] != PollState.active:
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
            await callback_query.answer("Poll announced.")
            await admin_log(f"announced the poll <b>{escape(poll['textFi'])}</b>.", update, context)
            context.application.create_task(send_poll(context, pid))
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Poll announced.</b>")
        case "np_close":
            await callback_query.answer()
            await update_menu(
                update,
                newpoll_menu_text(poll, bottom="<b>Are you sure you want to close this poll?</b>"),
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
                db.execute(
                    f"UPDATE polls SET status='{PollState.closed}', updatedAt=CURRENT_TIMESTAMP WHERE id=?", [pid]
                )
            await callback_query.answer("Poll closed.")
            await admin_log(f"closed the poll <b>{escape(poll['textFi'])}</b>.", update, context)
            context.application.create_task(close_poll(context, pid))
            poll = {**poll, "status": PollState.closed}
            return await newpoll_main_menu(update, context, pid, poll, top="<b>Poll closed.</b>")

        case "np_results" if poll["status"] != PollState.closed:
            await callback_query.answer("Poll is not closed!")
            return await newpoll_main_menu(update, context, pid, poll)
        case "np_results":
            await callback_query.answer()
            result = escape(poll["textFi"])
            if poll["perArea"]:
                votes = db.execute(
                    """
                    SELECT options.textFi, votes.area, options.candidateId, COUNT(*) AS count
                    FROM votes
                    INNER JOIN options ON votes.optionId = options.id
                    WHERE votes.pollId = ?
                    GROUP BY votes.optionId, votes.area
                    ORDER BY votes.area ASC, count DESC
                    """,
                    [poll["id"]],
                ).fetchall()
                by_area = grouplist(votes, lambda vote: vote["area"])
            else:
                votes = db.execute(
                    """
                    SELECT options.textFi, options.candidateId, COUNT(*) AS count
                    FROM votes
                    INNER JOIN options ON votes.optionId = options.id
                    WHERE votes.pollId = ?
                    GROUP BY votes.optionId
                    ORDER BY count DESC
                    """,
                    [poll["id"]],
                ).fetchall()
                by_area = {None: votes}
            for area, votes in by_area.items():
                if area is not None:
                    result += f"\n\n<b>Results in area {escape(area)}</b>:"
                else:
                    result += f"\n\n<b>Results</b>:"
                for row in votes:
                    result += "\n"
                    if row["candidateId"] is not None:
                        result += f"(UID <code>{row['candidateId']}</code>) "
                    result += f"{escape(row['textFi'])}: {row['count']} votes"
                if not votes:
                    result += "\nNo votes."
            return await callback_query.edit_message_text(text=result, parse_mode=ParseMode.HTML, reply_markup=None)

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
                *(([InlineKeyboardButton("Cand. group", callback_data=f"np_edit_sg:{pid}")],) if is_election else ()),
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
                *(
                    ([InlineKeyboardButton("Edit", callback_data=f"np_edit:{pid}")],)
                    if poll["status"] == PollState.created
                    else ()
                ),
                [
                    InlineKeyboardButton("Activate", callback_data=f"np_activate:{pid}")
                    if poll["status"] == PollState.created
                    else InlineKeyboardButton("Close", callback_data=f"np_close:{pid}")
                    if poll["status"] == PollState.active
                    else InlineKeyboardButton("Reopen", callback_data=f"np_activate:{pid}"),
                ],
                *(
                    ([InlineKeyboardButton("Announce", callback_data=f"np_announce:{pid}")],)
                    if poll["status"] == PollState.active
                    else ([InlineKeyboardButton("Results", callback_data=f"np_results:{pid}")],)
                    if poll["status"] == PollState.closed
                    else ()
                ),
            ]
        ),
    )
    return NP_MENU


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
    status_labels = {
        PollState.active: "[ACTIVE] ",
        PollState.closed: "[CLOSED] ",
    }
    await update_menu(
        update,
        "Choose a poll to edit.",
        reply_markup=InlineKeyboardMarkup(
            [
                *(
                    [
                        InlineKeyboardButton(
                            status_labels.get(poll["status"], "") + poll["textFi"],
                            callback_data=f"np_menu:{poll['id']}",
                        )
                    ]
                    for poll in polls
                ),
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


async def group_arg(message: Message, arg: str, *, allow_special=False):
    group = arg.strip().lower()
    if not re.match(GROUP_REGEX, group):
        await message.reply_text(
            "Invalid group name! Group names must be 1-32 of <code>a-z 0-9 _ -</code>.", parse_mode=ParseMode.HTML
        )
        return None
    elif group in ("everyone", "absent", "present") and not allow_special:
        await message.reply_text(f"Cannot use {group} as group name.", parse_mode=ParseMode.HTML)
        return None
    return group


async def uids_args(message, args: list[str], *, allow_groups=True):
    uids = set()
    for arg in args:
        try:
            uids.add(int(arg))
        except ValueError:
            if not allow_groups or not re.match(GROUP_REGEX, arg):
                await message.reply_text(
                    f"Invalid user ID or group {escape(arg)}. User IDs should be numbers in the participant sheet.",
                    parse_mode=ParseMode.HTML,
                )
                return None
            members = get_group_member_ids(arg)
            if not members:
                await message.reply_text(f"No members in group {escape(arg)}.", parse_mode=ParseMode.HTML)
                return None
            uids.update(members)
    return list(uids)


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
    group = await group_arg(message, context.args[0])
    if group:
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
            await message.reply_text(
                f"No members currently in <code>{escape(group)}</code>.", parse_mode=ParseMode.HTML
            )
        else:
            await message.reply_text(
                "\n".join(f"ID <code>{row['id']}</code> {escape(row['name'])}" for row in members),
                parse_mode=ParseMode.HTML,
            )
    return END


async def group_add(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args or len(context.args) < 2:
        await message.reply_text(
            f"<b>Usage:</b> <code>/group_add to_group uid|group...</code>\n\n{special_groups_help}",
            parse_mode=ParseMode.HTML,
        )
        return END
    group = await group_arg(message, context.args[0])
    uids = await uids_args(message, context.args[1:])
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
            f"<b>Usage:</b> <code>/group_remove from_group uid|group...</code>\n\n{special_groups_help}",
            parse_mode=ParseMode.HTML,
        )
        return END
    group = await group_arg(message, context.args[0])
    uids = await uids_args(message, context.args[1:])
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


async def mark_absent(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args or not context.args:
        await message.reply_text(
            f"<b>Usage:</b> <code>/mark_absent uid...</code>\n\n{special_groups_help}", parse_mode=ParseMode.HTML
        )
        return END
    uids = await uids_args(message, context.args, allow_groups=False)
    if uids:
        with db:
            cur = db.cursor()
            cur.execute(
                f"UPDATE users SET present=0 WHERE present = 1 AND id IN ({', '.join('?' * len(uids))})",
                uids,
            )
            changed = cur.rowcount
            await message.reply_text(f"Marked {changed} users as absent.", parse_mode=ParseMode.HTML)
            await admin_log(f"marked {changed} users as absent.", update, context)
    return END


async def broadcast(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    text = cast(str, message.text)
    if " " not in text:
        await message.reply_text(
            "<b>Usage:</b> <code>/broadcast message...</code>\n\n"
            "Everything after /broadcast, including formatting, will be sent to users - be careful!",
            parse_mode=ParseMode.HTML,
        )
        return END
    utf32_offset = text.index(" ")
    utf16_offset = len(text[: utf32_offset + 1].encode("utf-16-le")) // 2
    rest = text.encode("utf-16-le")[utf16_offset * 2 :].decode("utf-16-le")
    shifted_entities: list[dict] = []
    for entity in message.entities:
        if entity.offset + entity.length <= utf16_offset:
            continue
        if entity.offset < utf16_offset:
            shift = utf16_offset - entity.offset
            shifted_entities.append({**entity.to_dict(), "offset": 0, "length": entity.length - shift})
        else:
            shifted_entities.append({**entity.to_dict(), "offset": entity.offset - utf16_offset})

    bid = str(int(time() * 1000))
    context.user_data.broadcast_pending = PendingBroadcast(bid, rest, shifted_entities)

    prefix = "Are you sure you want to broadcast this message to ALL USERS?"
    reshifted_entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=len(prefix)),
        *[MessageEntity(**{**entity, "offset": entity["offset"] + len(prefix) + 2}) for entity in shifted_entities],
    ]
    await message.reply_text(
        f"{prefix}\n\n{rest}",
        entities=reshifted_entities,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Send it!", callback_data=f"br_send:{bid}")],
                [InlineKeyboardButton("Cancel", callback_data=f"br_cancel:{bid}")],
            ]
        ),
    )
    return END


async def broadcast_callback(update: Update, context: AppContext):
    callback_query = cast(CallbackQuery, update.callback_query)
    action, bid = (callback_query.data or "").split(":")
    await callback_query.answer()

    if not context.user_data.broadcast_pending or context.user_data.broadcast_pending.id != bid:
        await callback_query.edit_message_text("Broadcast missing from memory, try again.", reply_markup=None)
        context.user_data.broadcast_pending = None
        return END

    msg = context.user_data.broadcast_pending
    context.user_data.broadcast_pending = None

    if action == "br_send":
        entities = [MessageEntity(**d) for d in msg.entities]
        # TODO: actually broadcast
        await context.bot.send_message(callback_query.from_user.id, msg.text, entities=entities)
        await callback_query.edit_message_text("Broadcast sent.", reply_markup=None)

        clean_text = msg.text
        if len(clean_text) > 1000:
            clean_text = clean_text[:1000] + "..."
        await admin_log(f"broadcast the message:\n\n{escape(clean_text)}", update, context)
        bid = int(bid)
    else:
        await callback_query.edit_message_text("Broadcast cancelled.", reply_markup=None)

    return END


async def set_initiative_alert(update: Update, context: AppContext):
    message = cast(Message, update.effective_message)
    if not context.args or not context.args:
        await message.reply_text("<b>Usage:</b> <code>/initiative_alert number...</code>", parse_mode=ParseMode.HTML)
        return END
    try:
        alerts = sorted(set(int(arg) for arg in context.args))
        if len(alerts) > 10 or not all(1 <= alert <= 200 for alert in alerts):
            raise ValueError
    except ValueError:
        await message.reply_text(
            "Invalid numbers. Initiative alert limits must be 1-200 and there must be up to 10 of them.",
            parse_mode=ParseMode.HTML,
        )
        return END
    set_kv("initiative_alerts", alerts)
    await message.reply_text(
        f"Initiative alert limits set to {', '.join(map(str, alerts))}. Initiatives already over limits will not be alerted.",
        parse_mode=ParseMode.HTML,
    )
    await admin_log(f"set the initiative alert limits to {', '.join(map(str, alerts))}.", update, context)
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
    CommandHandler("mark_absent", mark_absent, ADMIN & ~UpdateType.EDITED),
    CommandHandler("broadcast", broadcast, ADMIN & ~UpdateType.EDITED),
    CommandHandler("initiative_alert", set_initiative_alert, ADMIN & ~UpdateType.EDITED),
    AdminCallbackQueryHandler(newpoll_callback, pattern=r"^np_\w+:\d+$"),
    AdminCallbackQueryHandler(poll_chooser, pattern=r"^polls:\d+$"),
    AdminCallbackQueryHandler(broadcast_callback, pattern=r"^br_\w+:\d+$"),
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

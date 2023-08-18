import re
from collections import Counter
from random import shuffle
from typing import cast

from telegram import CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ConversationHandler

from config import config
from db import DbPoll, DbUser, db
from help import special_groups_help
from langs import lang_icons, loc, locale
from shared import GROUP_REGEX, admin_log, get_group_member_users, ignore_errors, is_member, log_errors, update_menu
from typings import AppContext, PendingPoll, PollState
from user_setup import require_setup
from util import escape, grouplist

NP_QUESTION = "np_question"
NP_OPTIONS = "np_options"
NP_GROUP = "np_group"
NP_MENU = "np_menu"
END = ConversationHandler.END


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


def newpoll_menu_text(poll: DbPoll, top=None, bottom=None, pending: PendingPoll = {}):
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
    poll: DbPoll | None = db.execute("SELECT * FROM polls WHERE id = ?", [pid]).fetchone()
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
                    candidates.sort(key=lambda cand: int(cast(str, cand["candidateNumber"])))
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


async def newpoll_edit_menu(update: Update, context: AppContext, pid: int, poll: DbPoll):
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
    poll: DbPoll | None = None,
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


CHOOSER_PAGE_SIZE = 5


async def poll_chooser(update: Update, context: AppContext):
    if update.callback_query:
        offset = int((update.callback_query.data or "").removeprefix("polls:"))
    else:
        offset = 0
    (poll_count,) = db.execute("SELECT COUNT(*) FROM polls").fetchone()
    polls: list[DbPoll] = db.execute(
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


@require_setup
async def handle_current(update: Update, context: AppContext, user: DbUser):
    message = cast(Message, update.effective_message)
    current_polls: list[DbPoll] = db.execute(f"SELECT * FROM polls WHERE status = '{PollState.active}'").fetchall()
    if not current_polls:
        await message.reply_text(loc(context)["no_current_polls"])
        return END
    for poll in current_polls:
        if not is_member(poll["voterGroup"], user):
            continue
        messages = db.execute(
            f"SELECT messageId FROM sentMessages WHERE chatId = ? AND pollId = ? AND isAdmin = FALSE",
            [message.chat_id, poll["id"]],
        ).fetchall()
        for db_msg in messages:
            async with log_errors(context):
                await context.bot.delete_message(chat_id=message.chat_id, message_id=db_msg["messageId"])
            with db:
                db.execute(
                    "DELETE FROM sentMessages WHERE chatId = ? AND messageId = ?",
                    [message.chat_id, db_msg["messageId"]],
                )
        await send_poll(context, poll, user)
    return END


@require_setup
async def poll_callback(update: Update, context: AppContext, user: DbUser):
    callback_query = cast(CallbackQuery, update.callback_query)
    action, oid = (callback_query.data or "").split(":")
    oid = int(oid)
    row = db.execute(
        """
        SELECT
            options.id AS optionId,
            options.textFi AS optionFi,
            options.textEn AS optionEn,
            options.area as optionArea,
            polls.id AS pollId,
            polls.*
        FROM options
        INNER JOIN polls ON options.pollId = polls.id
        WHERE options.id = ?
        """,
        [oid],
    ).fetchone()
    if not row:
        await callback_query.answer("Internal error - invalid option", show_alert=True)
        return END

    # handle "eiku"
    lang = cast(str, user["language"])
    if action == "vote_cancel":
        await callback_query.answer()
        _, messages, keyboards = format_poll(row, (lang,))
        opts_key = (lang, user["area"]) if row["perArea"] else lang
        if opts_key not in keyboards:
            opts_key = (lang, None)  # non-elections don't have per-area options
        with ignore_errors(filter="not modified"):
            await callback_query.edit_message_text(
                messages[lang], reply_markup=keyboards[opts_key], parse_mode=ParseMode.HTML
            )
        return END

    # validate that the user can vote on this option
    if not is_member(row["voterGroup"], user):
        await callback_query.answer(
            "Seems like you're a hacker - you can't vote in this poll. Have a beer (at your cost)", show_alert=True
        )
        return END
    if row["perArea"] and (row["optionArea"] is not None and user["area"] != row["optionArea"]):
        await callback_query.answer(
            "Seems like you're a hacker - you can't vote for that in your area. Have a beer (at your cost)",
            show_alert=True,
        )
        return END

    # validate that the poll is open
    if row["status"] == PollState.created:
        await callback_query.answer(
            "Seems like you're a hacker - poll is not open yet. Have a beer (at your cost)", show_alert=True
        )
        return END
    question = row[f"text{lang.capitalize()}"]
    if row["status"] != PollState.active:
        closed = loc(context)["poll_closed" if row["type"] != "election" else "election_closed"]
        await callback_query.answer(
            closed,
            show_alert=True,
        )
        with ignore_errors(filter="not modified"):
            await callback_query.edit_message_text(
                f"{escape(question)}\n\n<b>{closed}</b>", reply_markup=None, parse_mode=ParseMode.HTML
            )
        return END

    # prevent multiple votes
    existing_vote = db.execute(
        "SELECT 1 FROM votes WHERE pollId = ? AND voterId = ?", [row["pollId"], user["id"]]
    ).fetchone()
    if existing_vote:
        closed = loc(context)["poll_already_voted" if row["type"] != "election" else "election_already_voted"]
        await callback_query.answer(
            closed,
            show_alert=True,
        )
        with ignore_errors(filter="not modified"):
            await callback_query.edit_message_text(
                f"{escape(question)}\n\n<b>{closed}</b>", reply_markup=None, parse_mode=ParseMode.HTML
            )
        return END

    match action:
        case "vote_vote":
            await callback_query.answer()
            option = row[f"option{lang.capitalize()}"]
            key = "poll_confirm" if row["type"] != "election" else "election_confirm"
            suffix = loc(context)[key].format(option=escape(option))
            with ignore_errors(filter="not modified"):
                await callback_query.edit_message_text(
                    f"{escape(question)}\n\n<b>{suffix}</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    loc(context)["poll_confirm_yes"], callback_data=f"vote_confirm:{oid}"
                                )
                            ],
                            [InlineKeyboardButton(loc(context)["poll_confirm_no"], callback_data=f"vote_cancel:{oid}")],
                        ]
                    ),
                )
            return END
        case "vote_confirm":
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO votes (pollId, voterId, optionId, area) VALUES (?, ?, ?, ?)",
                    [row["pollId"], user["id"], row["optionId"], user["area"]],
                )
            voted = loc(context)["poll_voted" if row["type"] != "election" else "election_voted"]
            await callback_query.answer(voted)
            with ignore_errors(filter="not modified"):
                await callback_query.edit_message_text(
                    f"{escape(question)}\n\n<b>{voted}</b>", reply_markup=None, parse_mode=ParseMode.HTML
                )
            return END
        case _:
            await callback_query.answer()
            return END


def get_poll(poll_id: int | DbPoll) -> DbPoll:
    if isinstance(poll_id, int):
        return db.execute("SELECT * FROM polls WHERE id = ?", [poll_id]).fetchone()
    else:
        return poll_id


def format_poll(
    poll: int | DbPoll,
    langs: tuple[str, ...] = ("fi", "en"),
) -> tuple[DbPoll, dict[str, str], dict[str | tuple[str, str | None], InlineKeyboardMarkup]]:
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


async def send_poll(context: AppContext, poll: int | DbPoll, user: DbUser | None = None):
    langs = (cast(str, user["language"]),) if user else ("fi", "en")
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


async def close_poll(context: AppContext, poll: int | DbPoll):
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


async def reopen_poll(context: AppContext, poll: int | DbPoll):
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

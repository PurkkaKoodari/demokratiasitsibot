import re
from datetime import datetime
from math import ceil
from time import time
from typing import cast

from telegram import (
    CallbackQuery,
    Chat,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ConversationHandler

from config import config
from db import DbInitiative, DbUser, db, get_kv, set_kv
from langs import lang_icons, loc, locale
from shared import admin_log, ignore_errors, log_errors, update_menu
from typings import AppContext, InitiativeState, PendingInitiative
from user_setup import require_setup
from util import escape

INIT_TITLE = "init_title"
INIT_DESC = "init_desc"
INIT_CHECK = "init_check"
IADM_TITLE = "iadm_title"
IADM_DESC = "iadm_desc"
IADM_MENU = "iadm_menu"
END = ConversationHandler.END


def initiative_create_allowed(user: DbUser, context: AppContext):
    if user["initiativeBanUntil"]:
        until = datetime.fromtimestamp(user["initiativeBanUntil"])
        if until > datetime.now():
            mins = ceil((until - datetime.now()).seconds / 60)
            return loc(context)["init_banned"].format(mins=mins)
    existing = db.execute(
        f"SELECT 1 FROM initiatives WHERE userId = ? AND status = '{InitiativeState.submitted}'",
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
            iid = initiative_create(user, context.user_data.init_pending)
            await callback_query.edit_message_text(
                loc(context)["init_sent"],
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
            async with log_errors(context):
                await send_initiative_admin(context, iid, auto=True)
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
        lang_suffix = cast(str, user["language"]).capitalize()
        lang_cols = f"title{lang_suffix}, desc{lang_suffix}"
        cur = db.cursor()
        cur.execute(
            f"INSERT INTO initiatives (userId, {lang_cols}) VALUES (?, ?, ?)",
            [user["id"], data["title"], data["desc"]],
        )
        iid = cur.lastrowid
        assert iid
        return iid


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
    tg_user = cast(User, update.effective_user)
    # choose the 1) approved initiative 2) the user has not signed yet 3) and has passed the fewest times
    chosen_init = db.execute(
        f"""
        SELECT initiatives.id, COALESCE(initiativeChoices.passCount, 0) AS realPassCount
        FROM initiatives
        LEFT JOIN initiativeChoices ON initiativeChoices.initiativeId = initiatives.id AND initiativeChoices.userId = ?
        WHERE initiatives.status = '{InitiativeState.approved}' AND realPassCount >= 0
        ORDER BY realPassCount ASC
        LIMIT 1
        """,
        [user["id"]],
    ).fetchone()
    if not chosen_init:
        msg = loc(context)["init_no_more"]
        if not user["initiativeNotifs"]:
            msg += loc(context)["init_no_more_notifs"]
        await tg_user.send_message(msg, parse_mode=ParseMode.HTML)
        return END
    init = get_initiative(chosen_init["id"])
    assert init
    # delete existing messages
    messages = db.execute(
        f"SELECT messageId FROM sentMessages WHERE chatId = ? AND initiativeId = ? AND isAdmin = FALSE",
        [tg_user.id, init["id"]],
    ).fetchall()
    for db_msg in messages:
        async with log_errors(context):
            await context.bot.delete_message(chat_id=tg_user.id, message_id=db_msg["messageId"])
        with db:
            db.execute(
                "DELETE FROM sentMessages WHERE chatId = ? AND messageId = ?",
                [tg_user.id, db_msg["messageId"]],
            )
    # send new message
    await send_initiative_users(context, init, user=user)
    return END


@require_setup
async def initiatives_callback(update: Update, context: AppContext, user: DbUser):
    callback_query = cast(CallbackQuery, update.callback_query)
    action, iid = (callback_query.data or "").split(":")
    iid = int(iid)
    init = get_initiative(iid)
    if not init:
        await callback_query.answer("Internal error - invalid initiative", show_alert=True)
        return END

    lang = cast(str, user["language"])
    if init["status"] == InitiativeState.closed:
        closed = loc(context)["init_closed"]
        await callback_query.answer(
            closed,
            show_alert=True,
        )
        with ignore_errors(filter="not modified"):
            await callback_query.edit_message_text(
                initiative_users_text(init, lang, new=False, bottom=f"<b>{closed}</b>"),
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
        return END

    existing = db.execute(
        "SELECT 1 FROM initiativeChoices WHERE userId = ? AND initiativeId = ? AND passCount < 0",
        [user["id"], init["id"]],
    ).fetchone()
    if existing:
        voted = loc(context)["init_seconded"]
        await callback_query.answer(voted)
        with ignore_errors(filter="not modified"):
            await callback_query.edit_message_text(
                initiative_users_text(init, lang, new=False, bottom=f"<b>{voted}</b>"),
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
        return END

    if init["status"] != InitiativeState.approved:
        await callback_query.answer(
            "Seems like you're a hacker - that initiative is not open. Have a beer (at your cost)", show_alert=True
        )
        return END

    match action:
        case "inits_pass":
            await callback_query.answer()
            with db:
                db.execute(
                    """
                    INSERT INTO initiativeChoices (userId, initiativeId, passCount)
                    VALUES (?, ?, 1)
                    ON CONFLICT DO UPDATE SET passCount = passCount + 1 WHERE passCount > 0
                    """,
                    [user["id"], init["id"]],
                )
            return await handle_initiatives(update, context)

        case "inits_sign":
            await callback_query.answer()
            with ignore_errors(filter="not modified"):
                await callback_query.edit_message_text(
                    initiative_users_text(
                        init, lang, new=False, bottom=f"<b>{loc(context)['init_second_confirm']}</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    loc(context)["init_second_confirm_yes"], callback_data=f"inits_sign2:{iid}"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    loc(context)["init_second_confirm_no"], callback_data=f"inits_cancel:{iid}"
                                )
                            ],
                        ]
                    ),
                )
            return END
        case "inits_sign2":
            voted = loc(context)["init_seconded"]
            await callback_query.answer(voted)
            with db:
                db.execute(
                    "REPLACE INTO initiativeChoices (userId, initiativeId, passCount) VALUES (?, ?, -1)",
                    [user["id"], init["id"]],
                )
                new_count = db.execute(
                    "SELECT COUNT(*) AS signCount FROM initiativeChoices WHERE initiativeId = ? AND passCount = -1",
                    [init["id"]],
                ).fetchone()["signCount"]
                db.execute("UPDATE initiatives SET signCount = ? WHERE id = ?", [new_count, init["id"]])
            # send alert if necessary
            limits: list[int] = get_kv("initiative_alerts", config["initiatives"]["default_alerts"])
            if any(init["signCount"] < limit <= new_count for limit in limits):
                await send_initiative_admin(context, init, milestone=new_count)
            with ignore_errors(filter="not modified"):
                await callback_query.edit_message_text(
                    initiative_users_text(init, lang, new=False, bottom=f"<b>{voted}</b>"),
                    reply_markup=None,
                    parse_mode=ParseMode.HTML,
                )
            return await handle_initiatives(update, context)

        case _:
            await callback_query.answer()
            with ignore_errors(filter="not modified"):
                await callback_query.edit_message_text(
                    initiative_users_text(init, lang, new=False),
                    parse_mode=ParseMode.HTML,
                    reply_markup=initiative_sign_keyboard(init, lang),
                )


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


async def set_initiative_log(update: Update, context: AppContext):
    message = cast(Message, update.message)
    chat = cast(Chat, update.effective_chat)
    current = get_kv("initiative_log", config["admins"][0])
    if chat.id != current:
        set_kv("initiative_log", chat.id)
        await admin_log(
            f"moved initiative handling to {escape(chat.effective_name or 'unnamed')} ({chat.id}).",
            update,
            context,
            extra_target=current,
        )
        await message.reply_text("Initiatives will now be handled here.")
    await send_next_initiative_admin(context, auto=False, target=chat.id)


async def iadm_start(update: Update, context: AppContext, iid: int):
    user = cast(User, update.effective_user)
    message = cast(Message, update.effective_message)
    curr_handler = context.bot_data.init_handlers.get(iid)
    if curr_handler:
        curr_user, curr_name, curr_expiry = curr_handler
        if curr_user != user.id and time() < curr_expiry:
            secs = ceil(curr_expiry - time())
            await message.reply_text(
                f"This initiative is currently being handled by {escape(curr_name)}. Try again in {secs} seconds.",
                parse_mode=ParseMode.HTML,
            )
            return END
    context.bot_data.init_handlers[iid] = (
        user.id,
        user.full_name,
        time() + config["initiatives"]["handle_cooldown"],
    )
    await iadm_main_menu(update, iid)
    return END


async def iadm_ask_title(update: Update, context: AppContext, lang: str, prefix=""):
    await update_menu(
        update,
        f"{prefix}Enter a new title for the initiative in {lang_icons[lang]} (or /cancel) (max {config['initiatives']['title_max_len']} chars)",
        reply_markup=ForceReply(),
    )
    context.user_data.iadm_lang = lang
    return IADM_TITLE


async def iadm_save_title(update: Update, context: AppContext):
    message = cast(Message, update.message)
    new_title = clean_whitespace(cast(str, message.text))
    if not new_title:
        return IADM_TITLE
    iid = context.user_data.iadm_edit
    if not iid:
        return END
    lang = context.user_data.iadm_lang
    assert lang in ("fi", "en")
    if len(new_title) > config["initiatives"]["title_max_len"]:
        return await iadm_ask_title(
            update, context, lang, prefix=f"<b>Maximum length is {config['initiatives']['title_max_len']}!</b>\n\n"
        )
    with db:
        db.execute(f"UPDATE initiatives SET title{lang.capitalize()}=? WHERE id = ?", [new_title, iid])
    return await iadm_main_menu(update, iid)


async def iadm_ask_desc(update: Update, context: AppContext, lang: str, prefix=""):
    await update_menu(
        update,
        f"{prefix}Enter a new description for the initiative in {lang_icons[lang]} (or /cancel) (max {config['initiatives']['desc_max_len']} chars)",
        reply_markup=ForceReply(),
    )
    context.user_data.iadm_lang = lang
    return IADM_DESC


async def iadm_save_desc(update: Update, context: AppContext):
    message = cast(Message, update.message)
    new_desc = clean_whitespace(cast(str, message.text))
    if not new_desc:
        return IADM_DESC
    iid = context.user_data.iadm_edit
    if not iid:
        return END
    lang = context.user_data.iadm_lang
    assert lang in ("fi", "en")
    if len(new_desc) > config["initiatives"]["desc_max_len"]:
        return await iadm_ask_desc(
            update, context, lang, prefix=f"<b>Maximum length is {config['initiatives']['desc_max_len']}!</b>\n\n"
        )
    with db:
        db.execute(f"UPDATE initiatives SET desc{lang.capitalize()}=? WHERE id = ?", [new_desc, iid])
    return await iadm_main_menu(update, iid)


def iadm_menu_text(init: DbInitiative, top="", bottom="", new=False):
    parts = []
    if top:
        parts.append(top)
    parts.append(f"<b>{'New i' if new else 'i'}nitiative by {escape(init['userName'])}</b>")
    for lang in ("fi", "en"):
        title = init["titleFi" if lang == "fi" else "titleEn"]
        desc = init["descFi" if lang == "fi" else "descEn"]
        item = f"<b>{lang_icons[lang]}</b>\n"
        item += f"<b>{escape(title)}</b>\n" if title else "<b><i>title missing</i></b>\n"
        item += escape(desc) if desc else "<i>description missing</i>"
        parts.append(item)
    match init["status"]:
        case InitiativeState.approved:
            bottom = f"<b>This initiative is approved and can be voted on.</b>\n<b>Signatures: {init['signCount']}</b>"
        case InitiativeState.unconst:
            bottom = "<b>This initiative was marked as unconstitutional.</b>"
        case InitiativeState.shitpost:
            bottom = "<b>This initiative was marked as a shitpost.</b>"
        case InitiativeState.closed:
            bottom = "<b>This initiative has been closed for signatures.</b>\n<b>Signatures: {init['signCount']}</b>"
    if bottom:
        parts.append(bottom)
    return "\n\n".join(parts)


def iadm_menu_keyboard(init: DbInitiative, private=True, bot_link=""):
    if not private:
        assert bot_link
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Handle in private chat", url=f"{bot_link}?start=init_adm_{init['id']}")]]
        )
    if init["status"] == InitiativeState.approved:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Close signatures", callback_data=f"iadm_close:{init['id']}")]]
        )
    if init["status"] != InitiativeState.submitted:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Title 🇫🇮", callback_data=f"iadm_edit_tfi:{init['id']}"),
                InlineKeyboardButton("Title 🇬🇧", callback_data=f"iadm_edit_ten:{init['id']}"),
            ],
            [
                InlineKeyboardButton("Description 🇫🇮", callback_data=f"iadm_edit_dfi:{init['id']}"),
                InlineKeyboardButton("Description 🇬🇧", callback_data=f"iadm_edit_den:{init['id']}"),
            ],
            [InlineKeyboardButton("Approve", callback_data=f"iadm_approve:{init['id']}")],
            [InlineKeyboardButton("Unconstitutional", callback_data=f"iadm_unconst:{init['id']}")],
            [InlineKeyboardButton("Shitpost", callback_data=f"iadm_shitpost:{init['id']}")],
        ]
    )


async def iadm_main_menu(update: Update, iid: DbInitiative | int, *, top="", bottom=""):
    init = get_initiative(iid)
    if init is None:
        await update_menu(update, "Initiative not found!", reply_markup=None)
        return END
    await update_menu(
        update,
        iadm_menu_text(init, top=top, bottom=bottom),
        reply_markup=iadm_menu_keyboard(init),
    )
    return IADM_MENU


async def iadm_callback(update: Update, context: AppContext):
    callback_query = cast(CallbackQuery, update.callback_query)
    action, iid = (callback_query.data or "").split(":")
    iid = int(iid)

    # read data of initiative from db
    init: DbInitiative | None = get_initiative(iid)
    if init is None:
        await callback_query.answer("Initiative not found!")
        await update_menu(update, "Initiative not found!", reply_markup=None)
        return END
    context.user_data.iadm_edit = iid

    # don't allow editing initiatives except in submitted state
    if init["status"] != InitiativeState.submitted and action not in ("iadm_menu", "iadm_close", "iadm_close2"):
        await callback_query.answer("Initiative already decided!")
        return await iadm_main_menu(update, init)

    is_ready = init["titleFi"] and init["titleEn"] and init["descFi"] and init["descEn"]

    match action:
        case "iadm_edit_tfi":
            await callback_query.answer()
            return await iadm_ask_title(update, context, "fi")
        case "iadm_edit_ten":
            await callback_query.answer()
            return await iadm_ask_title(update, context, "en")
        case "iadm_edit_dfi":
            await callback_query.answer()
            return await iadm_ask_desc(update, context, "fi")
        case "iadm_edit_den":
            await callback_query.answer()
            return await iadm_ask_desc(update, context, "en")

        case "iadm_approve" | "iadm_approve2" if not is_ready:
            await callback_query.answer("Initiative is missing some fields!")
            return END
        case "iadm_approve":
            await callback_query.answer()
            await update_menu(
                update,
                iadm_menu_text(
                    init, bottom="<b>Area you sure you want to APPROVE this initiative?</b> (i.e. publish to voters)"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Yes, approve!", callback_data=f"iadm_approve2:{iid}")],
                        [InlineKeyboardButton("Cancel", callback_data=f"iadm_menu:{iid}")],
                    ]
                ),
            )
            return END
        case "iadm_approve2":
            await callback_query.answer()
            with db:
                # mark as approved
                db.execute(f"UPDATE initiatives SET status='{InitiativeState.approved}' WHERE id = ?", [init["id"]])
                # pre-sign by creator
                db.execute(
                    "INSERT OR IGNORE INTO initiativeChoices (userId, initiativeId, passCount) VALUES (?, ?, -1)",
                    [init["userId"], init["id"]],
                )
            # notify user
            if init["userTgId"]:
                user_lang = init["userLanguage"] or "en"
                pref_title = (init["titleFi"], init["titleEn"])[:: 1 if user_lang == "fi" else -1]
                async with log_errors(context):
                    await context.bot.send_message(
                        init["userTgId"],
                        locale[user_lang]["init_published"].format(title=escape(pref_title[0] or pref_title[1])),
                        parse_mode=ParseMode.HTML,
                    )
            # publish to users
            context.application.create_task(send_initiative_users(context, init))
            # send next
            await send_next_initiative_admin(context, auto=True)
            # update menu
            init = {**init, "status": InitiativeState.approved}
            context.application.create_task(update_initiative_admin(context, init))
            return await iadm_main_menu(update, init)
        case "iadm_unconst":
            await callback_query.answer()
            await update_menu(
                update,
                iadm_menu_text(
                    init,
                    bottom="<b>Area you sure you want to mark this initiative as UNCONSTITUTIONAL?</b> (i.e. not a shitpost, but not implementable in sitsit)",
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Yes, mark!", callback_data=f"iadm_unconst2:{iid}")],
                        [InlineKeyboardButton("Cancel", callback_data=f"iadm_menu:{iid}")],
                    ]
                ),
            )
            return END
        case "iadm_unconst2":
            await callback_query.answer("Marked as unconstitutional.")
            # mark as unconstitutional
            with db:
                db.execute(f"UPDATE initiatives SET status='{InitiativeState.unconst}' WHERE id = ?", [init["id"]])
            # notify user
            if init["userTgId"]:
                user_lang = init["userLanguage"] or "en"
                pref_title = (init["titleFi"], init["titleEn"])[:: 1 if user_lang == "fi" else -1]
                async with log_errors(context):
                    await context.bot.send_message(
                        init["userTgId"],
                        locale[user_lang]["init_unconstitutional"].format(title=escape(pref_title[0] or pref_title[1])),
                        parse_mode=ParseMode.HTML,
                    )
            # send next
            await send_next_initiative_admin(context, auto=True)
            # update menu
            init = {**init, "status": InitiativeState.unconst}
            context.application.create_task(update_initiative_admin(context, init))
            return await iadm_main_menu(update, init)
        case "iadm_shitpost":
            await callback_query.answer()
            await update_menu(
                update,
                iadm_menu_text(
                    init,
                    bottom="<b>Area you sure you want to mark this initiative as a SHITPOST?</b> (i.e. spam or very clearly unimplementable)",
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Yes, SHITPOST!", callback_data=f"iadm_shitpost2:{iid}")],
                        [InlineKeyboardButton("Cancel", callback_data=f"iadm_menu:{iid}")],
                    ]
                ),
            )
            return END
        case "iadm_shitpost2":
            await callback_query.answer("Marked as shitpost.")
            with db:
                # mark as shitpost
                db.execute(f"UPDATE initiatives SET status='{InitiativeState.shitpost}' WHERE id = ?", [init["id"]])
                # ban user, length depends on shitpost count
                user_shitposts = db.execute(
                    f"SELECT COUNT(*) AS count FROM initiatives WHERE userId = ? AND status = '{InitiativeState.shitpost}'",
                    [init["userId"]],
                ).fetchone()["count"]
                ban_idx = min(user_shitposts - 1, len(config["initiatives"]["shitpost_bans"]) - 1)
                ban_length = config["initiatives"]["shitpost_bans"][ban_idx]
                ban_ends = time() + ban_length * 60
                db.execute(f"UPDATE users SET initiativeBanUntil=? WHERE id = ?", [ban_ends, init["userId"]])
            if init["userTgId"]:
                user_lang = init["userLanguage"] or "en"
                pref_title = (init["titleFi"], init["titleEn"])[:: 1 if user_lang == "fi" else -1]
                async with log_errors(context):
                    await context.bot.send_message(
                        init["userTgId"],
                        locale[user_lang]["init_shitpost"].format(
                            title=escape(pref_title[0] or pref_title[1]),
                            ban=locale[user_lang]["init_banned"].format(mins=ban_length),
                        ),
                        parse_mode=ParseMode.HTML,
                    )
            # send next
            await send_next_initiative_admin(context, auto=True)
            # update menu
            init = {**init, "status": InitiativeState.shitpost}
            context.application.create_task(update_initiative_admin(context, init))
            return await iadm_main_menu(update, init)

        case "iadm_close" | "iadm_close2" if init["status"] != InitiativeState.approved:
            await callback_query.answer("Initiative is not open!")
            return await iadm_main_menu(update, init)
        case "iadm_close":
            await callback_query.answer()
            return END
        case "iadm_close2":
            await callback_query.answer("Signatures closed.")
            with db:
                # mark as closed
                db.execute(f"UPDATE initiatives SET status='{InitiativeState.closed}' WHERE id = ?", [init["id"]])
            # update menu
            init = {**init, "status": InitiativeState.closed}
            context.application.create_task(update_initiative_admin(context, init))
            context.application.create_task(close_initiative(context, init))
            return await iadm_main_menu(update, init)

        case _:
            await callback_query.answer()
            await iadm_main_menu(update, init)
            return END


async def iadm_cancel(update: Update, context: AppContext):
    if (pid := context.user_data.iadm_edit) is not None:
        context.user_data.iadm_edit = None
        return await iadm_main_menu(update, pid)


def get_initiative(init: int | DbInitiative) -> DbInitiative | None:
    if isinstance(init, int):
        return db.execute(
            """
            SELECT
                initiatives.*,
                COALESCE(users.name, '<unknown user>') AS userName,
                users.tgUserId AS userTgId,
                users.language AS userLanguage
            FROM initiatives
            LEFT JOIN users ON initiatives.userId = users.id
            WHERE initiatives.id = ?
            """,
            [init],
        ).fetchone()
    else:
        return init


async def send_initiative_admin(
    context: AppContext, iid: int | DbInitiative, *, auto=False, target: int | None = None, milestone: int | None = None
):
    init = get_initiative(iid)
    if not init:
        raise RuntimeError("initiative not found")
    if auto:
        # don't auto-send if an unhandled initiative is still posted
        existing = db.execute(
            f"""
            SELECT 1
            FROM sentMessages
            INNER JOIN initiatives ON initiatives.id = sentMessages.initiativeId
            WHERE sentMessages.isAdmin = 1 AND initiatives.status = '{InitiativeState.submitted}'
            """
        ).fetchone()
        if existing is not None:
            return

    # delete existing messages from groups (private messages will have menus -> don't touch)
    if milestone is not None:
        messages = db.execute(
            """
            SELECT chatId, messageId
            FROM sentMessages
            WHERE initiativeId = ? AND isAdmin = TRUE AND chatId < 0 -- XXX: not sure if this is 100% foolproof
            """,
            [init["id"]],
        ).fetchall()
        for db_msg in messages:
            async with log_errors(context):
                await context.bot.delete_message(chat_id=db_msg["chatId"], message_id=db_msg["messageId"])
            with db:
                db.execute(
                    "DELETE FROM sentMessages WHERE chatId = ? AND messageId = ?",
                    [db_msg["chatId"], db_msg["messageId"]],
                )

    # send new message
    target = cast(int, target or get_kv("initiative_log", config["admins"][0]))
    top = f"Initiative has {milestone} signatures!" if milestone is not None else ""
    msg = await context.bot.send_message(
        target,
        iadm_menu_text(init, new=auto, top=top),
        parse_mode=ParseMode.HTML,
        reply_markup=iadm_menu_keyboard(
            init,
            private=target > 0,  # XXX: not sure if this is 100% foolproof
            bot_link=context.bot.link,
        ),
    )
    with db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO sentMessages (chatId, messageId, initiativeId, language, isAdmin, status) VALUES (?, ?, ?, 'en', TRUE, 'open')",
            [msg.chat_id, msg.message_id, init["id"]],
        )


async def send_next_initiative_admin(context: AppContext, *, auto: bool, target: int | None = None):
    iid = db.execute(
        f"SELECT id FROM initiatives WHERE status = '{InitiativeState.submitted}' ORDER BY createdAt ASC LIMIT 1"
    ).fetchone()
    if iid is not None:
        async with log_errors(context):
            await send_initiative_admin(context, iid["id"], auto=auto, target=target)


async def update_initiative_admin(context: AppContext, iid: int | DbInitiative):
    init = get_initiative(iid)
    assert init
    messages = db.execute(
        f"SELECT chatId, messageId FROM sentMessages WHERE initiativeId = ? AND isAdmin = TRUE", [init["id"]]
    ).fetchall()
    text = iadm_menu_text(init)
    keyboards = {
        private: iadm_menu_keyboard(init, private=private, bot_link=context.bot.link) for private in (True, False)
    }
    for db_msg in messages:
        async with log_errors(context):
            with ignore_errors("not modified"):
                await context.bot.edit_message_text(
                    text,
                    chat_id=db_msg["chatId"],
                    message_id=db_msg["messageId"],
                    reply_markup=keyboards[db_msg["chatId"] > 0],
                    parse_mode=ParseMode.HTML,
                )


def initiative_users_text(init: DbInitiative, lang: str, new: bool, bottom=""):
    result = locale[lang]["init_notif" if new else "init_view"].format(
        title=escape(init[f"title{lang.capitalize()}"]),
        desc=escape(init[f"desc{lang.capitalize()}"]),
        user=escape(init["userName"]),
    )
    if bottom:
        result += "\n\n" + bottom
    return result


def initiative_sign_keyboard(init: DbInitiative, lang: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(locale[lang]["init_second"], callback_data=f"inits_sign:{init['id']}")],
            [InlineKeyboardButton(locale[lang]["init_pass"], callback_data=f"inits_pass:{init['id']}")],
        ]
    )


async def send_initiative_users(context: AppContext, iid: int | DbInitiative, *, user: DbUser | None = None):
    init = get_initiative(iid)
    assert init
    targets: list[DbUser]
    if user:
        targets = [user]
        langs = (cast(str, user["language"]),)
    else:
        targets = db.execute(
            """
            SELECT *
            FROM users
            LEFT JOIN initiativeChoices ON initiativeChoices.userId = users.id AND initiativeChoices.initiativeId = ?
            WHERE initiativeNotifs = 1 AND passCount != -1
            """,
            [init["id"]],
        ).fetchall()
        langs = ("fi", "en")
    messages = {lang: initiative_users_text(init, lang, not user) for lang in langs}
    keyboards = {lang: initiative_sign_keyboard(init, lang) for lang in langs}
    attempted = 0
    success = 0
    absent = 0
    for target in targets:
        if not target["tgUserId"] or not target["language"] or (not user and not target["present"]):
            absent += 1
            continue
        lang = cast(str, target["language"])
        attempted += 1
        try:
            msg = await context.bot.send_message(
                target["tgUserId"],
                messages[lang],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboards[lang],
            )
        except TelegramError as err:
            await context.application.process_error(None, err)
        else:
            with db:
                cur = db.cursor()
                cur.execute(
                    "INSERT INTO sentMessages (chatId, messageId, userId, initiativeId, language, isAdmin, status) VALUES (?, ?, ?, ?, ?, FALSE, 'open')",
                    [msg.chat_id, msg.message_id, target["id"], init["id"], lang],
                )
            success += 1
    if not user:
        await admin_log(
            f"Initiative <b>{escape(init['titleFi'])}</b> sent successfully to {success} of {attempted} present users. "
            f"{absent} absent users skipped.",
            None,
            context,
        )


async def close_initiative(context: AppContext, iid: int | DbInitiative):
    init = get_initiative(iid)
    assert init
    messages = db.execute(
        f"SELECT chatId, messageId, language FROM sentMessages WHERE initiativeId = ? AND isAdmin = FALSE", [init["id"]]
    ).fetchall()
    success = 0
    attempted = 0
    texts = {
        lang: initiative_users_text(init, lang, new=False, bottom=f"<b>{locale[lang]['init_closed']}</b>")
        for lang in ("fi", "en")
    }
    for db_msg in messages:
        lang = db_msg["language"]
        attempted += 1
        try:
            with ignore_errors(filter="not modified"):
                await context.bot.edit_message_text(
                    texts[lang],
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
        f"Initiative <b>{escape(init['titleFi'])}</b> closed successfully in {success} of {attempted} messages.",
        None,
        context,
    )

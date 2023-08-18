import re
from sqlite3 import IntegrityError
from time import time
from typing import cast

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    User,
)
from telegram.constants import ChatMemberStatus
from telegram.constants import ChatType as ChatTypeEnum
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler
from telegram.ext.filters import COMMAND, TEXT, ChatType, UpdateType

from config import config
from db import db, get_kv, set_kv
from filters import ADMIN, CONFIG_ADMIN, AdminCallbackQueryHandler, banned_admins, config_admins, db_admins
from help import admin_commands, admin_help, special_groups_help, user_commands
from initiatives import (
    IADM_DESC,
    IADM_TITLE,
    iadm_callback,
    iadm_cancel,
    iadm_save_desc,
    iadm_save_title,
    iadm_start,
    set_initiative_alert,
    set_initiative_log,
)
from polls import (
    NP_GROUP,
    NP_MENU,
    NP_OPTIONS,
    NP_QUESTION,
    newpoll_callback,
    newpoll_cancel,
    newpoll_cancel_ask,
    newpoll_save_group,
    newpoll_save_options,
    newpoll_save_question,
    newpoll_start,
    newpoll_start_election,
    poll_chooser,
)
from shared import GROUP_REGEX, admin_log, get_group_member_ids, get_group_member_users, update_menu
from typings import AppContext, PendingBroadcast, PollState
from util import escape

END = ConversationHandler.END


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
    if context.args and context.args[0].startswith("init_adm_"):
        iid = int(context.args[0].removeprefix("init_adm_"))
        return await iadm_start(update, context, iid)
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
    uids: set[int] = set()
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
    if text.count(" ") < 2:
        await message.reply_text(
            f"<b>Usage:</b> <code>/broadcast group message...</code>\n\n"
            f"Everything after the group name, including formatting, will be sent to users - be careful!\n\n"
            f"{special_groups_help}",
            parse_mode=ParseMode.HTML,
        )
        return END
    group_offset = text.index(" ") + 1
    utf32_offset = text.index(" ", group_offset)
    group = text[group_offset:utf32_offset]
    if not re.match(GROUP_REGEX, group):
        await message.reply_text(
            f"Invalid group name {escape(group)}. (To send to everyone, use <code>/broadcast everyone ...</code>)",
            parse_mode=ParseMode.HTML,
        )
        return END
    target_count = len(get_group_member_ids(group))
    if not target_count:
        await message.reply_text(
            f"No members in group {escape(group)}. (To send to everyone, use <code>/broadcast everyone ...</code>)",
            parse_mode=ParseMode.HTML,
        )
        return END
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
    context.user_data.broadcast_pending = PendingBroadcast(bid, group, rest, shifted_entities)

    prefix = f"Are you sure you want to broadcast this message to {group} ({target_count} users)?"
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


async def broadcast_message(group: str, text: str, entities: list[MessageEntity], context: AppContext):
    targets = get_group_member_users(group)
    attempted = 0
    success = 0
    skipped = 0
    for target in targets:
        if not (target["present"] and target["tgUserId"]):
            skipped += 1
            continue
        attempted += 1
        try:
            await context.bot.send_message(target["tgUserId"], text, entities=entities)
        except TelegramError as err:
            await context.application.process_error(None, err)
        else:
            success += 1
    await admin_log(
        f"Message sent successfully to {success} of {attempted} present users. {skipped} absent users skipped.",
        None,
        context,
    )


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
        await callback_query.edit_message_text("Broadcast sent.", reply_markup=None)
        context.application.create_task(broadcast_message(msg.group, msg.text, entities, context))

        clean_text = msg.text
        if len(clean_text) > 1000:
            clean_text = clean_text[:1000] + "..."
        await admin_log(f"broadcast the message:\n\n{escape(clean_text)}", update, context)
        bid = int(bid)
    else:
        await callback_query.edit_message_text("Broadcast cancelled.", reply_markup=None)

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
    AdminCallbackQueryHandler(iadm_callback, pattern=r"^iadm_\w+:\d+$"),
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
    IADM_TITLE: [
        MessageHandler(TEXT & ~COMMAND, iadm_save_title),
        CommandHandler("cancel", iadm_cancel, ~UpdateType.EDITED),
    ],
    IADM_DESC: [
        MessageHandler(TEXT & ~COMMAND, iadm_save_desc),
        CommandHandler("cancel", iadm_cancel, ~UpdateType.EDITED),
    ],
}

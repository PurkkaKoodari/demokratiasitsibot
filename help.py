from sqlite3 import Row

from telegram import User, Chat, ReplyKeyboardRemove
from telegram.constants import ParseMode

from db import DbUser
from langs import loc
from util import escape
from typings import AppContext


user_commands: dict[str, list[tuple[str, str | None, str]]] = {
    "fi": [
        ("start", None, "rekisteröidy tai näytä ohjeita"),
        ("aanesta", None, "näytä nykyinen äänestys"),
        ("aloite", None, "luo kansalaisaloite"),
        ("aloitteet", None, "selaa kansalaisaloitteita"),
        ("ailmoitukset", None, "kytke kansalaisaloiteilmoitukset päälle/pois"),
        ("kieli", None, "vaihda kieltä"),
        ("language", None, "change language"),
        ("poistu", None, "poistu sitseiltä"),
    ],
    "en": [
        ("start", None, "register or show help"),
        ("current", None, "show current poll"),
        ("initiative", None, "create new initiative"),
        ("initiatives", None, "browse initiatives"),
        ("inotifications", None, "toggle notifications for initiatives"),
        ("kieli", None, "vaihda kieltä"),
        ("language", None, "change language"),
        ("absent", None, "leave the sitsit"),
    ],
}

admin_commands: list[tuple[str, str | None, str]] = [
    ("start", None, "show help and get admin privileges for DMs"),
    ("grant", None, "get admin privileges for DMs"),
    ("broadcast", "<group> <message...>", "broadcast a message to a group"),
    ("admin_log", None, "show log of admin actions here"),
    ("initiative_log", None, "handle initiatives here"),
    ("initiative_alert", "<number...>", "alert when initiatives reach signature counts"),
    ("polls", None, "manage existing polls (only in private chat)"),
    ("newpoll", None, "create a poll (only in private chat)"),
    ("newelection", None, "create an election (only in private chat)"),
    ("unassign_code", "<code>", "unassign a seat code from its Telegram user"),
    ("group_list", None, "list all groups"),
    ("group_view", "<group>", "view members of a group"),
    ("group_add", "<to_group> <uid|group...>", "add people to a group"),
    ("group_remove", "<from_group> <uid|group...>", "remove people from a group"),
    ("mark_absent", "<uid...>", "mark people as absent from the sitsit"),
    ("start_user", None, "register as a sitsi participant (only in private chat)"),
]

special_groups_help = """Special group names:
<code>everyone</code>, <code>present</code>, <code>absent</code>"""

admin_command_help = "\n".join(
    f"/{cmd}{' ' + escape(args) if args else ''} - {desc}" for cmd, args, desc in admin_commands[1:]
)

admin_help = f"""\
Welcome! Things you can do in admin-approved chats:

{admin_command_help}

{special_groups_help}"""


def generate_help(user: DbUser | Row | None, context: AppContext):
    return loc(context)["help"].format(
        area=loc(context)["area"].format(area=escape(user["area"])) if user else "",
        initnotif=loc(context)["init_notifs_on" if (not user) or user["initiativeNotifs"] else "init_notifs_off"],
    )


async def send_help(tg_user: User | Chat, user: DbUser | Row | None, context: AppContext):
    return await tg_user.send_message(
        generate_help(user, context),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )

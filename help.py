from sqlite3 import Row

from telegram import User, Chat, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from db import DbUser
from langs import loc
from util import escape


def generate_help(user: DbUser | Row | None, context: CallbackContext):
    return loc(context)["help"].format(
        area=loc(context)["area"].format(area=escape(user["area"])) if user else "",
        initnotif=loc(context)["init_notifs_on" if (not user) or user["initiativeNotifs"] else "init_notifs_off"],
    )


async def send_help(tg_user: User | Chat, user: DbUser | Row | None, context: CallbackContext):
    return await tg_user.send_message(
        generate_help(user, context),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )

from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler
from telegram.ext.filters import COMMAND, TEXT, ChatType, UpdateType

from filters import ADMIN
from initiatives import (
    handle_initiative,
    handle_initiatives,
    handle_inotifications,
    initiative_callback,
    initiative_cancel,
    initiative_save_title,
    initiative_save_desc,
    initiatives_callback,
    INIT_DESC,
    INIT_TITLE,
)
from polls import handle_current, poll_callback
from user_setup import (
    CHANGE_LANG,
    REG_CODE,
    REG_LANG,
    handle_absent,
    handle_language,
    handle_start,
    lang_callback,
    save_code,
)

END = ConversationHandler.END


user_entry = [
    CommandHandler("start", handle_start, ~ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("aloita", handle_start, ~ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("start_user", handle_start, ADMIN & ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("help", handle_start, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("language", handle_language, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("kieli", handle_language, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("absent", handle_absent, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("poistu", handle_absent, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("current", handle_current, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("aanesta", handle_current, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("initiative", handle_initiative, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("aloite", handle_initiative, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("initiatives", handle_initiatives, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("aloitteet", handle_initiatives, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("inotifications", handle_inotifications, ChatType.PRIVATE & ~UpdateType.EDITED),
    CommandHandler("ailmoitukset", handle_inotifications, ChatType.PRIVATE & ~UpdateType.EDITED),
    CallbackQueryHandler(lang_callback, pattern=r"^lang_\w+$"),
    CallbackQueryHandler(initiative_callback, pattern=r"^init_\w+:\d+$"),
    CallbackQueryHandler(initiatives_callback, pattern=r"^inits_\w+:\d+$"),
    CallbackQueryHandler(poll_callback, pattern=r"^vote_\w+:\d+$"),
]

user_states = {
    REG_LANG: [],  # could kinda use END here
    CHANGE_LANG: [],
    REG_CODE: [
        MessageHandler(TEXT & ~COMMAND, save_code),
    ],
    INIT_TITLE: [
        MessageHandler(TEXT & ~COMMAND, initiative_save_title),
        CommandHandler("cancel", initiative_cancel, ~UpdateType.EDITED),
    ],
    INIT_DESC: [
        MessageHandler(TEXT & ~COMMAND, initiative_save_desc),
        CommandHandler("cancel", initiative_cancel, ~UpdateType.EDITED),
    ],
}

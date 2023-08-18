import pprint
from logging import getLogger

from telegram import Update
from telegram.ext import Application, BaseHandler, ChatMemberHandler, ConversationHandler, ContextTypes, CallbackContext

from admin import admin_entry, admin_states, handle_chat_member
from config import config
from shared import admin_log
from user import user_entry, user_states
from typings import AppContext, BotData, UserData


LOGGER = getLogger("dsitsibot")


class DumpHandler(BaseHandler):
    def __init__(self):
        super().__init__(self.handle, True)

    async def handle(self, update: Update, _ctx):
        pprint.pprint(update.to_dict(), width=120)

    def check_update(self, _update):
        return True


async def log_error(update, context: AppContext):
    try:
        await admin_log(f"Error: {type(context.error).__name__}: {context.error}", None, context, parse_mode=None)
    except Exception:
        pass
    LOGGER.exception("Unhandled error", exc_info=context.error)


def main():
    context_types = ContextTypes(context=AppContext, user_data=UserData, bot_data=BotData)
    app = Application.builder().context_types(context_types).token(config["token"]).build()
    app.add_handler(DumpHandler(), -999)
    app.add_handler(
        ConversationHandler(
            entry_points=[
                *admin_entry,
                *user_entry,
            ],
            states={
                **admin_states,
                **user_states,
            },
            fallbacks=[],
            name="conversation",
            allow_reentry=True,
            # persistent=True,
        )
    )
    # app.add_handler()
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(log_error)
    app.run_polling()


if __name__ == "__main__":
    main()

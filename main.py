import pprint
from typing import cast

from telegram.ext import Application, BaseHandler, ChatMemberHandler, ConversationHandler
from telegram.ext.filters import UpdateType

from admin import admin_entry, admin_states, handle_chat_member
from config import config
from filters import ADMIN
from user import user_entry, user_states


class DumpHandler(BaseHandler):
    def __init__(self):
        super().__init__(self.handle, True)

    async def handle(self, update, _ctx):
        pprint.pprint(update)

    def check_update(self, _update):
        return True


def main():
    app = Application.builder().token(config["token"]).build()
    app.add_handler(DumpHandler(), 999)
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
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.run_polling()


if __name__ == "__main__":
    main()

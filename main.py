import pprint
from typing import cast

from telegram import Update, Chat, ChatMemberUpdated
from telegram.constants import ChatMemberStatus
from telegram.ext import (Application, BaseHandler, CallbackContext,
                          ChatMemberHandler, CommandHandler)
from telegram.ext.filters import UpdateType

from config import config
from filters import ADMIN
from newpoll import newpoll_conv

async def handle_chat_member(update: Update, context: CallbackContext):
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
    if member.from_user.id not in config["admins"]:
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
    await handle_admin_start(update, context)

async def handle_admin_start(update: Update, context: CallbackContext):
    await cast(Chat, update.effective_chat).send_message("""\
welcome! things you can do as admin:

/initiatives - handle initiatives here
/polls - show log of new polls here
/newpoll - create a poll (only in private chat)""")

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
    app.add_handler(CommandHandler("start", handle_admin_start, ADMIN & ~UpdateType.EDITED))
    app.add_handler(newpoll_conv)
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.run_polling()

if __name__ == "__main__":
    main()

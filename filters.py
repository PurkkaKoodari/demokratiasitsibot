from telegram.ext.filters import UpdateFilter

from config import config
from db import get_kv


admin_groups = set(get_kv("admin_groups", []))


class AdminFilter(UpdateFilter):
    def filter(self, update):
        return (update.effective_user is not None and update.effective_user.id in config["admins"]) or (
            update.effective_chat is not None and update.effective_chat.id in admin_groups
        )


ADMIN = AdminFilter()

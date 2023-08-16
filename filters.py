from telegram import Update
from telegram.ext import CallbackQueryHandler
from telegram.ext.filters import UpdateFilter

from config import config
from db import get_kv


config_admins = set(config["admins"])
db_admins = set(get_kv("admin_groups", []))
banned_admins = set(get_kv("banned_admins", []))


class AdminFilter(UpdateFilter):
    def filter(self, update):
        all_admins = config_admins | db_admins
        return (update.effective_user is not None and update.effective_user.id in all_admins) or (
            update.effective_chat is not None and update.effective_chat.id in all_admins
        )


ADMIN = AdminFilter()


class ConfigAdminFilter(UpdateFilter):
    def filter(self, update):
        return update.effective_user is not None and update.effective_user.id in config_admins


CONFIG_ADMIN = ConfigAdminFilter()


class AdminCallbackQueryHandler(CallbackQueryHandler):
    def check_update(self, update: Update) -> bool | object | None:
        # XXX: ADMIN.check_update doesn't accept non-message updates by default, so using ADMIN.filter
        if not isinstance(update, Update) or not ADMIN.filter(update):
            return False
        return super().check_update(update)

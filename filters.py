from telegram.ext.filters import UpdateFilter

from config import config

class AdminFilter(UpdateFilter):
    def filter(self, update):
        return update.effective_user is not None and update.effective_user.id in config["admins"]
    
ADMIN = AdminFilter()

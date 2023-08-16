from telegram import User


def escape(text: str):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def user_link(user: User):
    return f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'

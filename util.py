from itertools import groupby
from typing import Iterable, Generic, TypeVar, Callable

from telegram import User


def escape(text: str):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def user_link(user: User):
    return f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'


T = TypeVar("T")
K = TypeVar("K")


def grouplist(it: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    return {k: list(v) for k, v in groupby(it, key)}

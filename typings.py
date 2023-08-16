from dataclasses import dataclass, field
from typing import TypedDict, NotRequired

from telegram.ext import CallbackContext, ExtBot


class PendingPoll(TypedDict, total=False):
    textFi: str
    textEn: str
    perArea: bool
    voterGroup: str | None
    sourceGroup: str
    electedGroup: str
    replaceElectedGroup: bool
    electedGroupEligible: bool
    electedGroupVoting: bool
    opts_fi: list[str]
    opts_en: list[str]


class PendingInitiative(TypedDict, total=False):
    id: int
    title: str
    desc: str


@dataclass
class BotData:
    init_handlers: dict[int, int] = field(default_factory=dict)


@dataclass
class UserData:
    lang: str | None = None
    """Language selected by regular user"""
    init_pending: PendingInitiative = field(default_factory=PendingInitiative)
    """Initiative currently being edited by user"""
    init_edit: bool = False
    """Whether the current prompt is an edit (--> return to initiative confirmation screen)"""
    poll_edit: int | None = None
    """Poll ID being edited"""
    poll_pending: PendingPoll = field(default_factory=PendingPoll)
    """Pending updates to poll"""
    poll_is_election: bool = False
    """Whether poll_edit/poll_pending is an election"""
    poll_lang: str | None = None
    """Current language being edited in poll"""
    poll_group: str | None = None
    """Current group key being edited in poll"""


class AppContext(CallbackContext[ExtBot, UserData, None, BotData]):
    @property
    def user_data(self) -> UserData:
        data = super().user_data
        if data is None:
            raise RuntimeError("no user data")
        return data

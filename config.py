import tomllib
from typing import TypedDict, cast


class ElectionConfig(TypedDict):
    max_candidates: int


class InitiativesConfig(TypedDict):
    title_max_len: int
    desc_max_len: int
    shitpost_bans: list[int]
    default_alerts: list[int]
    handle_cooldown: int


class Config(TypedDict):
    token: str
    database: str
    admins: list[int]
    election: ElectionConfig
    initiatives: InitiativesConfig


config = cast(Config, tomllib.load(open("config.toml", "rb")))

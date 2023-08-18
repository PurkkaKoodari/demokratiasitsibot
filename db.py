import json
import sqlite3
from typing import Any, cast, TypedDict, Literal

from telegram import Update, User

from config import config
from typings import PollState, InitiativeState

db = sqlite3.connect(config["database"])
db.row_factory = sqlite3.Row

db.set_trace_callback(print)

db.executescript(
    """
PRAGMA foreign_keys = TRUE;

CREATE TABLE IF NOT EXISTS kv (
    key CHAR(32) PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    passcode CHAR(8) UNIQUE NOT NULL,
    tgUserId INTEGER DEFAULT NULL,
    tgUsername VARCHAR(255) DEFAULT NULL,
    tgDisplayName VARCHAR(255) DEFAULT NULL,
    name VARCHAR(255) NOT NULL,
    area VARCHAR(32) NOT NULL DEFAULT 'default',
    candidateNumber VARCHAR(16) DEFAULT NULL,
    present BOOLEAN DEFAULT FALSE,
    language CHAR(2) DEFAULT NULL,
    initiativeNotifs BOOLEAN DEFAULT TRUE,
    initiativeBanUntil DATETIME DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS groupMembers (
    userId INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE,
    `group` CHAR(32) NOT NULL,
    PRIMARY KEY (userId, `group`)
);
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY,
    textFi TEXT NOT NULL,
    textEn TEXT NOT NULL,
    status CHAR(8) NOT NULL DEFAULT 'created',
    type CHAR(8) NOT NULL DEFAULT 'question',
    voterGroup CHAR(32) NOT NULL DEFAULT 'everyone',
    sourceGroup CHAR(32) NOT NULL DEFAULT 'everyone',
    perArea BOOLEAN DEFAULT TRUE,
    updatedAt DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY,
    pollId INTEGER NOT NULL REFERENCES polls (id) ON DELETE CASCADE ON UPDATE CASCADE,
    candidateId INTEGER DEFAULT NULL REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE,
    area VARCHAR(32) DEFAULT NULL,
    textFi TEXT NOT NULL,
    textEn TEXT NOT NULL,
    orderNo INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    pollId INTEGER NOT NULL REFERENCES polls (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    voterId INTEGER NOT NULL REFERENCES users (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    optionId INTEGER NOT NULL REFERENCES options (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    area VARCHAR(32) NOT NULL DEFAULT 'default',
    PRIMARY KEY (pollId, voterId)
);
CREATE TABLE IF NOT EXISTS initiatives (
    id INTEGER PRIMARY KEY,
    userId INTEGER NOT NULL REFERENCES users (id),
    createdAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    titleFi TEXT DEFAULT NULL,
    titleEn TEXT DEFAULT NULL, 
    descFi TEXT DEFAULT NULL,
    descEn TEXT DEFAULT NULL,
    status CHAR(16) NOT NULL DEFAULT 'submitted',
    signCount INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS initiativeChoices (
    userId INTEGER NOT NULL REFERENCES users (id),
    initiativeId INTEGER NOT NULL REFERENCES initiatives (id),
    passCount INTEGER NOT NULL,
    PRIMARY KEY (userId, initiativeId)
);
CREATE TABLE IF NOT EXISTS sentMessages (
    chatId INTEGER NOT NULL,
    messageId INTEGER NOT NULL,
    userId INTEGER DEFAULT NULL REFERENCES users (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    pollId INTEGER DEFAULT NULL REFERENCES polls (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    initiativeId INTEGER DEFAULT NULL REFERENCES initiatives (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    language CHAR(2) NOT NULL,
    isAdmin BOOLEAN NOT NULL,
    status CHAR(8) NOT NULL,
    PRIMARY KEY (chatId, messageId)
);
"""
)
db.commit()


class DbUser(TypedDict):
    id: int
    passcode: str
    tgUserId: int | None
    tgDisplayName: str | None
    tgUsername: str | None
    name: str
    area: str
    candidateNumber: int | None
    present: bool
    language: str | None
    initiativeNotifs: bool
    initiativeBanUntil: float | None


class DbPoll(TypedDict):
    id: int
    textFi: str
    textEn: str
    status: PollState
    type: Literal["question"] | Literal["election"]
    voterGroup: str
    sourceGroup: str
    perArea: bool
    updatedAt: str


class DbInitiative(TypedDict):
    id: int
    userId: int
    createdAt: str
    userName: str
    userLanguage: str | None
    userTgId: int | None
    titleFi: str
    titleEn: str
    descFi: str
    descEn: str
    status: InitiativeState
    signCount: int


def get_kv(key: str, default: Any):
    row = db.execute("SELECT value FROM kv WHERE key = ?", [key]).fetchone()
    return json.loads(row[0]) if row else default


def set_kv(key: str, value: Any):
    with db:
        db.execute("REPLACE INTO kv (key, value) VALUES (?, ?)", [key, json.dumps(value)])


def get_user(update: Update) -> DbUser | None:
    tg_id = cast(User, update.effective_user).id
    return db.execute("SELECT * FROM users WHERE tgUserId = ?", [tg_id]).fetchone()

import sqlite3
from typing import Any

from config import config

db = sqlite3.connect(config["database"])
db.row_factory = sqlite3.Row

db.executescript("""
CREATE TABLE IF NOT EXISTS kv (
    key CHAR(32) PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    passcode CHAR(8) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    area VARCHAR(32) DEFAULT NULL,
    candidateNumber INTEGER DEFAULT NULL,
    present BOOLEAN DEFAULT FALSE,
    language CHAR(2) DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY,
    textFi TEXT NOT NULL,
    textEn TEXT NOT NULL,
    status CHAR(8) NOT NULL DEFAULT 'created'
);
CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY,
    pollId INTEGER NOT NULL REFERENCES polls (id) ON DELETE CASCADE ON UPDATE CASCADE,
    textFi TEXT NOT NULL,
    textEn TEXT NOT NULL,
    orderNo INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pollVotes (
    pollId INTEGER NOT NULL REFERENCES polls (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    voterId INTEGER NOT NULL REFERENCES users (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    optionId INTEGER NOT NULL REFERENCES options (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    PRIMARY KEY (pollId, voterId)
);
CREATE TABLE IF NOT EXISTS electionVotes (
    electionId INTEGER NOT NULL REFERENCES polls (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    voterId INTEGER NOT NULL REFERENCES users (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    candidateId INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE,
    PRIMARY KEY (electionId, voterId)
);
CREATE TABLE IF NOT EXISTS initiatives (
    id INTEGER PRIMARY KEY,
    userId INTEGER NOT NULL REFERENCES users (id),
    createdAt DATETIME DEFAULT CURRENT_TIMESTAMP,
    textFi TEXT DEFAULT NULL,
    textEn TEXT DEFAULT NULL,
    status CHAR(16) NOT NULL DEFAULT 'submitted'
);
CREATE TABLE IF NOT EXISTS seconds (
    userId INTEGER NOT NULL REFERENCES users (id),
    initiativeId INTEGER NOT NULL REFERENCES initiatives (id),
    PRIMARY KEY (userId, initiativeId)
);
""")
                 
def get_kv(key: str, default: Any):
    row = db.execute("SELECT value FROM kv WHERE key = ?", [key]).fetchone()
    return row[0] if row else default

def set_kv(key: str, value: Any):
    with db:
        db.execute("REPLACE INTO kv (key, value) VALUES (?, ?)", [key, str(value)])

"""מסד הנתונים: כל מטבע, כל עסקה, כל נקודת בדיקה וכל עסקת נייר."""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    mint TEXT PRIMARY KEY,
    name TEXT, symbol TEXT, uri TEXT,
    creator TEXT, created_ts REAL,
    migrated_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_tokens_creator ON tokens(creator);
CREATE INDEX IF NOT EXISTS ix_tokens_created ON tokens(created_ts);

CREATE TABLE IF NOT EXISTS trades (
    ts REAL, mint TEXT, trader TEXT, side TEXT,
    sol REAL, tokens REAL, vsol REAL, mcap_sol REAL, pool TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_mint_ts ON trades(mint, ts);

-- כל מטבע שעבר נקודת בדיקה, גם אם נפסל. מכאן נמדד הדיוק של המסננים
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT, checkpoint REAL, ts REAL,
    entry_mcap REAL, score INTEGER, grade TEXT,
    rejected INTEGER, reasons TEXT, features TEXT,
    outcome TEXT,          -- target / stop / timeout / NULL
    max_mult REAL, min_mult REAL, resolved_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_cp_mint ON checkpoints(mint);
CREATE INDEX IF NOT EXISTS ix_cp_ts ON checkpoints(ts);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id INTEGER, mint TEXT, symbol TEXT,
    alert_ts REAL, entry_ts REAL, entry_mcap REAL,
    prob REAL, grade TEXT, stake REAL,
    half_sold INTEGER DEFAULT 0, half_value REAL DEFAULT 0,
    peak_mcap REAL, exit_ts REAL, exit_value REAL, exit_reason TEXT,
    status TEXT            -- pending / open / closed
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._trade_buf: list[tuple] = []

    # --- כתיבה מרוכזת של עסקאות ---
    def add_trade(self, row: tuple) -> None:
        self._trade_buf.append(row)
        if len(self._trade_buf) >= 500:
            self.flush()

    def flush(self) -> None:
        if not self._trade_buf:
            return
        self.conn.execute("BEGIN")
        self.conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)", self._trade_buf)
        self.conn.execute("COMMIT")
        self._trade_buf.clear()

    def add_token(self, mint, name, symbol, uri, creator, ts) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO tokens(mint,name,symbol,uri,creator,created_ts) VALUES (?,?,?,?,?,?)",
            (mint, name, symbol, uri, creator, ts),
        )

    def mark_migrated(self, mint, ts) -> None:
        self.conn.execute("UPDATE tokens SET migrated_ts=? WHERE mint=? AND migrated_ts IS NULL", (ts, mint))

    # --- היסטוריית יוצר ---
    def creator_history(self, creator: str, now: float) -> dict:
        r = self.conn.execute(
            """SELECT COUNT(*) n,
                      SUM(CASE WHEN migrated_ts IS NOT NULL THEN 1 ELSE 0 END) grads,
                      SUM(CASE WHEN created_ts > ? THEN 1 ELSE 0 END) n24
               FROM tokens WHERE creator=? AND created_ts < ?""",
            (now - 86400, creator, now - 1),
        ).fetchone()
        return {"launches": r["n"] or 0, "grads": r["grads"] or 0, "launches_24h": r["n24"] or 0}

    # --- נקודות בדיקה ---
    def add_checkpoint(self, mint, cp, ts, mcap, score, grade, rejected, reasons, features) -> int:
        cur = self.conn.execute(
            """INSERT INTO checkpoints(mint,checkpoint,ts,entry_mcap,score,grade,rejected,reasons,features)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (mint, cp, ts, mcap, score, grade, int(rejected), json.dumps(reasons, ensure_ascii=False),
             json.dumps(features)),
        )
        return cur.lastrowid

    def resolve_checkpoint(self, cid, outcome, max_mult, min_mult, ts) -> None:
        self.conn.execute(
            "UPDATE checkpoints SET outcome=?, max_mult=?, min_mult=?, resolved_ts=? WHERE id=?",
            (outcome, max_mult, min_mult, ts, cid),
        )

    def hit_rate(self, cp: float, grade: str, now: float, lookback_days: int) -> tuple[int, int]:
        r = self.conn.execute(
            """SELECT COUNT(*) n, SUM(CASE WHEN outcome='target' THEN 1 ELSE 0 END) hits
               FROM checkpoints WHERE checkpoint=? AND grade=? AND rejected=0
               AND outcome IS NOT NULL AND ts > ?""",
            (cp, grade, now - lookback_days * 86400),
        ).fetchone()
        return (r["n"] or 0, r["hits"] or 0)

    # --- מפתח/ערך ---
    def get(self, k, default=None):
        r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k, v) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES (?,?)", (k, json.dumps(v)))

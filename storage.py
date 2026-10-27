import os
from datetime import datetime, timezone
import aiosqlite

class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None

    async def init(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, start_count INTEGER NOT NULL DEFAULT 1)""")
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS user_states(
            user_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT '')""")
        await self.conn.execute("""CREATE TABLE IF NOT EXISTS sherlock_reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, filename TEXT NOT NULL,
            report_text TEXT NOT NULL, imported_by INTEGER NOT NULL, created_at TEXT NOT NULL,
            line_count INTEGER NOT NULL)""")
        await self.conn.commit()

    def now(self): return datetime.now(timezone.utc).isoformat()

    async def upsert_user(self, uid, username, full_name):
        now = self.now()
        await self.conn.execute("""INSERT INTO users VALUES(?,?,?,?,?,1)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name,
            last_seen=excluded.last_seen, start_count=users.start_count+1""",
            (uid, username, full_name, now, now))
        await self.conn.commit()

    async def get_user(self, uid):
        cur = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        return await cur.fetchone()

    async def user_count(self):
        cur = await self.conn.execute("SELECT COUNT(*) FROM users")
        return (await cur.fetchone())[0]

    async def set_state(self, uid, state):
        await self.conn.execute("INSERT INTO user_states VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state", (uid, state))
        await self.conn.commit()

    async def get_state(self, uid):
        cur = await self.conn.execute("SELECT state FROM user_states WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        return row[0] if row else ""

    async def clear_state(self, uid): await self.set_state(uid, "")

    async def save_report(self, username, filename, raw, imported_by):
        cur = await self.conn.execute(
            "INSERT INTO sherlock_reports(username,filename,report_text,imported_by,created_at,line_count) VALUES(?,?,?,?,?,?)",
            (username, filename, raw, imported_by, self.now(), len(raw.splitlines())))
        await self.conn.commit()
        return cur.lastrowid

    async def latest_reports(self, limit=10):
        cur = await self.conn.execute("SELECT id,username,filename,created_at,line_count FROM sherlock_reports ORDER BY id DESC LIMIT ?", (limit,))
        return await cur.fetchall()

    async def get_report(self, rid):
        cur = await self.conn.execute("SELECT id,username,filename,report_text,created_at FROM sherlock_reports WHERE id=?", (rid,))
        return await cur.fetchone()

    async def report_count(self):
        cur = await self.conn.execute("SELECT COUNT(*) FROM sherlock_reports")
        return (await cur.fetchone())[0]

    async def close(self):
        if self.conn: await self.conn.close()

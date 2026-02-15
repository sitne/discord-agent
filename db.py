"""SQLite database for conversation history and audit logs."""
import json
import time
from typing import Optional

import aiosqlite

DB_PATH = "data/bot.db"


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def create(cls) -> "Database":
        import os
        os.makedirs("data", exist_ok=True)
        conn = await aiosqlite.connect(DB_PATH)
        db = cls(conn)
        await db._init_tables()
        return db

    async def _init_tables(self):
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT,
                tool_call_id TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_channel
                ON conversations(channel_id, created_at);

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_input TEXT NOT NULL,
                tool_result TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        await self.conn.commit()

    async def add_message(
        self,
        channel_id: str,
        role: str,
        content: str,
        tool_calls: Optional[list] = None,
        tool_call_id: Optional[str] = None,
    ):
        await self.conn.execute(
            "INSERT INTO conversations (channel_id, role, content, tool_calls, tool_call_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                channel_id,
                role,
                content,
                json.dumps(tool_calls) if tool_calls else None,
                tool_call_id,
                time.time(),
            ),
        )
        await self.conn.commit()

    async def get_history(self, channel_id: str, limit: int = 30) -> list[dict]:
        """Get recent conversation history for a channel."""
        cursor = await self.conn.execute(
            "SELECT role, content, tool_calls, tool_call_id FROM conversations "
            "WHERE channel_id = ? ORDER BY created_at DESC LIMIT ?",
            (channel_id, limit),
        )
        rows = await cursor.fetchall()
        messages = []
        for role, content, tool_calls_json, tool_call_id in reversed(rows):
            msg = {"role": role}
            if role == "assistant" and tool_calls_json:
                tc = json.loads(tool_calls_json)
                msg["content"] = content or ""
                msg["tool_calls"] = tc
            elif role == "tool":
                msg["content"] = content
                msg["tool_call_id"] = tool_call_id
            else:
                msg["content"] = content
            messages.append(msg)
        return messages

    async def clear_history(self, channel_id: str):
        await self.conn.execute(
            "DELETE FROM conversations WHERE channel_id = ?", (channel_id,)
        )
        await self.conn.commit()

    async def log_tool_use(
        self, guild_id: str, user_id: str, tool_name: str, tool_input: dict, tool_result: str
    ):
        await self.conn.execute(
            "INSERT INTO audit_log (guild_id, user_id, tool_name, tool_input, tool_result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, tool_name, json.dumps(tool_input, ensure_ascii=False), tool_result, time.time()),
        )
        await self.conn.commit()

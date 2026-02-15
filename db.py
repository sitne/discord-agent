"""SQLite database for conversation history, message archive, memory, and tasks."""
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
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        db = cls(conn)
        await db._init_tables()
        return db

    async def _init_tables(self):
        await self.conn.executescript("""
            -- Conversation history (existing)
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

            -- Audit log (existing)
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_input TEXT NOT NULL,
                tool_result TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            -- ======================
            -- Message Archive + FTS
            -- ======================
            CREATE TABLE IF NOT EXISTS message_archive (
                message_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_archive_guild_time
                ON message_archive(guild_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_archive_channel_time
                ON message_archive(channel_id, created_at);

            -- FTS5 virtual table for full-text search
            CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
                content,
                author_name,
                channel_name,
                content='message_archive',
                content_rowid='rowid',
                tokenize='unicode61'
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON message_archive BEGIN
                INSERT INTO message_fts(rowid, content, author_name, channel_name)
                VALUES (new.rowid, new.content, new.author_name, new.channel_name);
            END;
            CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON message_archive BEGIN
                INSERT INTO message_fts(message_fts, rowid, content, author_name, channel_name)
                VALUES ('delete', old.rowid, old.content, old.author_name, old.channel_name);
            END;

            -- Track collection progress per channel
            CREATE TABLE IF NOT EXISTS collection_state (
                channel_id TEXT PRIMARY KEY,
                last_message_id TEXT,
                updated_at REAL NOT NULL
            );

            -- ===================
            -- Structured Memory
            -- ===================
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                created_by TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_guild
                ON memories(guild_id, category);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_guild_key
                ON memories(guild_id, category, key);

            -- FTS for memory search
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                key,
                content,
                category,
                content='memories',
                content_rowid='id',
                tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memory_fts(rowid, key, content, category)
                VALUES (new.id, new.key, new.content, new.category);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, key, content, category)
                VALUES ('delete', old.id, old.key, old.content, old.category);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, key, content, category)
                VALUES ('delete', old.id, old.key, old.content, old.category);
                INSERT INTO memory_fts(rowid, key, content, category)
                VALUES (new.id, new.key, new.content, new.category);
            END;

            -- ====================
            -- Scheduled Tasks
            -- ====================
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                created_by TEXT NOT NULL,
                task_name TEXT NOT NULL,
                task_prompt TEXT NOT NULL,
                cron_expression TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at REAL,
                next_run_at REAL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_next_run
                ON scheduled_tasks(enabled, next_run_at);
        """)
        await self.conn.commit()

    # ---------------------------------------------------------------
    # Conversation history (existing, unchanged)
    # ---------------------------------------------------------------
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

    # ---------------------------------------------------------------
    # Message Archive
    # ---------------------------------------------------------------
    async def archive_message(
        self,
        message_id: str,
        guild_id: str,
        channel_id: str,
        channel_name: str,
        author_id: str,
        author_name: str,
        content: str,
        created_at: float,
    ):
        """Insert a single message into the archive (ignore duplicates)."""
        await self.conn.execute(
            "INSERT OR IGNORE INTO message_archive "
            "(message_id, guild_id, channel_id, channel_name, author_id, author_name, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, channel_name, author_id, author_name, content, created_at),
        )

    async def archive_messages_bulk(self, rows: list[tuple]):
        """Bulk insert messages. Each row: (message_id, guild_id, channel_id, channel_name, author_id, author_name, content, created_at)."""
        await self.conn.executemany(
            "INSERT OR IGNORE INTO message_archive "
            "(message_id, guild_id, channel_id, channel_name, author_id, author_name, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self.conn.commit()

    async def search_messages(
        self,
        guild_id: str,
        query: str,
        channel_name: str = None,
        author_name: str = None,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search across archived messages."""
        # Build FTS query
        fts_terms = []
        if query:
            # Escape special FTS5 chars
            safe_q = query.replace('"', '""')
            fts_terms.append(f'content : "{safe_q}"')
        if channel_name:
            safe_cn = channel_name.replace('"', '""')
            fts_terms.append(f'channel_name : "{safe_cn}"')
        if author_name:
            safe_an = author_name.replace('"', '""')
            fts_terms.append(f'author_name : "{safe_an}"')

        if not fts_terms:
            return []

        fts_query = " AND ".join(fts_terms)

        cursor = await self.conn.execute(
            """
            SELECT a.message_id, a.channel_name, a.author_name, a.content,
                   a.created_at, rank
            FROM message_fts f
            JOIN message_archive a ON a.rowid = f.rowid
            WHERE f.message_fts MATCH ?
              AND a.guild_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, guild_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "message_id": r[0],
                "channel": r[1],
                "author": r[2],
                "content": r[3],
                "timestamp": r[4],
            }
            for r in rows
        ]

    async def get_collection_state(self, channel_id: str) -> Optional[str]:
        cursor = await self.conn.execute(
            "SELECT last_message_id FROM collection_state WHERE channel_id = ?",
            (channel_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_collection_state(self, channel_id: str, last_message_id: str):
        await self.conn.execute(
            "INSERT OR REPLACE INTO collection_state (channel_id, last_message_id, updated_at) "
            "VALUES (?, ?, ?)",
            (channel_id, last_message_id, time.time()),
        )
        await self.conn.commit()

    async def get_archive_stats(self, guild_id: str) -> dict:
        cursor = await self.conn.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM message_archive WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return {
            "total_messages": row[0],
            "oldest": row[1],
            "newest": row[2],
        }

    # ---------------------------------------------------------------
    # Structured Memory
    # ---------------------------------------------------------------
    async def remember(
        self, guild_id: str, category: str, key: str, content: str, created_by: str = None
    ):
        """Store or update a memory."""
        now = time.time()
        await self.conn.execute(
            """
            INSERT INTO memories (guild_id, category, key, content, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, category, key)
            DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
            """,
            (guild_id, category, key, content, created_by, now, now),
        )
        await self.conn.commit()

    async def recall(self, guild_id: str, query: str = None, category: str = None, limit: int = 20) -> list[dict]:
        """Search memories by FTS or list by category."""
        if query:
            safe_q = query.replace('"', '""')
            fts_parts = [f'"{safe_q}"']
            if category:
                safe_c = category.replace('"', '""')
                fts_parts.append(f'category : "{safe_c}"')
            fts_query = " AND ".join(fts_parts)

            cursor = await self.conn.execute(
                """
                SELECT m.id, m.category, m.key, m.content, m.created_by, m.updated_at
                FROM memory_fts f
                JOIN memories m ON m.id = f.rowid
                WHERE f.memory_fts MATCH ? AND m.guild_id = ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, guild_id, limit),
            )
        elif category:
            cursor = await self.conn.execute(
                "SELECT id, category, key, content, created_by, updated_at "
                "FROM memories WHERE guild_id = ? AND category = ? ORDER BY updated_at DESC LIMIT ?",
                (guild_id, category, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT id, category, key, content, created_by, updated_at "
                "FROM memories WHERE guild_id = ? ORDER BY updated_at DESC LIMIT ?",
                (guild_id, limit),
            )

        rows = await cursor.fetchall()
        return [
            {"id": r[0], "category": r[1], "key": r[2], "content": r[3], "created_by": r[4], "updated_at": r[5]}
            for r in rows
        ]

    async def forget(self, guild_id: str, memory_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM memories WHERE id = ? AND guild_id = ?",
            (memory_id, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_memory_categories(self, guild_id: str) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT DISTINCT category FROM memories WHERE guild_id = ? ORDER BY category",
            (guild_id,),
        )
        return [r[0] for r in await cursor.fetchall()]

    # ---------------------------------------------------------------
    # Scheduled Tasks
    # ---------------------------------------------------------------
    async def create_task(
        self,
        guild_id: str,
        channel_id: str,
        created_by: str,
        task_name: str,
        task_prompt: str,
        cron_expression: str,
        next_run_at: float,
    ) -> int:
        cursor = await self.conn.execute(
            "INSERT INTO scheduled_tasks "
            "(guild_id, channel_id, created_by, task_name, task_prompt, cron_expression, next_run_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, created_by, task_name, task_prompt, cron_expression, next_run_at, time.time()),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_due_tasks(self, now: float) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT id, guild_id, channel_id, created_by, task_name, task_prompt, cron_expression "
            "FROM scheduled_tasks WHERE enabled = 1 AND next_run_at <= ? "
            "ORDER BY next_run_at",
            (now,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "guild_id": r[1], "channel_id": r[2], "created_by": r[3],
                "task_name": r[4], "task_prompt": r[5], "cron_expression": r[6],
            }
            for r in rows
        ]

    async def update_task_run(self, task_id: int, next_run_at: float):
        await self.conn.execute(
            "UPDATE scheduled_tasks SET last_run_at = ?, next_run_at = ? WHERE id = ?",
            (time.time(), next_run_at, task_id),
        )
        await self.conn.commit()

    async def list_tasks(self, guild_id: str) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT id, task_name, task_prompt, cron_expression, enabled, last_run_at, next_run_at, channel_id, created_by "
            "FROM scheduled_tasks WHERE guild_id = ? ORDER BY next_run_at",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "name": r[1], "prompt": r[2], "cron": r[3],
                "enabled": bool(r[4]), "last_run": r[5], "next_run": r[6],
                "channel_id": r[7], "created_by": r[8],
            }
            for r in rows
        ]

    async def delete_task(self, guild_id: str, task_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM scheduled_tasks WHERE id = ? AND guild_id = ?",
            (task_id, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def toggle_task(self, guild_id: str, task_id: int, enabled: bool) -> bool:
        cursor = await self.conn.execute(
            "UPDATE scheduled_tasks SET enabled = ? WHERE id = ? AND guild_id = ?",
            (1 if enabled else 0, task_id, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

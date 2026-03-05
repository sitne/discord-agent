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

            -- ====================
            -- Task Executions
            -- ====================
            CREATE TABLE IF NOT EXISTS task_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at REAL NOT NULL,
                completed_at REAL,
                tokens_used INTEGER DEFAULT 0,
                tool_calls_count INTEGER DEFAULT 0,
                error_message TEXT,
                result_summary TEXT,
                retry_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_executions_task
                ON task_executions(task_id, started_at DESC);
        """)
        await self.conn.commit()
        await self._migrate_memories()
        await self._migrate_scheduled_tasks()

    async def _migrate_memories(self):
        """Add new columns to memories table if they don't exist (safe for existing DBs)."""
        # Check which columns already exist
        cursor = await self.conn.execute("PRAGMA table_info(memories)")
        existing = {row[1] for row in await cursor.fetchall()}
        migrations = [
            ("importance", "INTEGER NOT NULL DEFAULT 5"),
            ("access_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_accessed_at", "REAL"),
            ("user_id", "TEXT"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                await self.conn.execute(
                    f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}"
                )
        await self.conn.commit()

    async def _migrate_scheduled_tasks(self):
        """Add retry_count and max_retries columns to scheduled_tasks if they don't exist."""
        cursor = await self.conn.execute("PRAGMA table_info(scheduled_tasks)")
        existing = {row[1] for row in await cursor.fetchall()}
        migrations = [
            ("retry_count", "INTEGER DEFAULT 0"),
            ("max_retries", "INTEGER DEFAULT 3"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                try:
                    await self.conn.execute(
                        f"ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def}"
                    )
                except Exception:
                    pass
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
        self, guild_id: str, category: str, key: str, content: str,
        created_by: str = None, importance: int = 5,
    ):
        """Store or update a memory with importance scoring (1-10)."""
        importance = max(1, min(10, importance))
        now = time.time()
        await self.conn.execute(
            """
            INSERT INTO memories (guild_id, category, key, content, created_by, created_at, updated_at, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, category, key)
            DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at,
                         importance = excluded.importance
            """,
            (guild_id, category, key, content, created_by, now, now, importance),
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

    async def recall_relevant(self, guild_id: str, query: str, limit: int = 5) -> list[dict]:
        """FTS5 search with word-based OR tokens and hybrid BM25 scoring.
        Updates access_count and last_accessed_at on returned memories."""
        if not query or not query.strip():
            return []

        # Split query into individual word tokens joined by OR for broader matching
        words = [w.strip() for w in query.split() if w.strip()]
        if not words:
            return []
        # Escape each word for FTS5 safety and join with OR
        safe_words = []
        for w in words:
            safe = w.replace('"', '""')
            safe_words.append(f'"{safe}"')
        fts_query = " OR ".join(safe_words)

        cursor = await self.conn.execute(
            """
            SELECT m.id, m.category, m.key, m.content, m.created_by, m.updated_at,
                   m.importance, m.access_count, rank
            FROM memory_fts f
            JOIN memories m ON m.id = f.rowid
            WHERE f.memory_fts MATCH ? AND m.guild_id = ?
            ORDER BY (rank * -1.0) * (m.importance / 5.0) DESC
            LIMIT ?
            """,
            (fts_query, guild_id, limit),
        )
        rows = await cursor.fetchall()
        if not rows:
            return []

        # Update access stats for returned memories
        now = time.time()
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        await self.conn.execute(
            f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? "
            f"WHERE id IN ({placeholders})",
            [now] + ids,
        )
        await self.conn.commit()

        return [
            {
                "id": r[0], "category": r[1], "key": r[2], "content": r[3],
                "created_by": r[4], "updated_at": r[5], "importance": r[6],
                "access_count": r[7],
            }
            for r in rows
        ]

    async def forget(self, guild_id: str, memory_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM memories WHERE id = ? AND guild_id = ?",
            (memory_id, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def forget_by_key(self, guild_id: str, category: str, key: str) -> bool:
        """Delete a memory by category + key."""
        cursor = await self.conn.execute(
            "DELETE FROM memories WHERE guild_id = ? AND category = ? AND key = ?",
            (guild_id, category, key),
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

    # ---------------------------------------------------------------
    # Task Executions
    # ---------------------------------------------------------------
    async def start_task_execution(self, task_id: int) -> int:
        """Insert a new execution row with status='running', return its id."""
        cursor = await self.conn.execute(
            "INSERT INTO task_executions (task_id, status, started_at) VALUES (?, 'running', ?)",
            (task_id, time.time()),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def complete_task_execution(
        self,
        execution_id: int,
        status: str,
        result_summary: Optional[str] = None,
        error_message: Optional[str] = None,
        tokens: int = 0,
        tool_calls: int = 0,
    ):
        """Update an execution row with completion info."""
        await self.conn.execute(
            "UPDATE task_executions SET status = ?, completed_at = ?, "
            "result_summary = ?, error_message = ?, tokens_used = ?, tool_calls_count = ? "
            "WHERE id = ?",
            (status, time.time(), result_summary, error_message, tokens, tool_calls, execution_id),
        )
        await self.conn.commit()

    async def get_task_execution_history(self, task_id: int, limit: int = 10) -> list[dict]:
        """Get recent executions for a task."""
        cursor = await self.conn.execute(
            "SELECT id, task_id, status, started_at, completed_at, tokens_used, "
            "tool_calls_count, error_message, result_summary, retry_count "
            "FROM task_executions WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "task_id": r[1], "status": r[2], "started_at": r[3],
                "completed_at": r[4], "tokens_used": r[5], "tool_calls_count": r[6],
                "error_message": r[7], "result_summary": r[8], "retry_count": r[9],
            }
            for r in rows
        ]

    async def increment_task_retry(self, task_id: int) -> int:
        """Increment retry_count for a task and return the new count."""
        await self.conn.execute(
            "UPDATE scheduled_tasks SET retry_count = COALESCE(retry_count, 0) + 1 WHERE id = ?",
            (task_id,),
        )
        await self.conn.commit()
        cursor = await self.conn.execute(
            "SELECT retry_count FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def reset_task_retry(self, task_id: int):
        """Reset retry_count to 0."""
        await self.conn.execute(
            "UPDATE scheduled_tasks SET retry_count = 0 WHERE id = ?",
            (task_id,),
        )
        await self.conn.commit()

    async def claim_task(self, task_id: int) -> bool:
        """Atomically claim a task to prevent double-run.

        Sets next_run_at to a far-future sentinel value only if the task
        is currently due (next_run_at <= now).  Returns True if the claim
        succeeded (this caller should execute it).
        """
        now = time.time()
        cursor = await self.conn.execute(
            "UPDATE scheduled_tasks SET next_run_at = 9999999999 "
            "WHERE id = ? AND enabled = 1 AND next_run_at <= ?",
            (task_id, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

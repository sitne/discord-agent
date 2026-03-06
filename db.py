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

            -- ====================
            -- Ideas (raw thoughts/inspirations)
            -- ====================
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                status TEXT DEFAULT 'raw',
                project_id INTEGER,
                source TEXT DEFAULT 'manual',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ideas_guild ON ideas(guild_id, status);

            CREATE VIRTUAL TABLE IF NOT EXISTS idea_fts USING fts5(
                content, tags,
                content='ideas', content_rowid='id',
                tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS idea_ai AFTER INSERT ON ideas BEGIN
                INSERT INTO idea_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS idea_ad AFTER DELETE ON ideas BEGIN
                INSERT INTO idea_fts(idea_fts, rowid, content, tags) VALUES ('delete', old.id, old.content, old.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS idea_au AFTER UPDATE ON ideas BEGIN
                INSERT INTO idea_fts(idea_fts, rowid, content, tags) VALUES ('delete', old.id, old.content, old.tags);
                INSERT INTO idea_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
            END;

            -- ====================
            -- Projects (structured visions)
            -- ====================
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                vision_doc TEXT DEFAULT '',
                status TEXT DEFAULT 'planning',
                priority INTEGER DEFAULT 5,
                tags TEXT DEFAULT '[]',
                milestones TEXT DEFAULT '[]',
                github_repo TEXT,
                notes TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_guild ON projects(guild_id, status);

            CREATE VIRTUAL TABLE IF NOT EXISTS project_fts USING fts5(
                title, description, vision_doc, tags,
                content='projects', content_rowid='id',
                tokenize='unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS project_ai AFTER INSERT ON projects BEGIN
                INSERT INTO project_fts(rowid, title, description, vision_doc, tags)
                VALUES (new.id, new.title, new.description, new.vision_doc, new.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS project_ad AFTER DELETE ON projects BEGIN
                INSERT INTO project_fts(project_fts, rowid, title, description, vision_doc, tags)
                VALUES ('delete', old.id, old.title, old.description, old.vision_doc, old.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS project_au AFTER UPDATE ON projects BEGIN
                INSERT INTO project_fts(project_fts, rowid, title, description, vision_doc, tags)
                VALUES ('delete', old.id, old.title, old.description, old.vision_doc, old.tags);
                INSERT INTO project_fts(rowid, title, description, vision_doc, tags)
                VALUES (new.id, new.title, new.description, new.vision_doc, new.tags);
            END;
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

    # ── Data lifecycle management ─────────────────────────────────────────

    async def get_db_stats(self) -> dict:
        """Get database size and row counts for monitoring."""
        import os
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        tables = {}
        for table in ["conversations", "message_archive", "memories", "scheduled_tasks", "task_executions", "audit_log"]:
            try:
                cursor = await self.conn.execute(f"SELECT count(*) FROM {table}")
                row = await cursor.fetchone()
                tables[table] = row[0]
            except Exception:
                tables[table] = 0
        return {"db_size_bytes": db_size, "db_size_mb": round(db_size / 1048576, 2), "tables": tables}

    async def cleanup_old_data(
        self,
        conversation_days: int = 30,
        archive_days: int = 90,
        execution_days: int = 30,
        audit_days: int = 30,
    ) -> dict:
        """Delete old data to keep DB size manageable. Returns count of deleted rows."""
        import time as _time
        deleted = {}

        # Old conversations (by updated_at timestamp)
        cutoff = _time.time() - (conversation_days * 86400)
        cursor = await self.conn.execute(
            "DELETE FROM conversations WHERE updated_at < ?", (cutoff,)
        )
        deleted["conversations"] = cursor.rowcount

        # Old message archive entries
        cutoff_iso = _time.strftime("%Y-%m-%d", _time.gmtime(_time.time() - archive_days * 86400))
        cursor = await self.conn.execute(
            "DELETE FROM message_archive WHERE timestamp < ?", (cutoff_iso,)
        )
        deleted["message_archive"] = cursor.rowcount

        # Old task executions
        cutoff = _time.time() - (execution_days * 86400)
        cursor = await self.conn.execute(
            "DELETE FROM task_executions WHERE started_at < ?", (cutoff,)
        )
        deleted["task_executions"] = cursor.rowcount

        # Old audit logs
        cutoff = _time.time() - (audit_days * 86400)
        cursor = await self.conn.execute(
            "DELETE FROM audit_log WHERE timestamp < ?", (cutoff,)
        )
        deleted["audit_log"] = cursor.rowcount

        await self.conn.commit()

        # Reclaim space
        total = sum(deleted.values())
        if total > 0:
            await self.conn.execute("PRAGMA incremental_vacuum")

        return deleted

    async def get_memory_stats(self, guild_id: str) -> dict:
        """Get memory usage stats for a guild."""
        cursor = await self.conn.execute(
            "SELECT count(*), COALESCE(SUM(LENGTH(content)), 0) FROM memories WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        count, total_bytes = row[0], row[1]

        cursor = await self.conn.execute(
            "SELECT category, count(*) FROM memories WHERE guild_id = ? GROUP BY category ORDER BY count(*) DESC",
            (guild_id,),
        )
        categories = {r[0]: r[1] for r in await cursor.fetchall()}

        return {"count": count, "total_bytes": total_bytes, "categories": categories}

    async def cleanup_memories(
        self, guild_id: str, max_memories: int = 1000, keep_important: int = 5
    ) -> int:
        """If memory count exceeds max, delete lowest-value memories.
        Value = importance * (1 + log(access_count + 1)) — keeps frequently accessed and important ones.
        Memories with importance >= keep_important are never auto-deleted.
        Returns number deleted."""
        import math
        cursor = await self.conn.execute(
            "SELECT count(*) FROM memories WHERE guild_id = ?", (guild_id,),
        )
        count = (await cursor.fetchone())[0]
        if count <= max_memories:
            return 0

        to_delete = count - max_memories
        # Delete lowest-value memories that aren't high-importance
        # Score: importance * (access_count + 1) — simple but effective
        cursor = await self.conn.execute(
            """DELETE FROM memories WHERE id IN (
                SELECT id FROM memories
                WHERE guild_id = ? AND importance < ?
                ORDER BY importance * (COALESCE(access_count, 0) + 1) ASC,
                         last_accessed_at ASC NULLS FIRST
                LIMIT ?
            )""",
            (guild_id, keep_important, to_delete),
        )
        deleted = cursor.rowcount
        await self.conn.commit()
        return deleted

    # ---------------------------------------------------------------
    # Ideas
    # ---------------------------------------------------------------
    async def add_idea(
        self, guild_id: str, user_id: str, content: str,
        tags: Optional[list] = None, source: str = "manual",
    ) -> int:
        """Store a new idea and return its id."""
        now = time.time()
        tags_json = json.dumps(tags or [])
        cursor = await self.conn.execute(
            "INSERT INTO ideas (guild_id, user_id, content, tags, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, content, tags_json, source, now, now),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def list_ideas(
        self, guild_id: str, status: Optional[str] = None, limit: int = 20,
    ) -> list[dict]:
        """List ideas for a guild, optionally filtered by status."""
        if status:
            cursor = await self.conn.execute(
                "SELECT id, user_id, content, tags, status, project_id, source, created_at, updated_at "
                "FROM ideas WHERE guild_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                (guild_id, status, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT id, user_id, content, tags, status, project_id, source, created_at, updated_at "
                "FROM ideas WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
                (guild_id, limit),
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "user_id": r[1], "content": r[2],
                "tags": json.loads(r[3]) if r[3] else [],
                "status": r[4], "project_id": r[5], "source": r[6],
                "created_at": r[7], "updated_at": r[8],
            }
            for r in rows
        ]

    async def search_ideas(
        self, guild_id: str, query: str, limit: int = 10,
    ) -> list[dict]:
        """Full-text search across ideas."""
        if not query or not query.strip():
            return []
        safe_q = query.replace('"', '""')
        fts_query = f'"{ safe_q}"'
        cursor = await self.conn.execute(
            """
            SELECT i.id, i.user_id, i.content, i.tags, i.status, i.project_id,
                   i.source, i.created_at, i.updated_at
            FROM idea_fts f
            JOIN ideas i ON i.id = f.rowid
            WHERE f.idea_fts MATCH ? AND i.guild_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, guild_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "user_id": r[1], "content": r[2],
                "tags": json.loads(r[3]) if r[3] else [],
                "status": r[4], "project_id": r[5], "source": r[6],
                "created_at": r[7], "updated_at": r[8],
            }
            for r in rows
        ]

    async def update_idea(self, guild_id: str, idea_id: int, **kwargs) -> bool:
        """Update an idea's fields (status, content, tags, project_id)."""
        allowed = {"status", "content", "tags", "project_id"}
        updates = []
        values = []
        for key, val in kwargs.items():
            if key not in allowed:
                continue
            if key == "tags":
                val = json.dumps(val) if isinstance(val, list) else val
            updates.append(f"{key} = ?")
            values.append(val)
        if not updates:
            return False
        updates.append("updated_at = ?")
        values.append(time.time())
        values.extend([idea_id, guild_id])
        cursor = await self.conn.execute(
            f"UPDATE ideas SET {', '.join(updates)} WHERE id = ? AND guild_id = ?",
            values,
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_idea(self, guild_id: str, idea_id: int) -> bool:
        """Delete an idea by id."""
        cursor = await self.conn.execute(
            "DELETE FROM ideas WHERE id = ? AND guild_id = ?",
            (idea_id, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # ---------------------------------------------------------------
    # Projects
    # ---------------------------------------------------------------
    async def create_project(
        self, guild_id: str, user_id: str, title: str, description: str,
        vision_doc: str = "", priority: int = 5, tags: Optional[list] = None,
        github_repo: Optional[str] = None,
    ) -> int:
        """Create a new project and return its id."""
        now = time.time()
        tags_json = json.dumps(tags or [])
        cursor = await self.conn.execute(
            "INSERT INTO projects "
            "(guild_id, user_id, title, description, vision_doc, priority, tags, github_repo, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, title, description, vision_doc, priority, tags_json, github_repo, now, now),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_project(self, guild_id: str, project_id: int) -> Optional[dict]:
        """Get a single project by id."""
        cursor = await self.conn.execute(
            "SELECT id, user_id, title, description, vision_doc, status, priority, "
            "tags, milestones, github_repo, notes, created_at, updated_at "
            "FROM projects WHERE id = ? AND guild_id = ?",
            (project_id, guild_id),
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "user_id": r[1], "title": r[2], "description": r[3],
            "vision_doc": r[4], "status": r[5], "priority": r[6],
            "tags": json.loads(r[7]) if r[7] else [],
            "milestones": json.loads(r[8]) if r[8] else [],
            "github_repo": r[9], "notes": r[10],
            "created_at": r[11], "updated_at": r[12],
        }

    async def list_projects(
        self, guild_id: str, status: Optional[str] = None, limit: int = 20,
    ) -> list[dict]:
        """List projects for a guild, optionally filtered by status."""
        if status:
            cursor = await self.conn.execute(
                "SELECT id, user_id, title, description, vision_doc, status, priority, "
                "tags, milestones, github_repo, notes, created_at, updated_at "
                "FROM projects WHERE guild_id = ? AND status = ? ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (guild_id, status, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT id, user_id, title, description, vision_doc, status, priority, "
                "tags, milestones, github_repo, notes, created_at, updated_at "
                "FROM projects WHERE guild_id = ? ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (guild_id, limit),
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "user_id": r[1], "title": r[2], "description": r[3],
                "vision_doc": r[4], "status": r[5], "priority": r[6],
                "tags": json.loads(r[7]) if r[7] else [],
                "milestones": json.loads(r[8]) if r[8] else [],
                "github_repo": r[9], "notes": r[10],
                "created_at": r[11], "updated_at": r[12],
            }
            for r in rows
        ]

    async def search_projects(
        self, guild_id: str, query: str, limit: int = 10,
    ) -> list[dict]:
        """Full-text search across projects."""
        if not query or not query.strip():
            return []
        safe_q = query.replace('"', '""')
        fts_query = f'"{ safe_q}"'
        cursor = await self.conn.execute(
            """
            SELECT p.id, p.user_id, p.title, p.description, p.vision_doc, p.status,
                   p.priority, p.tags, p.milestones, p.github_repo, p.notes,
                   p.created_at, p.updated_at
            FROM project_fts f
            JOIN projects p ON p.id = f.rowid
            WHERE f.project_fts MATCH ? AND p.guild_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, guild_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "user_id": r[1], "title": r[2], "description": r[3],
                "vision_doc": r[4], "status": r[5], "priority": r[6],
                "tags": json.loads(r[7]) if r[7] else [],
                "milestones": json.loads(r[8]) if r[8] else [],
                "github_repo": r[9], "notes": r[10],
                "created_at": r[11], "updated_at": r[12],
            }
            for r in rows
        ]

    async def update_project(self, guild_id: str, project_id: int, **kwargs) -> bool:
        """Update a project's fields."""
        allowed = {
            "title", "description", "vision_doc", "status", "priority",
            "tags", "milestones", "github_repo", "notes",
        }
        updates = []
        values = []
        for key, val in kwargs.items():
            if key not in allowed:
                continue
            if key in ("tags", "milestones"):
                val = json.dumps(val) if isinstance(val, list) else val
            updates.append(f"{key} = ?")
            values.append(val)
        if not updates:
            return False
        updates.append("updated_at = ?")
        values.append(time.time())
        values.extend([project_id, guild_id])
        cursor = await self.conn.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = ? AND guild_id = ?",
            values,
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_active_projects(
        self, guild_id: str, limit: int = 2,
    ) -> list[dict]:
        """Get active projects ordered by priority (highest first)."""
        cursor = await self.conn.execute(
            "SELECT id, user_id, title, description, vision_doc, status, priority, "
            "tags, milestones, github_repo, notes, created_at, updated_at "
            "FROM projects WHERE guild_id = ? AND status = 'active' "
            "ORDER BY priority DESC LIMIT ?",
            (guild_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "user_id": r[1], "title": r[2], "description": r[3],
                "vision_doc": r[4], "status": r[5], "priority": r[6],
                "tags": json.loads(r[7]) if r[7] else [],
                "milestones": json.loads(r[8]) if r[8] else [],
                "github_repo": r[9], "notes": r[10],
                "created_at": r[11], "updated_at": r[12],
            }
            for r in rows
        ]

    async def delete_project(self, guild_id: str, project_id: int) -> bool:
        """Delete a project by id."""
        cursor = await self.conn.execute(
            "DELETE FROM projects WHERE id = ? AND guild_id = ?",
            (project_id, guild_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

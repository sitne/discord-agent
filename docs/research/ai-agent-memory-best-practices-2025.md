# AI Agent Memory Best Practices 2025-2026

> Research compiled from Mem0 docs, Letta/MemGPT blog & deep wiki, Substratia (memory-mcp), and Maxim AI.
> Date: July 2025

---

## 1. Mem0's Approach: Graph Memory + Vectors

**Architecture:** Dual-store — vector DB for embeddings + graph DB (Neo4j/Memgraph/Kuzu) for relationships.

**Memory scoping:** Memories are scoped by `user_id`, `session_id`, or `agent_id`.

**How it works:**
1. On `memory.add()`, an extraction LLM identifies **entities, relationships, and timestamps** from conversation.
2. Embeddings go to vector DB; nodes + edges go to graph backend.
3. On `memory.search()`, vector similarity narrows candidates; graph returns **related entities** in a `relations` array (parallel retrieval).

**Key insight:** Graph memory solves the "who did what to whom" problem that vectors alone blur together. Vectors find similar content; graphs find connected context.

**Operational guidance from Mem0:**
- Toggle graph writes per request (routine conversations can stay vector-only to save latency)
- Prune stale relationships: nodes not accessed in 90 days get deleted
- Graph edges do NOT reorder vector search results — they augment them

**Takeaway for us:** We don't need a graph DB, but we should store **structured relationships** (who said what, about whom) as metadata fields rather than just free text. Our FTS5 approach can use tagged fields for this.

---

## 2. Letta/MemGPT: Memory Blocks + Self-Editing Memory

### Three-Tier Memory Architecture

| Layer | Purpose | Storage | Max Size | Search Method |
|-------|---------|---------|----------|---------------|
| **Core Memory** | Always-present agent identity & user context | In-context blocks | Per-block char limit (default 2000) | Direct access by label |
| **Recall Memory** | Recent conversation history | SQL + vector embeddings | Limited by context window | Text search + semantic similarity |
| **Archival Memory** | Long-term knowledge storage | Vector DB (passages) | Unlimited | Semantic similarity + tags + timestamps |

### Memory Blocks (Core Memory)

Each block has:
- **Label** — purpose identifier (e.g., "human", "persona", "knowledge")
- **Value** — string content (can encode lists, dicts, etc.)
- **Size limit** — character cap to control context window allocation
- **Read-only flag** — developer-only vs agent-editable
- **Description** — guides the agent on how to use the block

**Self-editing memory:** The agent has tools to modify its own memory blocks:
- `memory_replace(label, old_str, new_str)` — precise string replacement in a block
- `memory_insert(label, line_number, value)` — insert at specific line
- `core_memory_append(label, content)` — append to block end
- The system validates: old_str must exist exactly once, new value must fit within char limit

**Block versioning:** Optimistic locking via version column; `checkpoint_block_async()` saves snapshots; `restore_checkpoint_async()` reverts.

### Recall Memory (Conversation Search)
- Messages stored in SQL with `sequence_id` (monotonic ordering)
- Background async task embeds messages for vector search (eventual consistency)
- Hybrid search: vector similarity + text search + temporal filtering
- Search latency: semantic ~50-200ms, text ~10-50ms, hybrid ~100-300ms

### Key Patterns

1. **"Sleep-time compute"** — Background agents process conversation history during idle time to form consolidated memories ("learned context") written to shared memory blocks.
2. **Multi-agent shared memory** — Multiple agents can read/write the same block via junction tables.
3. **Context window compilation** — `Memory.compile()` renders all blocks into formatted strings (XML or JSON) injected into the LLM prompt.

**Takeaway for us:** The core memory block pattern is directly applicable. Our bot should have:
- A **persona block** (bot identity, personality — read-only)
- A **user block** per user (preferences, facts learned — agent-editable)
- The `remember` tool is analogous to `core_memory_append` / `archival_memory_insert`
- The `recall` tool is analogous to `conversation_search` / `archival_memory_search`

---

## 3. FTS5 vs Embeddings: When FTS5 Wins

**Source:** Substratia's memory-mcp project (migrated from Python+embeddings to TypeScript+FTS5)

### The Case Against Embeddings for Small-Scale Memory

| Metric | Embeddings (old) | FTS5 (new) |
|--------|------------------|------------|
| Model weight | 46MB (sentence-transformers) | 0 |
| Startup time | 30+ seconds | <1 second |
| Tokens per response | 1,500+ | 88 (hot context) |
| External deps | PyTorch, NumPy | better-sqlite3 (or built-in) |
| Concurrent access | File locks | SQLite WAL mode |

### FTS5 Capabilities
- **BM25 ranking** — same algorithm as Elasticsearch/Lucene
- **Phrase queries** — `"authentication flow"` as exact phrase
- **Boolean operators** — AND, OR, NOT
- **Prefix matching** — `auth*` matches authentication, authorize
- **Column weights** — prioritize title matches over body matches

### Hybrid Scoring Formula (memory-mcp)
```
score = 0.4 * relevance + 0.3 * importance + 0.2 * recency + 0.1 * frequency
```
This multi-factor scoring means a highly relevant old memory can still outrank a recent but tangential one.

### 3-Tier Token Budget System
| Tier | ~Tokens | Content |
|------|---------|--------|
| Minimal | ~30 | Just the summary |
| Standard | ~200 | Summary + key context |
| Full | ~500 | Everything including metadata |

### When Embeddings ARE Better
- **Millions of documents** — can't brute-force at scale
- **Cross-lingual search** — semantic meaning crosses languages
- **Image/text similarity** — cross-modal requires embeddings
- **Typo tolerance** — "authenication" won't find "authentication" in FTS5
- **Synonyms** — "car" won't match "automobile" in FTS5

### Decision Tree
- Dataset < 10K documents → **FTS5**
- Need semantic/cross-lingual → **Embeddings** (external service)
- Local-first, no external deps → **FTS5 is the only sane choice**

### Database Schema (memory-mcp)
```
SQLite Database
├── memories (main table)
│   ├── id, content, summary
│   ├── importance, created_at
│   ├── access_count, last_accessed
│   └── tags (JSON array)
├── memories_fts (FTS5 virtual table)
│   └── Indexed: content, summary, tags
└── Hybrid scoring query
    └── BM25 + importance + recency + frequency
```

**Takeaway for us:** This validates our SQLite + FTS5 approach perfectly. We should implement:
- The hybrid scoring formula (BM25 + importance + recency + frequency)
- Token-budget-aware responses (minimal/standard/full tiers)
- Summary field alongside full content

---

## 4. Memory Tiers: Episodic vs Semantic vs Procedural

**Source:** Maxim AI article + synthesized from all sources

### Three Memory Types

| Type | What It Stores | Example | Retrieval Pattern |
|------|---------------|---------|-------------------|
| **Episodic** | Specific events/interactions | "User asked about X on Tuesday" | Temporal + keyword search |
| **Semantic** | Facts, preferences, knowledge | "User prefers dark mode" | Keyword/semantic search |
| **Procedural** | How-to knowledge, workflows | "To deploy, run X then Y" | Pattern matching, task context |

### Hierarchical Memory Design (from Maxim)
- Organize knowledge across **granularity levels**: facts → procedures → narratives
- Use **topic taxonomies** or tags layered over the search index
- Enable retrieval at the **right abstraction level** — precise lookups AND generalization

### Memory Schema Best Practices
- Define **explicit memory types** (facts, preferences, tasks, constraints)
- Attribute **provenance** (who said it, when, confidence level)
- Include **timestamps** for temporal reasoning
- Use **chunking strategies** aligned with content structure

### Self-Reflection and Selection
- Agents should **meta-analyze sessions** to decide what to remember
- Score memory candidates by: **task success, novelty, conflict resolution**
- Generate **concise memory entries** (not raw conversation dumps)

**Takeaway for us:** Tag memories with type (fact/preference/event/procedure). The `remember` tool should extract structured facts, not just store raw text.

---

## 5. Memory Consolidation: Compacting Old Memories

### Patterns from the Research

**Letta's approach (sleep-time compute):**
- Background agents run during idle periods
- Review conversation history → extract insights → write to memory blocks
- "Learned context" = distilled understanding from multiple interactions
- Shared across agents via memory blocks with `block_id`

**Experience replay (from Maxim/RL research):**
- Batch and replay high-quality or rare trajectories
- Prevents catastrophic forgetting of edge cases
- Score memories by utility before deciding to keep/merge/discard

**Practical consolidation strategies:**

1. **Summarize-and-replace:** Periodically LLM-summarize clusters of related memories into single consolidated entries. Delete originals.
2. **Importance decay:** Memories not accessed lose importance over time. Below threshold → candidate for deletion or archival.
3. **Deduplication:** Detect near-duplicate memories and merge them, keeping the most recent/complete version.
4. **Hierarchical rollup:** Individual event memories → weekly summaries → monthly themes.

**Mem0's graph pruning rule:** Delete nodes not accessed in 90 days: `MATCH (n) WHERE n.lastSeen < date() - duration('P90D') DETACH DELETE n`

**Takeaway for us:** Implement a consolidation routine that:
- Runs periodically (or on memory count threshold)
- Groups related memories by user + topic
- LLM-summarizes groups into single entries
- Applies importance decay to `last_accessed` tracking
- The `forget` tool should support both explicit deletion and age-based cleanup

---

## 6. Actionable Recommendations for Our Discord Bot

### Architecture: SQLite + FTS5 Memory System

#### Schema Design
```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,         -- Discord user ID (or 'global')
    guild_id TEXT,                 -- Discord server scope
    content TEXT NOT NULL,         -- Full memory text
    summary TEXT,                  -- LLM-generated short summary
    memory_type TEXT DEFAULT 'fact', -- fact|preference|event|procedure
    importance REAL DEFAULT 0.5,  -- 0.0-1.0, set by LLM on creation
    source TEXT,                   -- 'user_explicit', 'agent_inferred', 'consolidation'
    tags TEXT,                     -- JSON array for categorization
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER DEFAULT 0,
    metadata TEXT                  -- JSON blob for extensibility
);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, summary, tags,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE INDEX idx_memories_user ON memories(user_id);
CREATE INDEX idx_memories_guild ON memories(guild_id);
CREATE INDEX idx_memories_type ON memories(memory_type);
CREATE INDEX idx_memories_importance ON memories(importance);
CREATE INDEX idx_memories_accessed ON memories(last_accessed);
```

#### Hybrid Scoring Query
```sql
SELECT m.*, 
    (
        0.4 * (1.0 / (1.0 + ABS(fts.rank))) +   -- BM25 relevance (normalized)
        0.3 * m.importance +                       -- importance score
        0.2 * (1.0 / (1.0 + (julianday('now') - julianday(m.last_accessed)))) + -- recency
        0.1 * MIN(m.access_count / 10.0, 1.0)     -- frequency (capped)
    ) AS score
FROM memories m
JOIN memories_fts fts ON m.rowid = fts.rowid
WHERE memories_fts MATCH ?
    AND m.user_id IN (?, 'global')
ORDER BY score DESC
LIMIT ?;
```

#### Tool Implementations

**`remember` tool:**
1. Accept raw text from the agent
2. LLM extracts: content (clean fact), summary (one line), memory_type, importance (0-1), tags
3. Check for near-duplicates via FTS5 search on the content
4. If duplicate found with similarity > threshold → update existing memory
5. Otherwise insert new memory
6. Return confirmation with memory ID

**`recall` tool:**
1. Accept query string + optional filters (user_id, memory_type, limit)
2. Run hybrid scoring query (BM25 + importance + recency + frequency)
3. Update `last_accessed` and `access_count` for returned memories
4. Return results using token budget tiers:
   - If <5 results: full tier (~500 tokens each)
   - If 5-10 results: standard tier (~200 tokens each)
   - If >10 results: minimal tier (~30 tokens each)

**`forget` tool:**
1. Accept either: specific memory ID, or query + user_id
2. If query-based: search first, confirm what will be deleted, then delete
3. Support bulk operations: forget all memories matching criteria
4. Soft-delete option: mark as `archived` rather than permanent deletion

#### Consolidation Routine (Background)

Run periodically (e.g., daily or when memory count per user exceeds threshold):

1. **Importance decay:** `UPDATE memories SET importance = importance * 0.95 WHERE last_accessed < datetime('now', '-7 days')`
2. **Group related memories** by user + overlapping tags/content
3. **LLM consolidation prompt:** "Given these N memories about [topic], produce a single consolidated memory that preserves all important facts."
4. **Insert consolidated memory** with `source = 'consolidation'`, delete originals
5. **Prune low-value:** Delete memories where `importance < 0.1 AND access_count = 0 AND last_accessed < datetime('now', '-30 days')`

#### Core Memory Blocks (In-Context, Letta-Style)

For always-available context without search:
```sql
CREATE TABLE core_blocks (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    label TEXT NOT NULL,           -- 'persona', 'user:{user_id}', 'server:{guild_id}'
    value TEXT NOT NULL DEFAULT '',
    char_limit INTEGER DEFAULT 2000,
    read_only BOOLEAN DEFAULT FALSE,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_id, label)
);
```

The **persona block** (read-only) goes into every system prompt. Per-user blocks get loaded when that user is active.

---

## Summary of Key Patterns

| Pattern | Source | Priority | Effort |
|---------|--------|----------|--------|
| FTS5 + BM25 hybrid scoring | memory-mcp | **Must-have** | Low |
| Memory importance + recency + frequency weighting | memory-mcp | **Must-have** | Low |
| Structured memory types (fact/pref/event/proc) | Maxim, Mem0 | **Must-have** | Low |
| Summary field + token budget tiers | memory-mcp | **Should-have** | Medium |
| Core memory blocks (persona, user) | Letta/MemGPT | **Should-have** | Medium |
| Self-editing memory (agent writes its own blocks) | Letta/MemGPT | **Should-have** | Medium |
| Duplicate detection on remember | Mem0 | **Should-have** | Medium |
| Background consolidation routine | Letta (sleep-time), Maxim | **Nice-to-have** | High |
| Graph-style relationships in metadata | Mem0 | **Nice-to-have** | High |
| Importance decay over time | All sources | **Nice-to-have** | Low |

---

## Sources
1. Mem0 Graph Memory docs — https://docs.mem0.ai/open-source/features/graph-memory
2. Letta Blog: Memory Blocks — https://www.letta.com/blog/memory-blocks
3. Substratia: Why FTS5 Over Embeddings — https://substratia.io/blog/why-fts5-over-embeddings/
4. Maxim AI: Demystifying AI Agent Memory — https://www.getmaxim.ai/articles/demystifying-ai-agent-memory-long-term-retention-strategies/
5. DeepWiki: Letta Memory System — https://deepwiki.com/letta-ai/letta/3-memory-system

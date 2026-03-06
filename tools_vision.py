"""Vision System tools — ideas capture, project tracking, and dashboard.

All tools are owner-only. They provide structured idea-to-project workflow
for the bot owner to capture thoughts, refine them, and track projects.
"""
import json
import logging
from datetime import datetime, timezone

from discord import Guild

from tools import tool
from tools_permissions import is_owner

log = logging.getLogger("tools.vision")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(ts: float | None) -> str:
    """Format a Unix timestamp as a human-readable UTC datetime string."""
    if not ts:
        return "N/A"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _owner_gate(kwargs: dict) -> str | None:
    """Return an error string if the caller is not the owner, else None."""
    if not is_owner(kwargs.get("user_id", "")):
        return "\u26d4 Vision tools are owner-only."
    return None


def _get_db(kwargs: dict):
    """Extract the database instance from kwargs."""
    return kwargs.get("db")


def _format_tags(tags) -> str:
    """Format tags for display."""
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            return tags or ""
    if isinstance(tags, list):
        return ", ".join(f"`{t}`" for t in tags) if tags else "—"
    return str(tags) if tags else "—"


STATUS_ICONS = {
    # Ideas
    "raw": "\U0001f4a1",         # 💡
    "refined": "\u2728",         # ✨
    "converted": "\u2705",       # ✅
    "archived": "\U0001f4e6",    # 📦
    # Projects
    "planning": "\U0001f4d0",    # 📐
    "active": "\U0001f680",      # 🚀
    "paused": "\u23f8\ufe0f",    # ⏸️
    "completed": "\U0001f3c6",   # 🏆
    "abandoned": "\U0001f6d1",   # 🛑
}


# ---------------------------------------------------------------------------
# Idea tools
# ---------------------------------------------------------------------------

@tool(
    name="capture_idea",
    description=(
        "Save a raw idea or thought for later refinement. "
        "Use this whenever the owner shares an idea, inspiration, or feature concept."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The idea content — can be rough notes, a sentence, or detailed description",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorisation (e.g. ['bot', 'feature', 'ui'])",
            },
            "source": {
                "type": "string",
                "description": "Where the idea came from (default: 'manual'). E.g. 'conversation', 'research', 'dream'",
            },
        },
        "required": ["content"],
    },
)
async def capture_idea(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    content = kwargs["content"]
    tags = kwargs.get("tags")
    source = kwargs.get("source", "manual")
    guild_id = str(guild.id)
    user_id = kwargs.get("user_id", str(guild.owner_id))

    idea_id = await db.add_idea(guild_id, user_id, content, tags=tags, source=source)
    tag_str = _format_tags(tags) if tags else "none"
    return (
        f"\U0001f4a1 **Idea #{idea_id} captured**\n"
        f"Content: {content[:200]}{'…' if len(content) > 200 else ''}\n"
        f"Tags: {tag_str} | Source: {source}"
    )


@tool(
    name="list_ideas",
    description="List saved ideas, optionally filtered by status. Shows newest first.",
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["raw", "refined", "converted", "archived"],
                "description": "Filter by idea status (optional, shows all if omitted)",
            },
            "limit": {
                "type": "integer",
                "description": "Max ideas to return (default 20, max 50)",
            },
        },
        "required": [],
    },
)
async def list_ideas(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    status = kwargs.get("status")
    limit = min(kwargs.get("limit", 20), 50)
    guild_id = str(guild.id)

    ideas = await db.list_ideas(guild_id, status=status, limit=limit)
    if not ideas:
        filter_msg = f" with status '{status}'" if status else ""
        return f"No ideas found{filter_msg}."

    header = f"**Ideas** ({len(ideas)}"
    if status:
        header += f", status: {status}"
    header += "):\n"

    lines = [header]
    for idea in ideas:
        icon = STATUS_ICONS.get(idea["status"], "\u2022")
        tags = _format_tags(idea.get("tags"))
        ts = _ts(idea.get("created_at"))
        proj = f" → project #{idea['project_id']}" if idea.get("project_id") else ""
        content_preview = idea["content"][:120].replace("\n", " ")
        if len(idea["content"]) > 120:
            content_preview += "…"
        lines.append(
            f"{icon} **#{idea['id']}** [{idea['status']}] {content_preview}\n"
            f"   Tags: {tags} | {ts}{proj}"
        )

    return "\n".join(lines)


@tool(
    name="search_ideas",
    description="Full-text search across all ideas. Searches content and tags.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (keywords)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10, max 30)",
            },
        },
        "required": ["query"],
    },
)
async def search_ideas(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    query = kwargs["query"]
    limit = min(kwargs.get("limit", 10), 30)
    guild_id = str(guild.id)

    results = await db.search_ideas(guild_id, query, limit=limit)
    if not results:
        return f"No ideas matching '{query}'."

    lines = [f"**Idea search: '{query}'** ({len(results)} results):"]
    for idea in results:
        icon = STATUS_ICONS.get(idea["status"], "\u2022")
        tags = _format_tags(idea.get("tags"))
        content_preview = idea["content"][:120].replace("\n", " ")
        if len(idea["content"]) > 120:
            content_preview += "…"
        lines.append(
            f"{icon} **#{idea['id']}** [{idea['status']}] {content_preview}\n"
            f"   Tags: {tags} | {_ts(idea.get('created_at'))}"
        )

    return "\n".join(lines)


@tool(
    name="update_idea",
    description="Update an existing idea's status, content, or tags.",
    parameters={
        "type": "object",
        "properties": {
            "idea_id": {
                "type": "integer",
                "description": "ID of the idea to update",
            },
            "status": {
                "type": "string",
                "enum": ["raw", "refined", "converted", "archived"],
                "description": "New status for the idea",
            },
            "content": {
                "type": "string",
                "description": "Updated content (replaces existing)",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated tags (replaces existing)",
            },
        },
        "required": ["idea_id"],
    },
)
async def update_idea(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    idea_id = kwargs["idea_id"]
    guild_id = str(guild.id)

    update_fields = {}
    if "status" in kwargs:
        update_fields["status"] = kwargs["status"]
    if "content" in kwargs:
        update_fields["content"] = kwargs["content"]
    if "tags" in kwargs:
        update_fields["tags"] = kwargs["tags"]

    if not update_fields:
        return "No fields to update. Provide at least one of: status, content, tags."

    ok = await db.update_idea(guild_id, idea_id, **update_fields)
    if not ok:
        return f"Idea #{idea_id} not found or no changes made."

    changes = ", ".join(f"{k}={v!r}" for k, v in update_fields.items())
    return f"\u2705 **Idea #{idea_id} updated**: {changes}"


# ---------------------------------------------------------------------------
# Project tools
# ---------------------------------------------------------------------------

@tool(
    name="create_project",
    description=(
        "Create a structured project, optionally from an existing idea. "
        "Projects track title, description, vision doc, priority, milestones, and GitHub repo."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Project title",
            },
            "description": {
                "type": "string",
                "description": "Project description / summary",
            },
            "vision_doc": {
                "type": "string",
                "description": "Markdown vision document — goals, scope, architecture notes (optional)",
            },
            "priority": {
                "type": "integer",
                "description": "Priority 1-10 (10 = highest, default 5)",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Project tags for categorisation",
            },
            "github_repo": {
                "type": "string",
                "description": "GitHub repo in 'owner/repo' format (optional)",
            },
            "from_idea_id": {
                "type": "integer",
                "description": "Link this project to an existing idea (sets idea status to 'converted')",
            },
        },
        "required": ["title", "description"],
    },
)
async def create_project(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    guild_id = str(guild.id)
    user_id = kwargs.get("user_id", str(guild.owner_id))
    title = kwargs["title"]
    description = kwargs["description"]
    vision_doc = kwargs.get("vision_doc", "")
    priority = max(1, min(10, kwargs.get("priority", 5)))
    tags = kwargs.get("tags")
    github_repo = kwargs.get("github_repo")
    from_idea_id = kwargs.get("from_idea_id")

    project_id = await db.create_project(
        guild_id, user_id, title, description,
        vision_doc=vision_doc, priority=priority, tags=tags, github_repo=github_repo,
    )

    # Link idea if specified
    idea_note = ""
    if from_idea_id:
        ok = await db.update_idea(
            guild_id, from_idea_id, status="converted", project_id=project_id,
        )
        if ok:
            idea_note = f"\n\U0001f517 Linked from idea #{from_idea_id} (now converted)"
        else:
            idea_note = f"\n\u26a0\ufe0f Idea #{from_idea_id} not found — project created without link"

    tag_str = _format_tags(tags) if tags else "none"
    repo_str = f" | Repo: `{github_repo}`" if github_repo else ""
    return (
        f"\U0001f680 **Project #{project_id} created: {title}**\n"
        f"Priority: {'\u2b50' * min(priority, 5)} ({priority}/10)\n"
        f"Tags: {tag_str}{repo_str}\n"
        f"Description: {description[:200]}{'…' if len(description) > 200 else ''}"
        f"{idea_note}"
    )


@tool(
    name="list_projects",
    description="List projects, optionally filtered by status. Ordered by priority (highest first).",
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["planning", "active", "paused", "completed", "abandoned"],
                "description": "Filter by project status (optional, shows all if omitted)",
            },
            "limit": {
                "type": "integer",
                "description": "Max projects to return (default 20, max 50)",
            },
        },
        "required": [],
    },
)
async def list_projects(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    status = kwargs.get("status")
    limit = min(kwargs.get("limit", 20), 50)
    guild_id = str(guild.id)

    projects = await db.list_projects(guild_id, status=status, limit=limit)
    if not projects:
        filter_msg = f" with status '{status}'" if status else ""
        return f"No projects found{filter_msg}."

    header = f"**Projects** ({len(projects)}"
    if status:
        header += f", status: {status}"
    header += "):\n"

    lines = [header]
    for p in projects:
        icon = STATUS_ICONS.get(p["status"], "\u2022")
        tags = _format_tags(p.get("tags"))
        repo = f" | `{p['github_repo']}`" if p.get("github_repo") else ""
        ms = p.get("milestones") or []
        ms_done = sum(1 for m in ms if m.get("status") == "done")
        ms_str = f" | Milestones: {ms_done}/{len(ms)}" if ms else ""
        priority_str = f"P{p['priority']}"
        desc_preview = p["description"][:100].replace("\n", " ")
        if len(p["description"]) > 100:
            desc_preview += "…"
        lines.append(
            f"{icon} **#{p['id']} {p['title']}** [{p['status']}] ({priority_str})\n"
            f"   {desc_preview}\n"
            f"   Tags: {tags}{repo}{ms_str} | Updated: {_ts(p.get('updated_at'))}"
        )

    return "\n".join(lines)


@tool(
    name="update_project",
    description=(
        "Update a project's fields — title, description, vision doc, status, "
        "priority, tags, milestones, GitHub repo, or notes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "project_id": {
                "type": "integer",
                "description": "ID of the project to update",
            },
            "title": {
                "type": "string",
                "description": "New project title",
            },
            "description": {
                "type": "string",
                "description": "New project description",
            },
            "vision_doc": {
                "type": "string",
                "description": "Updated vision document (markdown)",
            },
            "status": {
                "type": "string",
                "enum": ["planning", "active", "paused", "completed", "abandoned"],
                "description": "New project status",
            },
            "priority": {
                "type": "integer",
                "description": "New priority (1-10)",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated tags (replaces existing)",
            },
            "milestones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Milestone name"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done"],
                            "description": "Milestone status",
                        },
                        "due_date": {
                            "type": "string",
                            "description": "Due date (YYYY-MM-DD format, optional)",
                        },
                    },
                    "required": ["name", "status"],
                },
                "description": "Project milestones (replaces existing)",
            },
            "github_repo": {
                "type": "string",
                "description": "GitHub repo in 'owner/repo' format",
            },
            "notes": {
                "type": "string",
                "description": "Free-form notes (replaces existing)",
            },
        },
        "required": ["project_id"],
    },
)
async def update_project(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    project_id = kwargs["project_id"]
    guild_id = str(guild.id)

    # Collect only the allowed update fields that were actually provided
    allowed_keys = {
        "title", "description", "vision_doc", "status", "priority",
        "tags", "milestones", "github_repo", "notes",
    }
    update_fields = {}
    for key in allowed_keys:
        if key in kwargs:
            value = kwargs[key]
            # Clamp priority
            if key == "priority":
                value = max(1, min(10, value))
            update_fields[key] = value

    if not update_fields:
        return (
            "No fields to update. Provide at least one of: "
            "title, description, vision_doc, status, priority, tags, milestones, github_repo, notes."
        )

    ok = await db.update_project(guild_id, project_id, **update_fields)
    if not ok:
        return f"Project #{project_id} not found or no changes made."

    # Build a summary of changes
    change_parts = []
    for k, v in update_fields.items():
        if k == "milestones":
            change_parts.append(f"milestones ({len(v)} items)")
        elif k == "tags":
            change_parts.append(f"tags → {_format_tags(v)}")
        elif k == "vision_doc":
            change_parts.append(f"vision_doc ({len(v)} chars)")
        elif k == "notes":
            change_parts.append(f"notes ({len(v)} chars)")
        else:
            change_parts.append(f"{k} → {v!r}")

    return f"\u2705 **Project #{project_id} updated**: {', '.join(change_parts)}"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@tool(
    name="project_dashboard",
    description=(
        "Get an overview dashboard of all active projects, milestone progress, "
        "and recent unprocessed ideas. Great for daily check-ins."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def project_dashboard(guild: Guild, **kwargs) -> str:
    if err := _owner_gate(kwargs):
        return err
    db = _get_db(kwargs)
    if not db:
        return "Database not available."

    guild_id = str(guild.id)

    # Fetch data in parallel-ish (all awaited sequentially but fast on SQLite)
    active_projects = await db.list_projects(guild_id, status="active", limit=20)
    planning_projects = await db.list_projects(guild_id, status="planning", limit=10)
    paused_projects = await db.list_projects(guild_id, status="paused", limit=10)
    raw_ideas = await db.list_ideas(guild_id, status="raw", limit=50)
    refined_ideas = await db.list_ideas(guild_id, status="refined", limit=50)

    lines = ["\U0001f4ca **Project Dashboard**\n"]

    # ── Active Projects ──
    lines.append(f"\U0001f680 **Active Projects** ({len(active_projects)})")
    if active_projects:
        for p in active_projects:
            ms = p.get("milestones") or []
            ms_done = sum(1 for m in ms if m.get("status") == "done")
            ms_in_prog = sum(1 for m in ms if m.get("status") == "in_progress")
            ms_total = len(ms)

            # Progress bar
            if ms_total > 0:
                pct = int(ms_done / ms_total * 100)
                filled = int(pct / 10)
                bar = "\u2588" * filled + "\u2591" * (10 - filled)
                ms_str = f" [{bar}] {pct}% ({ms_done}/{ms_total})"
            else:
                ms_str = " (no milestones)"

            repo = f" | `{p['github_repo']}`" if p.get("github_repo") else ""
            lines.append(
                f"  **#{p['id']} {p['title']}** (P{p['priority']}){repo}\n"
                f"    Milestones:{ms_str}"
            )

            # Show in-progress milestones
            if ms_in_prog > 0:
                for m in ms:
                    if m.get("status") == "in_progress":
                        due = f" (due: {m['due_date']})" if m.get("due_date") else ""
                        lines.append(f"    \u23f3 {m['name']}{due}")
    else:
        lines.append("  No active projects.")

    # ── Planning Projects ──
    if planning_projects:
        lines.append(f"\n\U0001f4d0 **Planning** ({len(planning_projects)})")
        for p in planning_projects:
            lines.append(f"  **#{p['id']} {p['title']}** (P{p['priority']})")

    # ── Paused Projects ──
    if paused_projects:
        lines.append(f"\n\u23f8\ufe0f **Paused** ({len(paused_projects)})")
        for p in paused_projects:
            lines.append(f"  **#{p['id']} {p['title']}** (P{p['priority']})")

    # ── Ideas Summary ──
    lines.append(f"\n\U0001f4a1 **Ideas Inbox**")
    lines.append(f"  Raw ideas: **{len(raw_ideas)}** | Refined: **{len(refined_ideas)}**")

    if raw_ideas:
        lines.append("  Recent unprocessed:")
        for idea in raw_ideas[:5]:
            content_preview = idea["content"][:80].replace("\n", " ")
            if len(idea["content"]) > 80:
                content_preview += "…"
            lines.append(f"    \u2022 #{idea['id']}: {content_preview} ({_ts(idea.get('created_at'))})")
        if len(raw_ideas) > 5:
            lines.append(f"    ... and {len(raw_ideas) - 5} more")

    # ── Quick Stats ──
    all_projects = active_projects + planning_projects + paused_projects
    total_milestones = sum(len(p.get("milestones") or []) for p in all_projects)
    done_milestones = sum(
        sum(1 for m in (p.get("milestones") or []) if m.get("status") == "done")
        for p in all_projects
    )
    lines.append(f"\n\U0001f4c8 **Quick Stats**")
    lines.append(
        f"  Projects: {len(active_projects)} active, {len(planning_projects)} planning, {len(paused_projects)} paused\n"
        f"  Milestones: {done_milestones}/{total_milestones} complete\n"
        f"  Ideas: {len(raw_ideas)} raw, {len(refined_ideas)} refined"
    )

    return "\n".join(lines)

"""Discord tools that the AI agent can call.

Each tool is a dict with:
  - spec: OpenAI function-calling schema
  - execute: async callable(guild, **kwargs) -> str
"""
import discord
from discord import Guild, TextChannel, ChannelType, PermissionOverwrite
from typing import Any

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
TOOLS: list[dict] = []


def tool(name: str, description: str, parameters: dict):
    """Decorator to register a tool."""
    def decorator(func):
        TOOLS.append({
            "spec": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
            "execute": func,
        })
        return func
    return decorator


def get_tool_specs() -> list[dict]:
    return [t["spec"] for t in TOOLS]


def get_tool_executor(name: str):
    for t in TOOLS:
        if t["spec"]["function"]["name"] == name:
            return t["execute"]
    return None


# ---------------------------------------------------------------------------
# Information tools
# ---------------------------------------------------------------------------
@tool(
    "get_server_info",
    "Get information about the Discord server (name, member count, channels, roles).",
    {"type": "object", "properties": {}, "required": []},
)
async def get_server_info(guild: Guild, **kwargs) -> str:
    categories = [c for c in guild.channels if isinstance(c, discord.CategoryChannel)]
    text_channels = [c for c in guild.channels if isinstance(c, TextChannel)]
    voice_channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
    roles = [r.name for r in guild.roles if r.name != "@everyone"]
    return (
        f"**Server:** {guild.name}\n"
        f"**Members:** {guild.member_count}\n"
        f"**Categories:** {len(categories)}\n"
        f"**Text Channels:** {len(text_channels)}\n"
        f"**Voice Channels:** {len(voice_channels)}\n"
        f"**Roles:** {', '.join(roles[:30]) or 'None'}\n"
        f"**Owner:** {guild.owner}"
    )


@tool(
    "list_channels",
    "List all channels in the server, optionally filtered by category.",
    {
        "type": "object",
        "properties": {
            "category_name": {
                "type": "string",
                "description": "Filter by category name (optional)",
            },
        },
        "required": [],
    },
)
async def list_channels(guild: Guild, category_name: str = None, **kwargs) -> str:
    lines = []
    for cat in sorted(guild.categories, key=lambda c: c.position):
        if category_name and category_name.lower() not in cat.name.lower():
            continue
        lines.append(f"\n📁 **{cat.name}**")
        for ch in sorted(cat.channels, key=lambda c: c.position):
            prefix = "#" if isinstance(ch, TextChannel) else "🔊"
            lines.append(f"  {prefix} {ch.name} (`{ch.id}`)")
    # Uncategorized
    uncategorized = [c for c in guild.channels if c.category is None and not isinstance(c, discord.CategoryChannel)]
    if uncategorized and not category_name:
        lines.append("\n📁 **(Uncategorized)**")
        for ch in uncategorized:
            prefix = "#" if isinstance(ch, TextChannel) else "🔊"
            lines.append(f"  {prefix} {ch.name} (`{ch.id}`)")
    return "\n".join(lines) or "No channels found."


@tool(
    "list_roles",
    "List all roles in the server with member counts.",
    {"type": "object", "properties": {}, "required": []},
)
async def list_roles(guild: Guild, **kwargs) -> str:
    lines = []
    for r in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if r.name == "@everyone":
            continue
        lines.append(f"**{r.name}** — {len(r.members)} members (color: {r.color}, id: `{r.id}`)")
    return "\n".join(lines) or "No roles."


@tool(
    "get_member_info",
    "Get information about a server member by username or display name.",
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Username or display name to search for"},
        },
        "required": ["username"],
    },
)
async def get_member_info(guild: Guild, username: str, **kwargs) -> str:
    member = discord.utils.find(
        lambda m: username.lower() in m.name.lower() or username.lower() in m.display_name.lower(),
        guild.members,
    )
    if not member:
        return f"Member '{username}' not found."
    roles = [r.name for r in member.roles if r.name != "@everyone"]
    return (
        f"**{member.display_name}** ({member.name})\n"
        f"ID: `{member.id}`\n"
        f"Joined: {member.joined_at.strftime('%Y-%m-%d') if member.joined_at else 'Unknown'}\n"
        f"Roles: {', '.join(roles) or 'None'}\n"
        f"Bot: {member.bot}"
    )


@tool(
    "read_messages",
    "Read recent messages from a channel.",
    {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Channel name to read from"},
            "limit": {"type": "integer", "description": "Number of messages to read (default 10, max 50)"},
        },
        "required": ["channel_name"],
    },
)
async def read_messages(guild: Guild, channel_name: str, limit: int = 10, **kwargs) -> str:
    limit = min(limit, 50)
    ch = discord.utils.find(
        lambda c: channel_name.lower() in c.name.lower() and isinstance(c, TextChannel),
        guild.channels,
    )
    if not ch:
        return f"Channel '{channel_name}' not found."
    messages = []
    async for msg in ch.history(limit=limit):
        ts = msg.created_at.strftime("%m/%d %H:%M")
        messages.append(f"[{ts}] {msg.author.display_name}: {msg.content[:200]}")
    messages.reverse()
    return f"**#{ch.name}** (last {len(messages)} messages):\n" + "\n".join(messages)


# ---------------------------------------------------------------------------
# Channel management
# ---------------------------------------------------------------------------
@tool(
    "create_channel",
    "Create a new text or voice channel.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Channel name"},
            "type": {"type": "string", "enum": ["text", "voice"], "description": "Channel type (default: text)"},
            "category_name": {"type": "string", "description": "Category to place channel in (optional)"},
            "topic": {"type": "string", "description": "Channel topic (text channels only)"},
        },
        "required": ["name"],
    },
)
async def create_channel(guild: Guild, name: str, type: str = "text", category_name: str = None, topic: str = None, **kwargs) -> str:
    category = None
    if category_name:
        category = discord.utils.find(
            lambda c: category_name.lower() in c.name.lower() and isinstance(c, discord.CategoryChannel),
            guild.channels,
        )
    ch_type = ChannelType.voice if type == "voice" else ChannelType.text
    ch = await guild.create_text_channel(name=name, category=category, topic=topic) if ch_type == ChannelType.text \
        else await guild.create_voice_channel(name=name, category=category)
    return f"Created {'voice' if ch_type == ChannelType.voice else 'text'} channel #{ch.name} (`{ch.id}`)"


@tool(
    "delete_channel",
    "Delete a channel by name. Use with caution.",
    {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Channel name to delete"},
            "reason": {"type": "string", "description": "Reason for deletion"},
        },
        "required": ["channel_name"],
    },
)
async def delete_channel(guild: Guild, channel_name: str, reason: str = None, **kwargs) -> str:
    ch = discord.utils.find(
        lambda c: c.name.lower() == channel_name.lower(),
        guild.channels,
    )
    if not ch:
        return f"Channel '{channel_name}' not found."
    name = ch.name
    await ch.delete(reason=reason)
    return f"Deleted channel #{name}."


@tool(
    "edit_channel",
    "Edit a channel's name, topic, or slowmode.",
    {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Current channel name"},
            "new_name": {"type": "string", "description": "New channel name (optional)"},
            "topic": {"type": "string", "description": "New topic (optional)"},
            "slowmode_seconds": {"type": "integer", "description": "Slowmode delay in seconds (0 to disable)"},
        },
        "required": ["channel_name"],
    },
)
async def edit_channel(guild: Guild, channel_name: str, new_name: str = None, topic: str = None, slowmode_seconds: int = None, **kwargs) -> str:
    ch = discord.utils.find(
        lambda c: channel_name.lower() in c.name.lower() and isinstance(c, TextChannel),
        guild.channels,
    )
    if not ch:
        return f"Channel '{channel_name}' not found."
    kwargs_edit = {}
    if new_name:
        kwargs_edit["name"] = new_name
    if topic is not None:
        kwargs_edit["topic"] = topic
    if slowmode_seconds is not None:
        kwargs_edit["slowmode_delay"] = slowmode_seconds
    if not kwargs_edit:
        return "No changes specified."
    await ch.edit(**kwargs_edit)
    return f"Updated channel #{ch.name}."


@tool(
    "create_category",
    "Create a new channel category.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Category name"},
        },
        "required": ["name"],
    },
)
async def create_category(guild: Guild, name: str, **kwargs) -> str:
    cat = await guild.create_category(name=name)
    return f"Created category '{cat.name}' (`{cat.id}`)."


# ---------------------------------------------------------------------------
# Role management
# ---------------------------------------------------------------------------
@tool(
    "create_role",
    "Create a new role.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Role name"},
            "color": {"type": "string", "description": "Hex color code (e.g. '#ff0000')"},
            "mentionable": {"type": "boolean", "description": "Whether the role is mentionable"},
        },
        "required": ["name"],
    },
)
async def create_role(guild: Guild, name: str, color: str = None, mentionable: bool = False, **kwargs) -> str:
    c = discord.Color.from_str(color) if color else discord.Color.default()
    role = await guild.create_role(name=name, color=c, mentionable=mentionable)
    return f"Created role '{role.name}' (`{role.id}`)."


@tool(
    "assign_role",
    "Assign a role to a member.",
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Username or display name"},
            "role_name": {"type": "string", "description": "Role name to assign"},
        },
        "required": ["username", "role_name"],
    },
)
async def assign_role(guild: Guild, username: str, role_name: str, **kwargs) -> str:
    member = discord.utils.find(
        lambda m: username.lower() in m.name.lower() or username.lower() in m.display_name.lower(),
        guild.members,
    )
    if not member:
        return f"Member '{username}' not found."
    role = discord.utils.find(lambda r: role_name.lower() in r.name.lower(), guild.roles)
    if not role:
        return f"Role '{role_name}' not found."
    await member.add_roles(role)
    return f"Assigned role '{role.name}' to {member.display_name}."


@tool(
    "remove_role",
    "Remove a role from a member.",
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Username or display name"},
            "role_name": {"type": "string", "description": "Role name to remove"},
        },
        "required": ["username", "role_name"],
    },
)
async def remove_role(guild: Guild, username: str, role_name: str, **kwargs) -> str:
    member = discord.utils.find(
        lambda m: username.lower() in m.name.lower() or username.lower() in m.display_name.lower(),
        guild.members,
    )
    if not member:
        return f"Member '{username}' not found."
    role = discord.utils.find(lambda r: role_name.lower() in r.name.lower(), guild.roles)
    if not role:
        return f"Role '{role_name}' not found."
    await member.remove_roles(role)
    return f"Removed role '{role.name}' from {member.display_name}."


# ---------------------------------------------------------------------------
# Message management
# ---------------------------------------------------------------------------
@tool(
    "send_message",
    "Send a message to a specific channel.",
    {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Channel name to send to"},
            "content": {"type": "string", "description": "Message content"},
        },
        "required": ["channel_name", "content"],
    },
)
async def send_message(guild: Guild, channel_name: str, content: str, **kwargs) -> str:
    ch = discord.utils.find(
        lambda c: channel_name.lower() in c.name.lower() and isinstance(c, TextChannel),
        guild.channels,
    )
    if not ch:
        return f"Channel '{channel_name}' not found."
    await ch.send(content[:2000])
    return f"Sent message to #{ch.name}."


@tool(
    "pin_message",
    "Pin a message by its ID in a channel.",
    {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Channel name"},
            "message_id": {"type": "string", "description": "Message ID to pin"},
        },
        "required": ["channel_name", "message_id"],
    },
)
async def pin_message(guild: Guild, channel_name: str, message_id: str, **kwargs) -> str:
    ch = discord.utils.find(
        lambda c: channel_name.lower() in c.name.lower() and isinstance(c, TextChannel),
        guild.channels,
    )
    if not ch:
        return f"Channel '{channel_name}' not found."
    msg = await ch.fetch_message(int(message_id))
    await msg.pin()
    return f"Pinned message {message_id} in #{ch.name}."


@tool(
    "delete_messages",
    "Bulk delete recent messages from a channel.",
    {
        "type": "object",
        "properties": {
            "channel_name": {"type": "string", "description": "Channel name"},
            "count": {"type": "integer", "description": "Number of messages to delete (max 100)"},
        },
        "required": ["channel_name", "count"],
    },
)
async def delete_messages(guild: Guild, channel_name: str, count: int = 10, **kwargs) -> str:
    count = min(count, 100)
    ch = discord.utils.find(
        lambda c: channel_name.lower() in c.name.lower() and isinstance(c, TextChannel),
        guild.channels,
    )
    if not ch:
        return f"Channel '{channel_name}' not found."
    deleted = await ch.purge(limit=count)
    return f"Deleted {len(deleted)} messages from #{ch.name}."


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------
@tool(
    "kick_member",
    "Kick a member from the server.",
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Username or display name"},
            "reason": {"type": "string", "description": "Reason for kick"},
        },
        "required": ["username"],
    },
)
async def kick_member(guild: Guild, username: str, reason: str = None, **kwargs) -> str:
    member = discord.utils.find(
        lambda m: username.lower() in m.name.lower() or username.lower() in m.display_name.lower(),
        guild.members,
    )
    if not member:
        return f"Member '{username}' not found."
    await member.kick(reason=reason)
    return f"Kicked {member.display_name}. Reason: {reason or 'No reason provided'}"


@tool(
    "ban_member",
    "Ban a member from the server.",
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Username or display name"},
            "reason": {"type": "string", "description": "Reason for ban"},
        },
        "required": ["username"],
    },
)
async def ban_member(guild: Guild, username: str, reason: str = None, **kwargs) -> str:
    member = discord.utils.find(
        lambda m: username.lower() in m.name.lower() or username.lower() in m.display_name.lower(),
        guild.members,
    )
    if not member:
        return f"Member '{username}' not found."
    await member.ban(reason=reason)
    return f"Banned {member.display_name}. Reason: {reason or 'No reason provided'}"


@tool(
    "timeout_member",
    "Timeout (mute) a member for a duration.",
    {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Username or display name"},
            "duration_minutes": {"type": "integer", "description": "Timeout duration in minutes"},
            "reason": {"type": "string", "description": "Reason for timeout"},
        },
        "required": ["username", "duration_minutes"],
    },
)
async def timeout_member(guild: Guild, username: str, duration_minutes: int, reason: str = None, **kwargs) -> str:
    from datetime import timedelta
    member = discord.utils.find(
        lambda m: username.lower() in m.name.lower() or username.lower() in m.display_name.lower(),
        guild.members,
    )
    if not member:
        return f"Member '{username}' not found."
    await member.timeout(timedelta(minutes=duration_minutes), reason=reason)
    return f"Timed out {member.display_name} for {duration_minutes} minutes."


# ---------------------------------------------------------------------------
# Server message search (requires db access)
# ---------------------------------------------------------------------------
@tool(
    "search_server_messages",
    "Search through archived server message history. Use this to find past conversations, decisions, or information shared in the server.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (keywords)"},
            "channel_name": {"type": "string", "description": "Filter by channel name (optional)"},
            "author_name": {"type": "string", "description": "Filter by author name (optional)"},
            "limit": {"type": "integer", "description": "Max results (default 15, max 30)"},
        },
        "required": ["query"],
    },
)
async def search_server_messages(guild: Guild, query: str, channel_name: str = None, author_name: str = None, limit: int = 15, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    limit = min(limit, 30)
    results = await db.search_messages(
        guild_id=str(guild.id), query=query, channel_name=channel_name, author_name=author_name, limit=limit,
    )
    if not results:
        return f"No messages found for '{query}'."
    from datetime import datetime, timezone
    lines = [f"**Search results for '{query}'** ({len(results)} hits):"]
    for r in results:
        ts = datetime.fromtimestamp(r["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] **#{r['channel']}** {r['author']}: {r['content'][:150]}")
    return "\n".join(lines)


@tool(
    "get_archive_stats",
    "Get statistics about the archived message history.",
    {"type": "object", "properties": {}, "required": []},
)
async def get_archive_stats(guild: Guild, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    stats = await db.get_archive_stats(str(guild.id))
    from datetime import datetime, timezone
    oldest = datetime.fromtimestamp(stats["oldest"], tz=timezone.utc).strftime("%Y-%m-%d") if stats["oldest"] else "N/A"
    newest = datetime.fromtimestamp(stats["newest"], tz=timezone.utc).strftime("%Y-%m-%d") if stats["newest"] else "N/A"
    return f"**Archived Messages:** {stats['total_messages']:,}\n**Oldest:** {oldest}\n**Newest:** {newest}"


# ---------------------------------------------------------------------------
# Structured Memory
# ---------------------------------------------------------------------------
@tool(
    "remember",
    "Store important information in long-term memory. Use this proactively to remember server rules, user preferences, decisions, or any important context.",
    {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Category (e.g. 'server_rules', 'user_preferences', 'decisions', 'facts', 'todo')",
            },
            "key": {"type": "string", "description": "Short identifier for this memory (used for updates)"},
            "content": {"type": "string", "description": "The information to remember"},
            "importance": {
                "type": "integer",
                "description": "Importance score 1-10 (default 5). Use 8-10 for critical info like rules/decisions, 1-3 for minor notes.",
            },
        },
        "required": ["category", "key", "content"],
    },
)
async def remember(guild: Guild, category: str, key: str, content: str, importance: int = 5, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    user = kwargs.get("user_name", "unknown")
    await db.remember(str(guild.id), category, key, content, created_by=user, importance=importance)
    return f"Remembered [{category}] '{key}' (importance:{importance}): {content[:100]}..."


@tool(
    "recall",
    "Search long-term memory for previously stored information. Uses word-based matching (each word is searched independently).",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query - individual words are matched (optional if category is given)"},
            "category": {"type": "string", "description": "Filter by category (optional)"},
            "limit": {"type": "integer", "description": "Max results to return (default 10, max 50)"},
        },
        "required": [],
    },
)
async def recall(guild: Guild, query: str = None, category: str = None, limit: int = 10, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    limit = max(1, min(limit, 50))
    # If query provided, use relevance-based search
    if query:
        memories = await db.recall_relevant(str(guild.id), query, limit=limit)
        # Fall back to basic recall if FTS returns nothing
        if not memories:
            memories = await db.recall(str(guild.id), query=query, category=category, limit=limit)
    else:
        memories = await db.recall(str(guild.id), query=None, category=category, limit=limit)
    if not memories:
        return "No memories found."
    lines = [f"**Memories** ({len(memories)} found):"]
    for m in memories:
        imp = f" ⭐{m.get('importance', 5)}" if m.get('importance', 5) != 5 else ""
        lines.append(f"[{m['category']}] **{m['key']}** (id:{m['id']}){imp}: {m['content'][:200]}")
    return "\n".join(lines)


@tool(
    "forget",
    "Delete a specific memory by its ID.",
    {
        "type": "object",
        "properties": {
            "memory_id": {"type": "integer", "description": "Memory ID to delete"},
        },
        "required": ["memory_id"],
    },
)
async def forget(guild: Guild, memory_id: int, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    ok = await db.forget(str(guild.id), memory_id)
    return f"Memory {memory_id} deleted." if ok else f"Memory {memory_id} not found."


@tool(
    "forget_by_key",
    "Delete a memory by its category and key name (more intuitive than using ID).",
    {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Memory category"},
            "key": {"type": "string", "description": "Memory key to delete"},
        },
        "required": ["category", "key"],
    },
)
async def forget_by_key(guild: Guild, category: str, key: str, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    ok = await db.forget_by_key(str(guild.id), category, key)
    return f"Memory [{category}] '{key}' deleted." if ok else f"Memory [{category}] '{key}' not found."


@tool(
    "list_memory_categories",
    "List all memory categories.",
    {"type": "object", "properties": {}, "required": []},
)
async def list_memory_categories(guild: Guild, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    cats = await db.get_memory_categories(str(guild.id))
    return "**Memory categories:** " + ", ".join(cats) if cats else "No memories stored yet."


# ---------------------------------------------------------------------------
# Scheduled Tasks
# ---------------------------------------------------------------------------
@tool(
    "create_scheduled_task",
    "Create a scheduled task that runs automatically. The task prompt will be executed by the AI on the specified schedule.",
    {
        "type": "object",
        "properties": {
            "task_name": {"type": "string", "description": "Short name for the task"},
            "task_prompt": {"type": "string", "description": "The prompt/instruction to execute each run"},
            "schedule": {
                "type": "string",
                "description": "Cron expression (e.g. '0 9 * * *' for daily 9am UTC) or preset (@hourly, @daily, @weekly, @monthly)",
            },
        },
        "required": ["task_name", "task_prompt", "schedule"],
    },
)
async def create_scheduled_task(guild: Guild, task_name: str, task_prompt: str, schedule: str, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    from cron_parser import next_cron_time, describe_cron
    try:
        next_run = next_cron_time(schedule)
    except ValueError as e:
        return f"Invalid schedule: {e}"
    channel_id = kwargs.get("channel_id", "0")
    user_name = kwargs.get("user_name", "unknown")
    task_id = await db.create_task(
        guild_id=str(guild.id), channel_id=channel_id, created_by=user_name,
        task_name=task_name, task_prompt=task_prompt, cron_expression=schedule, next_run_at=next_run,
    )
    from datetime import datetime, timezone
    next_dt = datetime.fromtimestamp(next_run, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Created task #{task_id} '{task_name}'\nSchedule: {describe_cron(schedule)}\nNext run: {next_dt}"


@tool(
    "list_scheduled_tasks",
    "List all scheduled tasks for this server.",
    {"type": "object", "properties": {}, "required": []},
)
async def list_scheduled_tasks(guild: Guild, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    tasks = await db.list_tasks(str(guild.id))
    if not tasks:
        return "No scheduled tasks."
    from datetime import datetime, timezone
    from cron_parser import describe_cron
    lines = ["**Scheduled Tasks:**"]
    for t in tasks:
        status = "✅" if t["enabled"] else "❌"
        next_dt = datetime.fromtimestamp(t["next_run"], tz=timezone.utc).strftime("%m/%d %H:%M") if t["next_run"] else "N/A"
        lines.append(f"{status} **#{t['id']} {t['name']}** — {describe_cron(t['cron'])} — Next: {next_dt}")
        lines.append(f"   Prompt: {t['prompt'][:100]}")
    return "\n".join(lines)


@tool(
    "delete_scheduled_task",
    "Delete a scheduled task by ID.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "Task ID to delete"},
        },
        "required": ["task_id"],
    },
)
async def delete_scheduled_task(guild: Guild, task_id: int, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    ok = await db.delete_task(str(guild.id), task_id)
    return f"Task #{task_id} deleted." if ok else f"Task #{task_id} not found."


@tool(
    "toggle_scheduled_task",
    "Enable or disable a scheduled task.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "Task ID"},
            "enabled": {"type": "boolean", "description": "True to enable, False to disable"},
        },
        "required": ["task_id", "enabled"],
    },
)
async def toggle_scheduled_task(guild: Guild, task_id: int, enabled: bool, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    ok = await db.toggle_task(str(guild.id), task_id, enabled)
    state = "enabled" if enabled else "disabled"
    return f"Task #{task_id} {state}." if ok else f"Task #{task_id} not found."


@tool(
    "get_task_history",
    "Show recent execution history for a scheduled task, including status, errors, and timing.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "integer", "description": "Task ID to get history for"},
            "limit": {"type": "integer", "description": "Number of recent executions to show (default 10, max 25)"},
        },
        "required": ["task_id"],
    },
)
async def get_task_history(guild: Guild, task_id: int, limit: int = 10, **kwargs) -> str:
    db = kwargs.get("db")
    if not db:
        return "Database not available."
    limit = min(limit, 25)
    history = await db.get_task_execution_history(task_id, limit=limit)
    if not history:
        return f"No execution history for task #{task_id}."

    from datetime import datetime, timezone
    status_icons = {
        "success": "\u2705",
        "error": "\u274c",
        "error_retryable": "\u26a0\ufe0f",
        "timeout": "\u23f0",
        "running": "\u23f3",
    }
    lines = [f"**Execution history for task #{task_id}** (last {len(history)}):\n"]
    for ex in history:
        icon = status_icons.get(ex["status"], "\u2753")
        started = datetime.fromtimestamp(ex["started_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        duration = ""
        if ex["completed_at"]:
            dur_secs = ex["completed_at"] - ex["started_at"]
            duration = f" ({dur_secs:.1f}s)"
        line = f"{icon} **{ex['status']}** — {started} UTC{duration}"
        if ex["error_message"]:
            line += f"\n   Error: `{ex['error_message'][:150]}`"
        if ex["result_summary"]:
            line += f"\n   Result: {ex['result_summary'][:150]}"
        if ex["retry_count"]:
            line += f" (retry #{ex['retry_count']})"
        lines.append(line)
    return "\n".join(lines)


@tool(
    "db_stats",
    "Show database statistics including size, row counts, and memory usage per guild.",
    {
        "type": "object",
        "properties": {},
    },
)
async def db_stats(guild: Guild, **kwargs) -> str:
    db = guild._state._get_client().db  # type: ignore
    stats = await db.get_db_stats()
    mem_stats = await db.get_memory_stats(str(guild.id))

    lines = [
        f"**Database Statistics**",
        f"",
        f"**Size:** {stats['db_size_mb']:.2f} MB",
        f"",
        f"**Table row counts:**",
    ]
    for table, count in stats["tables"].items():
        lines.append(f"- {table}: {count:,}")

    lines.append(f"")
    lines.append(f"**Memory (this server):** {mem_stats['count']:,} entries ({mem_stats['total_bytes']:,} bytes)")
    if mem_stats["categories"]:
        for cat, cnt in mem_stats["categories"].items():
            lines.append(f"- {cat}: {cnt}")

    return "\n".join(lines)

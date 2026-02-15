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

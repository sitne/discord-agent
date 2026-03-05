"""Permission checking and confirmation UI for destructive operations."""
import discord
import asyncio
import logging

log = logging.getLogger("tools.permissions")

# Map tool names to required Discord permissions
PERMISSION_REQUIREMENTS = {
    "kick_member": discord.Permissions(kick_members=True),
    "ban_member": discord.Permissions(ban_members=True),
    "delete_messages": discord.Permissions(manage_messages=True),
    "delete_channel": discord.Permissions(manage_channels=True),
    "create_channel": discord.Permissions(manage_channels=True),
    "edit_channel": discord.Permissions(manage_channels=True),
    "create_category": discord.Permissions(manage_channels=True),
    "create_role": discord.Permissions(manage_roles=True),
    "assign_role": discord.Permissions(manage_roles=True),
    "remove_role": discord.Permissions(manage_roles=True),
    "timeout_member": discord.Permissions(moderate_members=True),
}

# Tools that require explicit confirmation via button
CONFIRMATION_REQUIRED = {
    "kick_member", "ban_member", "delete_messages", "delete_channel",
}


def check_permission(guild: discord.Guild, user_id: str, tool_name: str) -> tuple[bool, str]:
    """Check if a user has the required Discord permission for a tool.
    Returns (allowed, reason).
    """
    required = PERMISSION_REQUIREMENTS.get(tool_name)
    if not required:
        return True, ""

    member = guild.get_member(int(user_id))
    if not member:
        return False, "Member not found in server."

    # Server owner can do anything
    if member.id == guild.owner_id:
        return True, ""

    # Check permissions
    member_perms = member.guild_permissions
    for perm, value in required:
        if value and not getattr(member_perms, perm, False):
            return False, f"You need the `{perm}` permission to use `{tool_name}`."

    return True, ""


def needs_confirmation(tool_name: str) -> bool:
    """Check if a tool requires explicit user confirmation."""
    return tool_name in CONFIRMATION_REQUIRED


class ConfirmationView(discord.ui.View):
    """Discord button-based confirmation for destructive actions."""

    def __init__(self, requester_id: int, action_description: str, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.requester_id = requester_id
        self.action_description = action_description
        self.confirmed = None  # None = timed out, True = confirmed, False = cancelled
        self._event = asyncio.Event()

    @discord.ui.button(label="\u2705 Confirm", style=discord.ButtonStyle.danger, custom_id="confirm_action")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the requester can confirm this action.", ephemeral=True
            )
            return
        self.confirmed = True
        self._event.set()
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"\u2705 **Confirmed**: {self.action_description}",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="\u274c Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_action")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the requester can cancel this action.", ephemeral=True
            )
            return
        self.confirmed = False
        self._event.set()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"\u274c **Cancelled**: {self.action_description}",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        self.confirmed = None
        self._event.set()

    async def wait_for_result(self) -> bool | None:
        """Wait for user to click a button. Returns True/False/None(timeout)."""
        await self._event.wait()
        return self.confirmed


def _describe_action(tool_name: str, args: dict) -> str:
    """Create a human-readable description of the action."""
    descriptions = {
        "kick_member": lambda a: f"Kick **{a.get('username', '?')}**"
        + (f" (reason: {a.get('reason', 'none')})" if a.get("reason") else ""),
        "ban_member": lambda a: f"Ban **{a.get('username', '?')}**"
        + (f" (reason: {a.get('reason', 'none')})" if a.get("reason") else ""),
        "delete_messages": lambda a: f"Delete **{a.get('count', '?')}** messages from #{a.get('channel_name', '?')}",
        "delete_channel": lambda a: f"Delete channel **#{a.get('channel_name', '?')}**",
    }
    formatter = descriptions.get(tool_name)
    if formatter:
        return formatter(args)
    return f"Execute `{tool_name}` with args: {args}"


async def request_confirmation(
    channel: discord.TextChannel,
    requester_id: int,
    tool_name: str,
    tool_args: dict,
    timeout: float = 60.0,
) -> bool:
    """Send a confirmation embed with buttons. Returns True if confirmed."""
    description = _describe_action(tool_name, tool_args)

    embed = discord.Embed(
        title="\u26a0\ufe0f Confirmation Required",
        description=description,
        color=discord.Color.orange(),
    )
    embed.add_field(name="Action", value=f"`{tool_name}`", inline=True)
    embed.add_field(name="Timeout", value=f"{int(timeout)}s", inline=True)
    embed.set_footer(text="Only the original requester can confirm or cancel.")

    view = ConfirmationView(requester_id, description, timeout=timeout)
    msg = await channel.send(embed=embed, view=view)

    result = await view.wait_for_result()

    if result is None:
        # Timeout - edit message
        for item in view.children:
            item.disabled = True
        try:
            await msg.edit(content="\u23f0 **Timed out** \u2014 action cancelled.", view=view)
        except Exception:
            pass

    return result is True

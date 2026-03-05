"""AI Agent cog - handles message processing, tool-calling loop,
streaming responses, attachment processing, parallel tool execution,
permission checking, and confirmation UI."""
import asyncio
import base64
import json
import logging
import os
import re
import traceback
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands
from openai import AsyncOpenAI

from tools import get_tool_specs, get_tool_executor
from tools_permissions import (
    check_permission,
    needs_confirmation,
    request_confirmation,
)
from context_manager import maybe_compress_history

log = logging.getLogger("agent")

DEFAULT_MODEL = "google/gemini-2.5-flash"
MAX_TOOL_ROUNDS = 10
API_MAX_RETRIES = 3
API_RETRY_DELAYS = [2, 5, 15]  # seconds between retries
STREAMING_EDIT_INTERVAL = 1.0  # seconds between message edits while streaming

# File extensions we can read as text
TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".json", ".md", ".csv", ".xml",
    ".html", ".css", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sh", ".bash", ".log", ".sql", ".rs", ".go", ".java", ".c",
    ".cpp", ".h", ".rb", ".php", ".swift", ".kt", ".r", ".lua",
}

# Image MIME types for Vision API
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

SYSTEM_PROMPT_TEMPLATE = """You are an AI assistant that helps manage the Discord server "{server_name}".
You are a Discord specialist with full server administration capabilities.
You have long-term memory and can search the server's entire message history.

Server context:
- Server: {server_name} ({member_count} members)
- Current channel: #{channel_name}
- Requested by: {user_name}

Capabilities:
- View server info, channels, roles, members
- Read messages from channels
- **Search the entire server message history** (use search_server_messages)
- Create/edit/delete channels and categories
- Create/assign/remove roles
- Send messages to channels
- Pin/delete messages
- Kick/ban/timeout members (requires permission + confirmation)
- **Long-term memory**: remember/recall important information across conversations
- **Scheduled tasks**: create recurring automated tasks
- **Web search**: search the internet and read web pages
- **Screenshots**: capture webpage screenshots
- **GitHub**: manage repos, issues, PRs via gh CLI
- **Shell commands**: run system commands on the host
- **MCP tools**: additional capabilities from connected MCP servers
- **Attachments**: can read images (vision), text files, and audio (transcription)

Memory guidelines:
- Proactively use 'remember' to save important information (server rules, user preferences, decisions, recurring topics).
- Use 'recall' at the start of complex requests to check if you have relevant stored knowledge.
- Categories: server_rules, user_preferences, decisions, facts, todo, project_info

Rules:
1. Always explain what you're about to do before taking destructive actions (delete, kick, ban).
2. Use tools to gather information before making changes.
3. Respond in the same language the user uses.
4. Be concise but informative.
5. If a request is ambiguous, ask for clarification.
6. Report results after each action.
7. When asked about past events, search the message archive first.
8. Proactively remember important context from conversations.
9. For complex tasks, plan your approach first, then execute step by step.
10. Destructive actions (kick, ban, delete) require the user to have Discord permissions and will trigger a confirmation button.

{memories_context}
"""


# ── Retry helper ──────────────────────────────────────────────────────────

def _is_retryable_api_error(exc: Exception) -> bool:
    """Check if an LLM API error is worth retrying."""
    err_str = str(exc).lower()
    retryable_keywords = ["429", "rate limit", "timeout", "502", "503", "504",
                          "connection", "unavailable", "overloaded"]
    return any(kw in err_str for kw in retryable_keywords)


# ── Attachment processing ─────────────────────────────────────────────────

async def process_attachments(message: discord.Message) -> list[dict]:
    """Process message attachments into content parts for the LLM."""
    parts = []
    for att in message.attachments:
        try:
            if att.content_type and att.content_type.split(";")[0] in IMAGE_MIMES:
                # Image → vision content part
                data = await att.read()
                b64 = base64.b64encode(data).decode("utf-8")
                mime = att.content_type.split(";")[0]
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                    },
                })
                log.info(f"Processed image attachment: {att.filename} ({len(data)} bytes)")

            elif att.content_type and att.content_type.startswith("audio/"):
                # Audio → note for now (Whisper would need API key)
                parts.append({
                    "type": "text",
                    "text": f"[Audio attachment: {att.filename}, {att.size} bytes — audio transcription not yet available]",
                })

            elif any(att.filename.lower().endswith(ext) for ext in TEXT_EXTENSIONS):
                # Text file → read content
                data = await att.read()
                text = data.decode("utf-8", errors="replace")
                if len(text) > 8000:
                    text = text[:8000] + f"\n... (truncated, {len(data)} bytes total)"
                parts.append({
                    "type": "text",
                    "text": f"[File: {att.filename}]\n```\n{text}\n```",
                })
                log.info(f"Processed text attachment: {att.filename} ({len(data)} bytes)")

            else:
                # Unknown type
                parts.append({
                    "type": "text",
                    "text": f"[Attachment: {att.filename}, {att.content_type or 'unknown type'}, {att.size} bytes]",
                })
        except Exception as e:
            log.error(f"Failed to process attachment {att.filename}: {e}")
            parts.append({
                "type": "text",
                "text": f"[Failed to read attachment: {att.filename} — {e}]",
            })
    return parts


class AgentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        log.info(f"Using model: {self.model}")

    # ── Keyword extraction ────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> str:
        """Extract meaningful keywords from user message for memory search."""
        text = re.sub(r'<@!?\d+>', '', text)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        stop = {'the','a','an','is','are','was','were','be','been','being','have','has','had',
                'do','does','did','will','would','could','should','may','might','shall','can',
                'to','of','in','for','on','with','at','by','from','as','into','about','between',
                'through','after','before','during','it','its','this','that','these','those',
                'i','me','my','we','our','you','your','he','she','they','them','his','her',
                'what','which','who','whom','how','when','where','why','not','no','yes',
                'and','or','but','if','then','so','just','also','very','really','please'}
        words = [w for w in text.lower().split() if len(w) > 2 and w not in stop]
        return ' '.join(words[:15])

    # ── System prompt builder ─────────────────────────────────────────────

    async def _build_system_prompt(self, message, user_input: str = "") -> str:
        memories_context = ""
        if hasattr(message, "guild") and message.guild:
            guild_id = str(message.guild.id)
            seen_ids = set()
            all_memories = []

            keywords = self._extract_keywords(user_input)
            if keywords:
                relevant = await self.bot.db.recall_relevant(guild_id, keywords, limit=5)
                for m in relevant:
                    if m['id'] not in seen_ids:
                        seen_ids.add(m['id'])
                        all_memories.append(m)

            recent = await self.bot.db.recall(guild_id, limit=5)
            for m in recent:
                if m['id'] not in seen_ids:
                    seen_ids.add(m['id'])
                    all_memories.append(m)

            all_memories = all_memories[:10]
            if all_memories:
                lines = ["Relevant memories:"]
                for m in all_memories:
                    imp = f" [importance:{m.get('importance', 5)}]" if m.get('importance', 5) != 5 else ""
                    lines.append(f"- [{m['category']}] {m['key']}: {m['content'][:300]}{imp}")
                memories_context = "\n".join(lines)

        return SYSTEM_PROMPT_TEMPLATE.format(
            server_name=message.guild.name if message.guild else "DM",
            member_count=message.guild.member_count if message.guild else 1,
            channel_name=message.channel.name if hasattr(message.channel, "name") else "DM",
            user_name=message.author.display_name,
            memories_context=memories_context,
        )

    # ── LLM call with retry ───────────────────────────────────────────────

    async def _call_llm(self, messages: list[dict], tool_specs: list[dict]) -> object:
        """Call LLM API with exponential backoff retry on transient errors."""
        last_error = None
        for attempt in range(API_MAX_RETRIES):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tool_specs if tool_specs else None,
                    max_tokens=4096,
                )
                return response
            except Exception as e:
                last_error = e
                if attempt < API_MAX_RETRIES - 1 and _is_retryable_api_error(e):
                    delay = API_RETRY_DELAYS[min(attempt, len(API_RETRY_DELAYS) - 1)]
                    log.warning(f"LLM API error (attempt {attempt+1}/{API_MAX_RETRIES}), "
                                f"retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                else:
                    raise
        raise last_error  # unreachable, but satisfies type checker

    # ── Parallel tool execution ───────────────────────────────────────────

    async def _execute_tools_parallel(
        self,
        tool_calls: list,
        guild: discord.Guild,
        channel_id: str,
        user_id: str,
        user_name: str,
        channel: discord.TextChannel,
    ) -> list[dict]:
        """Execute multiple tool calls in parallel where safe.

        Permission checks and confirmation UI run before execution.
        Returns list of {tool_call_id, content} dicts.
        """
        async def _run_one(tc) -> dict:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            # ── Permission check ──────────────────────────────────────
            if guild:
                allowed, reason = check_permission(guild, user_id, tool_name)
                if not allowed:
                    return {"tool_call_id": tc.id, "content": f"⛔ Permission denied: {reason}"}

            # ── Confirmation for destructive ops ──────────────────────
            if guild and needs_confirmation(tool_name):
                try:
                    confirmed = await request_confirmation(
                        channel, int(user_id), tool_name, tool_args, timeout=60.0,
                    )
                    if not confirmed:
                        return {"tool_call_id": tc.id, "content": "❌ Action cancelled by user."}
                except Exception as e:
                    log.warning(f"Confirmation UI failed: {e}, proceeding with caution")

            # ── Execute ───────────────────────────────────────────────
            if hasattr(self.bot, 'mcp') and self.bot.mcp.is_mcp_tool(tool_name):
                try:
                    log.info(f"Executing MCP tool: {tool_name}({tool_args})")
                    result = await self.bot.mcp.call_tool(tool_name, tool_args)
                except Exception as e:
                    result = f"MCP tool error: {e}"
                    log.error(f"MCP tool error: {tool_name}: {traceback.format_exc()}")
            else:
                executor = get_tool_executor(tool_name)
                if not executor:
                    result = f"Unknown tool: {tool_name}"
                else:
                    try:
                        log.info(f"Executing tool: {tool_name}({tool_args})")
                        result = await executor(
                            guild,
                            db=self.bot.db,
                            channel_id=channel_id,
                            user_name=user_name,
                            **tool_args,
                        )
                    except Exception as e:
                        result = f"Error executing {tool_name}: {e}"
                        log.error(f"Tool error: {tool_name}: {traceback.format_exc()}")

            # ── Audit log ─────────────────────────────────────────────
            if guild:
                await self.bot.db.log_tool_use(
                    str(guild.id), user_id, tool_name, tool_args, str(result)[:1000],
                )

            return {"tool_call_id": tc.id, "content": str(result)}

        # Run all tool calls concurrently
        # But serialize confirmation-required tools (user can only click one at a time)
        confirm_tcs = [tc for tc in tool_calls if needs_confirmation(tc.function.name)]
        parallel_tcs = [tc for tc in tool_calls if not needs_confirmation(tc.function.name)]

        results = []

        # Parallel batch first
        if parallel_tcs:
            parallel_results = await asyncio.gather(
                *[_run_one(tc) for tc in parallel_tcs],
                return_exceptions=True,
            )
            for tc, res in zip(parallel_tcs, parallel_results):
                if isinstance(res, Exception):
                    results.append({"tool_call_id": tc.id, "content": f"Error: {res}"})
                else:
                    results.append(res)

        # Sequential confirmation tools
        for tc in confirm_tcs:
            res = await _run_one(tc)
            results.append(res)

        # Re-order to match original tool_calls order
        result_map = {r["tool_call_id"]: r["content"] for r in results}
        ordered = []
        for tc in tool_calls:
            ordered.append({
                "tool_call_id": tc.id,
                "content": result_map.get(tc.id, "Error: result missing"),
            })
        return ordered

    # ── Main agent loop ───────────────────────────────────────────────────

    async def _run_agent(
        self,
        message,
        user_input: str,
        attachment_parts: list[dict] | None = None,
    ) -> str:
        """Run the agent loop: call LLM, execute tools, repeat until text response."""
        guild = message.guild
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)
        user_name = message.author.display_name

        # Build messages from history
        history = await self.bot.db.get_history(channel_id, limit=30)
        system_prompt = await self._build_system_prompt(message, user_input)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        # Build user message (may include attachments)
        if attachment_parts:
            user_content = [{"type": "text", "text": user_input}] + attachment_parts
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_input})

        # Save user message (text only for DB)
        await self.bot.db.add_message(channel_id, "user", user_input)

        # Compress if conversation is long
        messages = await maybe_compress_history(self.client, self.model, messages)

        tool_specs = get_tool_specs()
        if hasattr(self.bot, 'mcp'):
            tool_specs = tool_specs + self.bot.mcp.get_tool_specs()

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self._call_llm(messages, tool_specs)
            except Exception as e:
                log.error(f"LLM API error after retries: {e}")
                return f"❌ API error: {e}"

            choice = response.choices[0]
            assistant_msg = choice.message

            # Build the assistant message dict for history
            assistant_dict = {"role": "assistant", "content": assistant_msg.content or ""}
            if assistant_msg.tool_calls:
                assistant_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_msg.tool_calls
                ]
            messages.append(assistant_dict)

            # If no tool calls, we're done
            if not assistant_msg.tool_calls:
                final_text = assistant_msg.content or "(No response)"
                await self.bot.db.add_message(channel_id, "assistant", final_text)
                return final_text

            # Save assistant message with tool calls
            await self.bot.db.add_message(
                channel_id,
                "assistant",
                assistant_msg.content or "",
                tool_calls=assistant_dict.get("tool_calls"),
            )

            # Execute tools (parallel where safe)
            tool_results = await self._execute_tools_parallel(
                assistant_msg.tool_calls,
                guild, channel_id, user_id, user_name, message.channel,
            )

            for tr in tool_results:
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                }
                messages.append(tool_msg)
                await self.bot.db.add_message(
                    channel_id, "tool", tr["content"][:1000], tool_call_id=tr["tool_call_id"],
                )

        return "⚠️ Max tool rounds reached. The operation may be incomplete."

    # ── Streaming response helper ─────────────────────────────────────────

    async def _stream_response(
        self,
        channel: discord.TextChannel,
        reply_to: discord.Message | None,
        agent_response: str,
    ):
        """Send a long response with progressive editing for a streaming feel.

        For responses longer than 2000 chars, sends in chunks.
        For shorter responses, sends directly.
        """
        if len(agent_response) <= 2000:
            if reply_to:
                await reply_to.reply(agent_response)
            else:
                await channel.send(agent_response)
            return

        # Split and send chunks
        chunks = self._split_text(agent_response)
        for i, chunk in enumerate(chunks):
            if i == 0 and reply_to:
                await reply_to.reply(chunk)
            else:
                await channel.send(chunk)

    # ── Message listener ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not self.bot.user.mentioned_in(message):
            return
        if message.mention_everyone:
            return

        content = message.content
        for mention in message.mentions:
            content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        content = content.strip()

        if not content and not message.attachments:
            content = "Hello!"

        # Process attachments
        attachment_parts = []
        if message.attachments:
            attachment_parts = await process_attachments(message)
            if not content:
                content = "Please analyze the attached file(s)."

        async with message.channel.typing():
            response = await self._run_agent(message, content, attachment_parts or None)

        await self._stream_response(message.channel, message, response)

    # ── Slash commands ────────────────────────────────────────────────────

    @app_commands.command(name="ask", description="Ask the AI agent a question")
    @app_commands.describe(
        prompt="Your question or request",
        image="Attach an image for the AI to analyze",
    )
    async def ask_slash(
        self,
        interaction: discord.Interaction,
        prompt: str,
        image: discord.Attachment = None,
    ):
        await interaction.response.defer(thinking=True)

        message_proxy = type("MessageProxy", (), {
            "guild": interaction.guild,
            "channel": interaction.channel,
            "author": interaction.user,
        })()

        # Process optional image attachment
        attachment_parts = []
        if image and image.content_type and image.content_type.split(";")[0] in IMAGE_MIMES:
            try:
                data = await image.read()
                b64 = base64.b64encode(data).decode("utf-8")
                mime = image.content_type.split(";")[0]
                attachment_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            except Exception as e:
                log.error(f"Failed to process slash command attachment: {e}")

        response = await self._run_agent(
            message_proxy, prompt, attachment_parts or None,
        )

        if len(response) <= 2000:
            await interaction.followup.send(response)
        else:
            chunks = self._split_text(response)
            for chunk in chunks:
                await interaction.followup.send(chunk)

    @app_commands.command(name="clear", description="Clear conversation history for this channel")
    async def clear_slash(self, interaction: discord.Interaction):
        await self.bot.db.clear_history(str(interaction.channel.id))
        await interaction.response.send_message("🗑️ Conversation history cleared.", ephemeral=True)

    @app_commands.command(name="model", description="Show or change the AI model")
    @app_commands.describe(name="Model name (e.g. google/gemini-2.5-flash, openai/gpt-4o-mini)")
    async def model_slash(self, interaction: discord.Interaction, name: str = None):
        if name:
            self.model = name
            await interaction.response.send_message(f"🤖 Model changed to `{name}`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"🤖 Current model: `{self.model}`", ephemeral=True)

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _send_response(self, message: discord.Message, response: str):
        await self._stream_response(message.channel, message, response)

    @staticmethod
    def _split_text(text: str, max_len: int = 2000) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1 or split_at < max_len // 2:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks


async def setup(bot: commands.Bot):
    await bot.add_cog(AgentCog(bot))

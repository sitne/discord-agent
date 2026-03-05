"""AI Agent cog - handles message processing and tool-calling loop."""
import json
import logging
import os
import re
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from openai import AsyncOpenAI

from tools import get_tool_specs, get_tool_executor

log = logging.getLogger("agent")

DEFAULT_MODEL = "google/gemini-2.5-flash"
MAX_TOOL_ROUNDS = 10

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
- Kick/ban/timeout members
- **Long-term memory**: remember/recall important information across conversations
- **Scheduled tasks**: create recurring automated tasks
- **Web search**: search the internet and read web pages
- **Screenshots**: capture webpage screenshots
- **GitHub**: manage repos, issues, PRs via gh CLI
- **Shell commands**: run system commands on the host
- **MCP tools**: additional capabilities from connected MCP servers

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

{memories_context}
"""


class AgentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        log.info(f"Using model: {self.model}")

    @staticmethod
    def _extract_keywords(text: str) -> str:
        """Extract meaningful keywords from user message for memory search."""
        # Remove mentions, URLs, and special chars
        text = re.sub(r'<@!?\d+>', '', text)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        # Filter out very short / stop words
        stop = {'the','a','an','is','are','was','were','be','been','being','have','has','had',
                'do','does','did','will','would','could','should','may','might','shall','can',
                'to','of','in','for','on','with','at','by','from','as','into','about','between',
                'through','after','before','during','it','its','this','that','these','those',
                'i','me','my','we','our','you','your','he','she','they','them','his','her',
                'what','which','who','whom','how','when','where','why','not','no','yes',
                'and','or','but','if','then','so','just','also','very','really','please'}
        words = [w for w in text.lower().split() if len(w) > 2 and w not in stop]
        return ' '.join(words[:15])  # cap at 15 keywords

    async def _build_system_prompt(self, message, user_input: str = "") -> str:
        memories_context = ""
        if hasattr(message, "guild") and message.guild:
            guild_id = str(message.guild.id)
            seen_ids = set()
            all_memories = []

            # 1) Get memories relevant to the user's message via FTS
            keywords = self._extract_keywords(user_input)
            if keywords:
                relevant = await self.bot.db.recall_relevant(guild_id, keywords, limit=5)
                for m in relevant:
                    if m['id'] not in seen_ids:
                        seen_ids.add(m['id'])
                        all_memories.append(m)

            # 2) Get most recent memories
            recent = await self.bot.db.recall(guild_id, limit=5)
            for m in recent:
                if m['id'] not in seen_ids:
                    seen_ids.add(m['id'])
                    all_memories.append(m)

            # Cap at 10 total
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

    async def _run_agent(self, message: discord.Message, user_input: str) -> str:
        """Run the agent loop: call LLM, execute tools, repeat until text response."""
        guild = message.guild
        channel_id = str(message.channel.id)

        # Build messages from history
        history = await self.bot.db.get_history(channel_id, limit=30)
        system_prompt = await self._build_system_prompt(message, user_input)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        # Save user message
        await self.bot.db.add_message(channel_id, "user", user_input)

        tool_specs = get_tool_specs()
        # Add MCP tools
        if hasattr(self.bot, 'mcp'):
            tool_specs = tool_specs + self.bot.mcp.get_tool_specs()

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tool_specs if tool_specs else None,
                    max_tokens=4096,
                )
            except Exception as e:
                log.error(f"LLM API error: {e}")
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

            # Execute each tool call
            for tc in assistant_msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                # Route to MCP or built-in tool
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
                                user_name=message.author.display_name,
                                **tool_args,
                            )
                        except Exception as e:
                            result = f"Error executing {tool_name}: {e}"
                            log.error(f"Tool error: {tool_name}: {traceback.format_exc()}")

                # Log tool use
                if guild:
                    await self.bot.db.log_tool_use(
                        str(guild.id),
                        str(message.author.id),
                        tool_name,
                        tool_args,
                        str(result)[:1000],
                    )

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                }
                messages.append(tool_msg)
                await self.bot.db.add_message(
                    channel_id, "tool", str(result)[:1000], tool_call_id=tc.id
                )

        return "⚠️ Max tool rounds reached. The operation may be incomplete."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots and self
        if message.author.bot:
            return

        # Only respond to @mentions
        if not self.bot.user.mentioned_in(message):
            return

        # Ignore @everyone/@here
        if message.mention_everyone:
            return

        # Strip the mention from content
        content = message.content
        for mention in message.mentions:
            content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        content = content.strip()

        if not content:
            content = "Hello!"

        async with message.channel.typing():
            response = await self._run_agent(message, content)

        # Send response, splitting if needed
        await self._send_response(message, response)

    @app_commands.command(name="ask", description="Ask the AI agent a question")
    @app_commands.describe(prompt="Your question or request")
    async def ask_slash(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer(thinking=True)

        # Create a fake-ish message context for the agent
        # We'll use the interaction's channel and user
        message = interaction.message or interaction
        message_proxy = type("MessageProxy", (), {
            "guild": interaction.guild,
            "channel": interaction.channel,
            "author": interaction.user,
        })()

        response = await self._run_agent(message_proxy, prompt)

        # Split response for followup
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

    async def _send_response(self, message: discord.Message, response: str):
        if len(response) <= 2000:
            await message.reply(response)
        else:
            chunks = self._split_text(response)
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await message.reply(chunk)
                else:
                    await message.channel.send(chunk)

    @staticmethod
    def _split_text(text: str, max_len: int = 2000) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            # Try to split at newline
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1 or split_at < max_len // 2:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks


async def setup(bot: commands.Bot):
    await bot.add_cog(AgentCog(bot))

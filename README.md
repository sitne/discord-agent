# 🤖 discord-agent

**[日本語](README.ja.md)**

A Discord bot that acts as an autonomous AI agent with 52 tools, long-term memory, a cron-based task scheduler, web capabilities, and code generation with GitHub CI. Powered by any LLM on [OpenRouter](https://openrouter.ai) (default: Gemini 2.5 Flash).

~4,600 lines of Python across 13 files. Built with [discord.py](https://github.com/Rapptz/discord.py).

## Features

- **LLM-powered agent** — Routes through OpenRouter; switch models at runtime with `/model`
- **52 tools** across 6 categories (Discord, Memory, Scheduler, Web, System, CodeGen)
- **Long-term memory** — SQLite + FTS5 hybrid search with automatic context injection
- **Autonomous scheduler** — Cron expressions, retry logic, dead-letter queue, execution history
- **Web access** — Search, news, page reading (3-tier extraction + caching), and screenshots via Playwright
- **Conversation compression** — Automatic summarization when context exceeds 20 messages
- **Safety first** — Permission checks and confirmation buttons for destructive actions
- **Robust I/O** — API retry with exponential backoff, parallel tool execution, image/text attachment processing
- **MCP support** — Connect external tool servers via `mcp_servers.json`

## Tools

| Category | Count | Tools |
|---|---|---|
| **Discord** | 18 | `send_message` `edit_message` `delete_messages` `create_channel` `delete_channel` `rename_channel` `set_channel_topic` `list_channels` `list_members` `get_member_info` `set_nickname` `create_role` `assign_role` `remove_role` `kick_member` `ban_member` `unban_member` `get_server_info` |
| **Memory** | 5 | `remember` `recall` `forget` `forget_by_key` `list_memory_categories` |
| **Scheduler** | 5 | `create_scheduled_task` `list_scheduled_tasks` `delete_scheduled_task` `toggle_scheduled_task` `get_task_history` |
| **Web** | 4 | `web_search` `web_news` `read_webpage` `screenshot_webpage` |
| **System** | 11 | `run_shell` + 10 GitHub CLI tools |
| **CodeGen** | 6 | `codegen_create_project` `codegen_update_files` `codegen_check_ci` `codegen_list_projects` `codegen_read_file` `codegen_delete_file` |

## Setup

**Requirements:** Python 3.12+

1. **Clone and install dependencies**

   ```bash
   git clone https://github.com/sitne/discord-agent.git
   cd discord-agent
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Configure environment**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set:

   ```env
   DISCORD_TOKEN=your-bot-token
   OPENROUTER_API_KEY=your-api-key

   # Optional: override the default model
   # OPENROUTER_MODEL=openai/gpt-4o-mini
   ```

3. **Discord bot setup**

   Create a bot at the [Discord Developer Portal](https://discord.com/developers/applications) with the following **privileged intents** enabled:
   - Message Content
   - Server Members

   Invite the bot with permissions for managing channels, roles, messages, and members.

4. **Run**

   ```bash
   python bot.py
   ```

## Commands

| Command | Description |
|---|---|
| `/ask` | Send a prompt to the agent (supports an optional image attachment) |
| `/model` | View or switch the active LLM (e.g. `google/gemini-2.5-flash`, `openai/gpt-4o-mini`) |

## 📂 Project Structure

```
bot.py                  Entry point and bot initialization
db.py                   SQLite database, memory store, FTS5 search
tools.py                Discord management tools (18)
tools_web.py            Web search, reading, and screenshots
tools_system.py         Shell execution and GitHub CLI tools
tools_codegen.py        Code generation, GitHub project management, CI
tools_permissions.py    Permission checks and confirmation gates
context_manager.py      Conversation history and compression
cron_parser.py          Cron expression parser
mcp_manager.py          MCP server lifecycle and tool routing
cogs/
  agent.py              Core agent loop, LLM calls, tool dispatch
  collector.py          Message and attachment collection
  scheduler.py          Cron scheduler, retry, DLQ, history
```

## MCP Servers

To connect external [Model Context Protocol](https://modelcontextprotocol.io/) tool servers, create a `mcp_servers.json` in the project root:

```json
{
  "servers": [
    {
      "name": "example",
      "command": "npx",
      "args": ["-y", "@example/mcp-server"]
    }
  ]
}
```

MCP tools are automatically discovered and made available to the agent alongside built-in tools.

## Dependencies

- [discord.py](https://github.com/Rapptz/discord.py) — Discord API
- [openai](https://github.com/openai/openai-python) — OpenRouter-compatible LLM client
- [aiosqlite](https://github.com/omnilib/aiosqlite) — Async SQLite + FTS5
- [ddgs](https://github.com/deedy5/duckduckgo_search) — Web search
- [trafilatura](https://github.com/adbar/trafilatura) + [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) — Content extraction
- [playwright](https://playwright.dev/python/) — Page screenshots
- [mcp](https://github.com/modelcontextprotocol/python-sdk) — MCP client

## License

MIT

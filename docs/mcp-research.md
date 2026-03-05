# MCP (Model Context Protocol) Research Summary

## What is MCP?

MCP (Model Context Protocol) is an **open-source standard** (by Anthropic / LF Projects) for connecting AI applications to external systems. Think of it as a **USB-C port for AI** — a standardized way to connect AI applications to data sources, tools, and workflows.

- **Spec**: https://modelcontextprotocol.io
- **Python SDK**: `pip install "mcp[cli]"` (package: `mcp`, repo: github.com/modelcontextprotocol/python-sdk)
- **Current stable**: v1.x

---

## Architecture: Host → Client → Server

MCP follows a **client-host-server** architecture:

```
┌─────────────────────────────────────────┐
│  HOST (AI Application / Bot)            │
│                                         │
│  ┌──────────┐  ┌──────────┐  ┌────────┐│
│  │ Client 1 │  │ Client 2 │  │Client 3││
│  └────┬─────┘  └────┬─────┘  └───┬────┘│
└───────┼─────────────┼────────────┼──────┘
        │             │            │
   ┌────▼────┐  ┌─────▼───┐  ┌────▼────┐
   │Server 1 │  │Server 2 │  │Server 3 │
   │(Files)  │  │(GitHub) │  │(Search) │
   └─────────┘  └─────────┘  └─────────┘
```

### Roles:
- **Host**: The AI application (our Discord bot). Creates and manages multiple MCP clients. Handles LLM integration, security, context aggregation.
- **Client**: One per server connection. Maintains a 1:1 stateful session with a server. Handles protocol negotiation and capability exchange.
- **Server**: Provides tools, resources, and prompts via MCP primitives. Can be local processes (stdio) or remote services (HTTP).

### Key Design Principles:
- Servers are easy to build and highly composable
- Servers are isolated — they can't see the full conversation or other servers
- The host controls everything — security, context routing, tool permissions
- Features are negotiated via capabilities during initialization

---

## Protocol Basics (JSON-RPC 2.0)

MCP uses JSON-RPC 2.0 over two transport types:

### Transports:
1. **stdio** — For local servers. The client spawns the server as a subprocess and communicates via stdin/stdout.
2. **Streamable HTTP** (recommended for production) — Server runs as an HTTP endpoint. Client connects via HTTP POST.
3. **SSE** (legacy, being superseded by Streamable HTTP)

### Lifecycle:
1. Client sends `initialize` with its capabilities
2. Server responds with its capabilities
3. Active session begins
4. Client can call `tools/list`, `tools/call`, `resources/list`, etc.
5. Server can send notifications (e.g., `notifications/tools/list_changed`)

---

## Three Core Primitives

| Primitive | Control | Description |
|-----------|---------|-------------|
| **Tools** | Model-controlled | Functions the LLM can invoke (like POST endpoints). Side effects allowed. |
| **Resources** | Application-controlled | Data the app can read (like GET endpoints). No side effects. |
| **Prompts** | User-controlled | Reusable templates for LLM interactions. |

For our bot, **Tools** are the most relevant — the LLM decides when to call them.

---

## Tool Discovery & Invocation

### Discovery (`tools/list`):
```json
// Response
{
  "tools": [
    {
      "name": "get_weather",
      "description": "Get current weather information for a location",
      "inputSchema": {
        "type": "object",
        "properties": {
          "location": { "type": "string", "description": "City name or zip code" }
        },
        "required": ["location"]
      }
    }
  ]
}
```

### Invocation (`tools/call`):
```json
// Request
{"method": "tools/call", "params": {"name": "get_weather", "arguments": {"location": "New York"}}}

// Response  
{"result": {"content": [{"type": "text", "text": "Temperature: 72°F, Partly cloudy"}], "isError": false}}
```

Tools return content blocks: text, images, audio, resource links, or embedded resources.

---

## Python SDK: Client Usage

### Installation:
```bash
pip install "mcp[cli]"
# or
uv add "mcp[cli]"
```

### Key imports:
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
```

### Connecting to a LOCAL server (stdio):
```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(
    command="python",          # or "node", "npx", "uvx", etc.
    args=["server_script.py"],
    env=None                   # optional env vars
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        
        # Discover tools
        tools_result = await session.list_tools()
        for tool in tools_result.tools:
            print(f"{tool.name}: {tool.description}")
            print(f"  Schema: {tool.inputSchema}")
        
        # Call a tool
        result = await session.call_tool("get_weather", {"location": "NYC"})
        for content in result.content:
            if isinstance(content, types.TextContent):
                print(content.text)
```

### Connecting to a REMOTE server (Streamable HTTP):
```python
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async with streamable_http_client("http://localhost:8000/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        # ... use tools
```

### Full integration pattern (bot as MCP client + LLM):
```python
class MCPClient:
    def __init__(self):
        self.session = None
        self.exit_stack = AsyncExitStack()
    
    async def connect_to_server(self, command, args):
        server_params = StdioServerParameters(command=command, args=args)
        transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        read, write = transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()
    
    async def get_tools_for_llm(self):
        """Convert MCP tools to LLM-compatible tool definitions."""
        response = await self.session.list_tools()
        return [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in response.tools]
    
    async def call_tool(self, name, arguments):
        """Execute a tool call from the LLM."""
        result = await self.session.call_tool(name, arguments)
        return result
```

---

## Integration Pattern for Our Discord Bot

The bot acts as an **MCP Host**:

```
Discord User → Bot (Host) → LLM (Anthropic/OpenAI)
                  ↓
           MCP Client 1 → Filesystem Server
           MCP Client 2 → GitHub Server  
           MCP Client 3 → Web Search Server
           MCP Client 4 → Custom Tool Server
```

### Flow:
1. **Startup**: Bot connects to configured MCP servers, creates one `ClientSession` per server
2. **Tool Discovery**: Bot calls `session.list_tools()` on each server, aggregates all tools
3. **User Message**: User sends a message in Discord
4. **LLM Call**: Bot sends message to LLM along with ALL discovered tool definitions
5. **Tool Execution**: If LLM requests a tool call, bot routes it to the correct MCP server's `session.call_tool()`
6. **Result**: Tool result is sent back to LLM for final response
7. **Reply**: Bot sends LLM's response to Discord

### Multi-server management:
```python
class MCPManager:
    def __init__(self):
        self.sessions: dict[str, ClientSession] = {}  # server_name -> session
        self.tool_map: dict[str, str] = {}  # tool_name -> server_name
    
    async def connect_servers(self, server_configs):
        for name, config in server_configs.items():
            # Connect to each server
            session = ...  # create session
            self.sessions[name] = session
            
            # Map tools to their server
            tools = await session.list_tools()
            for tool in tools.tools:
                self.tool_map[tool.name] = name
    
    async def get_all_tools(self):
        """Aggregate tools from all servers for the LLM."""
        all_tools = []
        for session in self.sessions.values():
            response = await session.list_tools()
            all_tools.extend(response.tools)
        return all_tools
    
    async def call_tool(self, tool_name, arguments):
        """Route tool call to correct server."""
        server_name = self.tool_map[tool_name]
        session = self.sessions[server_name]
        return await session.call_tool(tool_name, arguments)
```

---

## Available MCP Servers (Notable)

### Official Reference Servers (github.com/modelcontextprotocol/servers):
| Server | Description |
|--------|-------------|
| **Filesystem** | Secure file operations with configurable access controls |
| **Fetch** | Web content fetching and conversion for LLM usage |
| **Git** | Read, search, manipulate Git repositories |
| **Memory** | Knowledge graph-based persistent memory |
| **Sequential Thinking** | Dynamic problem-solving through thought sequences |
| **Time** | Time and timezone conversion |
| **Everything** | Reference/test server with all features |

### Major Third-Party Servers (from MCP Registry):
- **Brave Search** — Web search
- **GitHub** — Repository management, PRs, issues
- **Google Drive** — File access and search
- **Google Maps** — Location services
- **PostgreSQL** / **SQLite** — Database access
- **Puppeteer** / **Browserbase** — Browser automation
- **Slack** — Messaging
- **Sentry** — Error tracking
- **Docker** — Container management
- **Stripe** / **Alpaca** — Payments / trading
- **1000+ more** on the MCP Registry

### Running servers:
Most servers are npm packages or Python scripts:
```bash
# Node.js server via npx
npx -y @modelcontextprotocol/server-filesystem /path/to/allowed/dir

# Python server via uvx  
uvx mcp-server-git

# Or connect to a remote server URL
http://some-server.example.com/mcp
```

---

## Key Considerations for Our Bot

### Pros of MCP Integration:
1. **Instant access to 1000s of tools** without building them ourselves
2. **Standardized protocol** — tool discovery, invocation, error handling all handled
3. **Composability** — add/remove servers without changing bot code
4. **Community ecosystem** — growing rapidly
5. **Python SDK is mature** — async, well-typed, supports all transports

### Challenges:
1. **Tool name conflicts** — Multiple servers may expose tools with the same name. Need namespacing strategy.
2. **Too many tools** — Feeding hundreds of tools to an LLM degrades performance. Need tool selection/filtering.
3. **Stdio servers are subprocesses** — Each stdio server is a child process. Resource management needed.
4. **Session lifecycle** — Sessions are stateful. Need reconnection logic.
5. **Security** — MCP servers can execute arbitrary code. Need sandboxing for untrusted servers.

### Recommended Approach:
1. Start with a few high-value servers (filesystem, fetch/web, maybe GitHub)
2. Use the `AsyncExitStack` pattern for session management
3. Build a tool routing layer that maps tool names to server sessions
4. Consider tool namespacing (prefix tool names with server name)
5. Implement tool filtering per conversation context
6. Support both stdio (local) and HTTP (remote) transports

---

## MCP Tool Schema → LLM Tool Format

MCP tool schemas are already in JSON Schema format, which maps directly to both Anthropic and OpenAI tool formats:

```python
# MCP tool
tool.name         # "get_weather"
tool.description  # "Get current weather..."
tool.inputSchema  # {"type": "object", "properties": {...}, "required": [...]}

# Anthropic format (direct mapping)
{"name": tool.name, "description": tool.description, "input_schema": tool.inputSchema}

# OpenAI format
{"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.inputSchema}}
```

This makes MCP tools trivially convertible to any LLM provider's tool calling format.

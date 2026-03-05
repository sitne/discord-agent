"""MCP (Model Context Protocol) client manager.

Connects to MCP servers as a client, discovers their tools,
and makes them available to the AI agent.

Config file: mcp_servers.json
"""
import asyncio
import json
import logging
import os
from typing import Any, Optional
from contextlib import AsyncExitStack

log = logging.getLogger("mcp")

CONFIG_PATH = "mcp_servers.json"


class MCPManager:
    def __init__(self):
        self.sessions: dict[str, Any] = {}  # server_name -> session
        self.tool_map: dict[str, str] = {}  # tool_name -> server_name
        self.tool_specs: list[dict] = []     # OpenAI-format tool specs
        self._exit_stack = AsyncExitStack()
        self._transports: dict[str, tuple] = {}  # name -> (read, write)

    async def start(self):
        """Load config and connect to all configured MCP servers."""
        if not os.path.exists(CONFIG_PATH):
            log.info("No mcp_servers.json found, skipping MCP setup")
            return

        with open(CONFIG_PATH) as f:
            config = json.load(f)

        for name, server_config in config.get("servers", {}).items():
            try:
                await self._connect_server(name, server_config)
            except Exception as e:
                log.error(f"Failed to connect MCP server '{name}': {e}")

        log.info(f"MCP: {len(self.sessions)} servers connected, {len(self.tool_map)} tools available")

    async def _connect_server(self, name: str, config: dict):
        """Connect to a single MCP server."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        transport = config.get("transport", "stdio")

        if transport == "stdio":
            command = config["command"]
            args = config.get("args", [])
            env_vars = config.get("env", {})

            # Merge with current env
            env = {**os.environ, **env_vars}

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
            )

            transport_ctx = stdio_client(server_params)
            read, write = await self._exit_stack.enter_async_context(transport_ctx)

        elif transport == "http":
            from mcp.client.streamable_http import streamable_http_client
            url = config["url"]
            transport_ctx = streamable_http_client(url)
            read, write, _ = await self._exit_stack.enter_async_context(transport_ctx)

        else:
            raise ValueError(f"Unknown transport: {transport}")

        session = ClientSession(read, write)
        session = await self._exit_stack.enter_async_context(session)
        await session.initialize()

        self.sessions[name] = session

        # Discover tools
        response = await session.list_tools()
        for t in response.tools:
            # Prefix tool names to avoid collisions
            prefixed_name = f"mcp_{name}_{t.name}"
            self.tool_map[prefixed_name] = name

            # Convert to OpenAI function-calling format
            self.tool_specs.append({
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": f"[MCP:{name}] {t.description or t.name}",
                    "parameters": t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
                },
            })
            log.info(f"  MCP tool: {prefixed_name}")

    def get_tool_specs(self) -> list[dict]:
        """Return all MCP tools in OpenAI format."""
        return self.tool_specs

    async def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """Call an MCP tool and return the result as string."""
        server_name = self.tool_map.get(prefixed_name)
        if not server_name:
            return f"Unknown MCP tool: {prefixed_name}"

        session = self.sessions.get(server_name)
        if not session:
            return f"MCP server '{server_name}' not connected."

        # Strip prefix to get original tool name
        prefix = f"mcp_{server_name}_"
        original_name = prefixed_name[len(prefix):]

        try:
            result = await session.call_tool(original_name, arguments)
            # Extract text from result content
            parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
                elif hasattr(content, "data"):
                    parts.append(f"[Binary data: {len(content.data)} bytes]")
            return "\n".join(parts) if parts else "(no output)"
        except Exception as e:
            return f"MCP tool error ({prefixed_name}): {e}"

    def is_mcp_tool(self, name: str) -> bool:
        return name in self.tool_map

    async def close(self):
        await self._exit_stack.aclose()

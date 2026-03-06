"""Generic HTTP request tool for the Discord AI agent.

Allows the bot (owner-only) to make arbitrary HTTP requests with SSRF
protection, response size limits, and env-var secret substitution.
"""

import ipaddress
import json
import logging
import os
import re
import socket
from urllib.parse import urlparse

import aiohttp

from discord import Guild
from tools import tool
from tools_permissions import is_owner

log = logging.getLogger("tools.http")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RESPONSE_BYTES = 50 * 1024  # 50 KB
MAX_TIMEOUT = 120
DEFAULT_TIMEOUT = 30
ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"}

# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("0.0.0.0/8"),          # "this" network
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if the IP address falls in a blocked (private/internal) range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block
    return any(addr in net for net in _BLOCKED_NETWORKS)


def _check_url_safe(url: str) -> None:
    """Resolve the hostname and raise ValueError if it points to a blocked IP."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Could not extract hostname from URL.")

    # Strip IPv6 brackets for resolution
    raw_host = hostname.strip("[]")

    # Try direct IP parse first (covers literal IPs in the URL)
    try:
        addr = ipaddress.ip_address(raw_host)
        if _is_blocked_ip(str(addr)):
            raise ValueError(f"Blocked address: {addr}")
        return
    except ValueError:
        pass  # not a literal IP — resolve via DNS

    try:
        infos = socket.getaddrinfo(raw_host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for {raw_host!r}: {exc}")

    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            raise ValueError(
                f"Hostname {raw_host!r} resolves to blocked address {ip_str}"
            )


# ---------------------------------------------------------------------------
# Env-var substitution
# ---------------------------------------------------------------------------
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _substitute_env_vars(text: str, env_vars: dict[str, str]) -> str:
    """Replace {{PLACEHOLDER}} tokens with env-var values.

    Only placeholders listed in *env_vars* are expanded.
    """
    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        if key not in env_vars:
            return m.group(0)  # leave unknown placeholders untouched
        env_name = env_vars[key]
        return os.environ.get(env_name, "")

    return _PLACEHOLDER_RE.sub(_replacer, text)


def _apply_env_vars(url: str, headers: dict | None, body: str | dict | None,
                    env_vars: dict[str, str]) -> tuple[str, dict | None, str | dict | None]:
    """Apply env-var substitution across url, headers, and body."""
    if not env_vars:
        return url, headers, body

    url = _substitute_env_vars(url, env_vars)

    if headers:
        headers = {
            _substitute_env_vars(k, env_vars): _substitute_env_vars(v, env_vars)
            for k, v in headers.items()
        }

    if isinstance(body, str):
        body = _substitute_env_vars(body, env_vars)
    elif isinstance(body, dict):
        # Serialize → substitute → deserialize back
        raw = json.dumps(body)
        raw = _substitute_env_vars(raw, env_vars)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw  # fall back to substituted string

    return url, headers, body


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------
def _format_response(status: int, resp_headers: dict, body_bytes: bytes,
                     truncated: bool) -> str:
    """Build a human-readable response string."""
    parts: list[str] = [f"**Status:** {status}"]

    # Abbreviated headers (skip noisy ones)
    skip = {"set-cookie", "report-to", "nel", "expect-ct", "alt-svc",
            "cf-ray", "cf-cache-status", "server-timing"}
    hdr_lines = []
    for k, v in resp_headers.items():
        if k.lower() in skip:
            continue
        # Truncate very long header values
        display = v if len(v) <= 200 else v[:200] + "…"
        hdr_lines.append(f"  {k}: {display}")
    if hdr_lines:
        parts.append("**Headers:**\n" + "\n".join(hdr_lines))

    # Body
    content_type = resp_headers.get("content-type", "")
    try:
        text = body_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = repr(body_bytes[:500])

    # Pretty-print JSON when possible
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            obj = json.loads(text)
            text = json.dumps(obj, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass

    if truncated:
        text += "\n\n⚠️ Response truncated to 50 KB."

    parts.append(f"**Body:**\n```\n{text}\n```")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
@tool(
    "http_request",
    (
        "Make an HTTP request to an external URL. Owner-only. "
        "Supports GET/POST/PUT/DELETE/PATCH/HEAD. "
        "Use env_vars to inject secrets: e.g. env_vars={\"API_KEY\": \"DEEPL_API_KEY\"} "
        "replaces {{API_KEY}} in url, headers, and body with the env var value."
    ),
    {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
                "description": "HTTP method.",
            },
            "url": {
                "type": "string",
                "description": "Target URL (must be external).",
            },
            "headers": {
                "type": "object",
                "description": "Optional request headers as key-value pairs.",
                "additionalProperties": {"type": "string"},
            },
            "body": {
                "description": "Optional request body (object for JSON, or string).",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default 30, max 120).",
            },
            "env_vars": {
                "type": "object",
                "description": (
                    "Map of placeholder names to environment variable names. "
                    "{{PLACEHOLDER}} in url/headers/body is replaced with "
                    "the env var value. E.g. {\"API_KEY\": \"DEEPL_API_KEY\"}."
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["method", "url"],
    },
)
async def http_request(guild: Guild, **kwargs) -> str:
    """Execute an HTTP request and return the formatted response."""
    # --- owner gate ---
    if not is_owner(kwargs.get("user_id", "")):
        return "⛔ This tool is restricted to the bot owner."

    method: str = kwargs["method"].upper()
    url: str = kwargs["url"]
    headers: dict | None = kwargs.get("headers")
    body = kwargs.get("body")
    timeout_secs: int = min(int(kwargs.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT)
    env_vars: dict[str, str] = kwargs.get("env_vars") or {}

    if method not in ALLOWED_METHODS:
        return f"❌ Unsupported HTTP method: {method}"

    # --- env-var substitution ---
    try:
        url, headers, body = _apply_env_vars(url, headers, body, env_vars)
    except Exception as exc:
        return f"❌ Error during env-var substitution: {exc}"

    # --- URL validation ---
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"❌ Only http:// and https:// URLs are allowed (got {parsed.scheme!r})."

    # --- SSRF check ---
    try:
        _check_url_safe(url)
    except ValueError as exc:
        return f"⛔ SSRF protection blocked this request: {exc}"

    # --- Prepare body ---
    send_kwargs: dict = {}
    if body is not None and method not in ("GET", "HEAD"):
        if isinstance(body, dict):
            send_kwargs["json"] = body
        else:
            send_kwargs["data"] = str(body)

    # --- Execute request ---
    client_timeout = aiohttp.ClientTimeout(total=timeout_secs)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(
                method, url, headers=headers, **send_kwargs,
            ) as resp:
                # Read with size limit
                chunks: list[bytes] = []
                total = 0
                truncated = False
                async for chunk in resp.content.iter_chunked(8192):
                    remaining = MAX_RESPONSE_BYTES - total
                    if remaining <= 0:
                        truncated = True
                        break
                    chunks.append(chunk[:remaining])
                    total += len(chunk[:remaining])
                    if total >= MAX_RESPONSE_BYTES:
                        truncated = True
                        break

                body_bytes = b"".join(chunks)
                resp_headers = dict(resp.headers)
                return _format_response(resp.status, resp_headers, body_bytes, truncated)

    except aiohttp.InvalidURL as exc:
        return f"❌ Invalid URL: {exc}"
    except aiohttp.ClientConnectorError as exc:
        return f"❌ Connection error: {exc}"
    except asyncio.TimeoutError:
        return f"❌ Request timed out after {timeout_secs}s."
    except aiohttp.ClientError as exc:
        return f"❌ HTTP client error: {exc}"
    except Exception as exc:
        log.exception("Unexpected error in http_request")
        return f"❌ Unexpected error: {type(exc).__name__}: {exc}"

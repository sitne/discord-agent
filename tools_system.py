"""System tools: shell execution, GitHub CLI."""
import asyncio
import logging
import os

from tools import tool
from discord import Guild
from tools_permissions import is_owner

log = logging.getLogger("tools.system")

# Safety: commands that are never allowed
BLOCKED_PATTERNS = [
    "rm -rf /", "rm -rf /*", ":(){ :|:& };:",
    "mkfs", "dd if=", "> /dev/sd",
    "chmod -R 777 /", "shutdown", "reboot", "halt", "poweroff",
    "passwd", "useradd", "userdel",
]

MAX_OUTPUT_LEN = 3000


def _is_safe(cmd: str) -> bool:
    cmd_lower = cmd.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return False
    return True


# ---------------------------------------------------------------------------
# Shell Execution
# ---------------------------------------------------------------------------
@tool(
    "run_shell",
    "Execute a shell command on the server. Use for system tasks, file operations, or running CLI tools. Commands run as the bot user with limited privileges.",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "working_dir": {"type": "string", "description": "Working directory (default: bot directory)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)"},
        },
        "required": ["command"],
    },
)
async def run_shell(guild: Guild, command: str, working_dir: str = None, timeout: int = 30, **kwargs) -> str:
    caller_is_owner = is_owner(kwargs.get("user_id", ""))

    if not caller_is_owner and not _is_safe(command):
        return "⛔ Command blocked for safety reasons."

    # Owner gets longer timeout
    max_timeout = 300 if caller_is_owner else 120
    timeout = min(timeout, max_timeout)
    cwd = working_dir or os.path.expanduser("~")

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()

        if len(output) > MAX_OUTPUT_LEN:
            output = output[:MAX_OUTPUT_LEN] + f"\n... (truncated, {len(output)} total chars)"

        status = "✅" if proc.returncode == 0 else f"❌ (exit code {proc.returncode})"
        return f"{status} `{command}`\n```\n{output}\n```" if output else f"{status} `{command}` (no output)"

    except asyncio.TimeoutError:
        return f"⏰ Command timed out after {timeout}s: `{command}`"
    except Exception as e:
        return f"❌ Error: {e}"


# ---------------------------------------------------------------------------
# GitHub CLI
# ---------------------------------------------------------------------------
async def _gh(args: str, timeout: int = 30) -> str:
    """Run a gh CLI command and return output."""
    proc = await asyncio.create_subprocess_shell(
        f"gh {args}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"},
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    output = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return f"❌ gh error:\n{output}"
    return output


@tool(
    "gh_repo_list",
    "List GitHub repositories for a user or organization.",
    {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub username or org (optional, default: authenticated user)"},
            "limit": {"type": "integer", "description": "Max repos (default 10)"},
        },
        "required": [],
    },
)
async def gh_repo_list(guild: Guild, owner: str = None, limit: int = 10, **kwargs) -> str:
    limit = min(limit, 30)
    target = owner if owner else ""
    result = await _gh(f"repo list {target} --limit {limit}")
    return result


@tool(
    "gh_repo_view",
    "View details of a GitHub repository.",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/name)"},
        },
        "required": ["repo"],
    },
)
async def gh_repo_view(guild: Guild, repo: str, **kwargs) -> str:
    result = await _gh(f"repo view {repo}")
    if len(result) > MAX_OUTPUT_LEN:
        result = result[:MAX_OUTPUT_LEN] + "\n... (truncated)"
    return result


@tool(
    "gh_issue_list",
    "List issues for a GitHub repository.",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/name)"},
            "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Issue state (default: open)"},
            "limit": {"type": "integer", "description": "Max issues (default 10)"},
        },
        "required": ["repo"],
    },
)
async def gh_issue_list(guild: Guild, repo: str, state: str = "open", limit: int = 10, **kwargs) -> str:
    result = await _gh(f"issue list --repo {repo} --state {state} --limit {min(limit, 30)}")
    return result


@tool(
    "gh_issue_view",
    "View a specific GitHub issue.",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/name)"},
            "number": {"type": "integer", "description": "Issue number"},
        },
        "required": ["repo", "number"],
    },
)
async def gh_issue_view(guild: Guild, repo: str, number: int, **kwargs) -> str:
    result = await _gh(f"issue view {number} --repo {repo}")
    if len(result) > MAX_OUTPUT_LEN:
        result = result[:MAX_OUTPUT_LEN] + "\n... (truncated)"
    return result


@tool(
    "gh_issue_create",
    "Create a new GitHub issue.",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/name)"},
            "title": {"type": "string", "description": "Issue title"},
            "body": {"type": "string", "description": "Issue body/description"},
            "labels": {"type": "string", "description": "Comma-separated labels"},
        },
        "required": ["repo", "title"],
    },
)
async def gh_issue_create(guild: Guild, repo: str, title: str, body: str = "", labels: str = None, **kwargs) -> str:
    cmd = f'issue create --repo {repo} --title "{title}" --body "{body}"'
    if labels:
        cmd += f' --label "{labels}"'
    result = await _gh(cmd)
    return result


@tool(
    "gh_pr_list",
    "List pull requests for a GitHub repository.",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/name)"},
            "state": {"type": "string", "enum": ["open", "closed", "merged", "all"], "description": "PR state"},
            "limit": {"type": "integer", "description": "Max PRs (default 10)"},
        },
        "required": ["repo"],
    },
)
async def gh_pr_list(guild: Guild, repo: str, state: str = "open", limit: int = 10, **kwargs) -> str:
    result = await _gh(f"pr list --repo {repo} --state {state} --limit {min(limit, 30)}")
    return result


@tool(
    "gh_pr_view",
    "View a specific pull request.",
    {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/name)"},
            "number": {"type": "integer", "description": "PR number"},
        },
        "required": ["repo", "number"],
    },
)
async def gh_pr_view(guild: Guild, repo: str, number: int, **kwargs) -> str:
    result = await _gh(f"pr view {number} --repo {repo}")
    if len(result) > MAX_OUTPUT_LEN:
        result = result[:MAX_OUTPUT_LEN] + "\n... (truncated)"
    return result


@tool(
    "gh_search_repos",
    "Search GitHub repositories.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["query"],
    },
)
async def gh_search_repos(guild: Guild, query: str, limit: int = 5, **kwargs) -> str:
    result = await _gh(f'search repos "{query}" --limit {min(limit, 20)}')
    return result


@tool(
    "gh_search_code",
    "Search code on GitHub.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Code search query"},
            "repo": {"type": "string", "description": "Limit to repository (owner/name, optional)"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["query"],
    },
)
async def gh_search_code(guild: Guild, query: str, repo: str = None, limit: int = 5, **kwargs) -> str:
    cmd = f'search code "{query}" --limit {min(limit, 20)}'
    if repo:
        cmd += f' --repo {repo}'
    result = await _gh(cmd)
    return result


@tool(
    "gh_run_command",
    "Run an arbitrary gh CLI command. Use for advanced GitHub operations not covered by other tools.",
    {
        "type": "object",
        "properties": {
            "args": {"type": "string", "description": "Arguments to pass to gh (e.g. 'api /repos/owner/name')"},
        },
        "required": ["args"],
    },
)
async def gh_run_command(guild: Guild, args: str, **kwargs) -> str:
    # Block dangerous operations (owner bypasses)
    if not is_owner(kwargs.get("user_id", "")):
        dangerous = ["auth logout", "auth token", "ssh-key delete"]
        if any(d in args.lower() for d in dangerous):
            return "⛔ This gh command is blocked for safety."
    result = await _gh(args, timeout=60)
    if len(result) > MAX_OUTPUT_LEN:
        result = result[:MAX_OUTPUT_LEN] + "\n... (truncated)"
    return result

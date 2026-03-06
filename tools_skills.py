"""Skill management tools for the Discord AI agent.

Provides tools to list, load, create, install, remove, and search skills.
Skills follow the SKILL.md standard (Vercel Agent Skills pattern).
"""

import asyncio
import logging
import subprocess

from discord import Guild
from tools import tool
from tools_permissions import is_owner
import skills_manager

log = logging.getLogger("tools.skills")


# ---------------------------------------------------------------------------
# List skills (no restriction)
# ---------------------------------------------------------------------------
@tool(
    "list_skills",
    "List all installed skills with their names and descriptions. Use this to see what specialized knowledge is available.",
    {"type": "object", "properties": {}, "required": []},
)
async def list_skills(guild: Guild, **kwargs) -> str:
    skills = skills_manager.discover_skills()
    if not skills:
        return "No skills installed. Use `install_skill` to add skills from GitHub or `create_skill` to create one."

    lines = [f"**Installed Skills ({len(skills)}):**", ""]
    for s in skills:
        lines.append(f"- **{s['name']}**: {s['description']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Load skill (no restriction)
# ---------------------------------------------------------------------------
@tool(
    "load_skill",
    "Load full skill instructions by name. Call this when you need detailed guidance for a task that matches an available skill.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name (e.g. 'image-generation', 'translation')"},
        },
        "required": ["name"],
    },
)
async def tool_load_skill(guild: Guild, name: str, **kwargs) -> str:
    skill = skills_manager.load_skill(name)
    if not skill:
        # Try fuzzy match
        available = skills_manager.discover_skills()
        names = [s["name"] for s in available]
        return f"Skill '{name}' not found. Available skills: {', '.join(names) or 'none'}"

    return f"""# Skill: {skill['name']}
_{skill['description']}_

{skill['body']}"""


# ---------------------------------------------------------------------------
# Create skill (owner-only)
# ---------------------------------------------------------------------------
@tool(
    "create_skill",
    "Create a new SKILL.md to capture specialized knowledge. Use this after successfully completing a novel task to remember the approach for next time. Owner-only.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name in lowercase-hyphen format (e.g. 'web-scraping', 'pdf-generation')",
            },
            "description": {
                "type": "string",
                "description": "Routing-rule style description: what the skill does and when to use it",
            },
            "keywords": {
                "type": "string",
                "description": "Comma-separated keywords for matching (include Japanese if relevant)",
            },
            "content": {
                "type": "string",
                "description": "The markdown body of the skill (instructions, examples, tips)",
            },
        },
        "required": ["name", "description", "keywords", "content"],
    },
)
async def tool_create_skill(guild: Guild, name: str, description: str, keywords: str, content: str, **kwargs) -> str:
    user_id = kwargs.get("user_id", "")
    if not is_owner(user_id):
        return "⛔ Permission denied: only the bot owner can create skills."

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    return skills_manager.create_skill(name, description, kw_list, content)


# ---------------------------------------------------------------------------
# Install skill from GitHub (owner-only)
# ---------------------------------------------------------------------------
@tool(
    "install_skill",
    "Install skill(s) from a GitHub repository containing SKILL.md files. Owner-only.",
    {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "GitHub repo (e.g. 'user/repo' or full URL)",
            },
            "skill_name": {
                "type": "string",
                "description": "Install only this specific skill from the repo (optional — omit to install all)",
            },
        },
        "required": ["repo"],
    },
)
async def tool_install_skill(guild: Guild, repo: str, skill_name: str = None, **kwargs) -> str:
    user_id = kwargs.get("user_id", "")
    if not is_owner(user_id):
        return "⛔ Permission denied: only the bot owner can install skills."

    # Run the blocking git clone in a thread pool
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, skills_manager.install_skill_from_github, repo, skill_name,
    )
    return result


# ---------------------------------------------------------------------------
# Remove skill (owner-only)
# ---------------------------------------------------------------------------
@tool(
    "remove_skill",
    "Delete an installed skill by name. Owner-only.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name to delete"},
        },
        "required": ["name"],
    },
)
async def tool_remove_skill(guild: Guild, name: str, **kwargs) -> str:
    user_id = kwargs.get("user_id", "")
    if not is_owner(user_id):
        return "⛔ Permission denied: only the bot owner can remove skills."

    return skills_manager.delete_skill(name)


# ---------------------------------------------------------------------------
# Search community skills on GitHub (no restriction)
# ---------------------------------------------------------------------------
@tool(
    "search_community_skills",
    "Search GitHub for community-shared SKILL.md repositories. Returns repos that contain agent skills you can install.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (e.g. 'discord bot', 'image generation', 'web scraping')",
            },
        },
        "required": ["query"],
    },
)
async def tool_search_community_skills(guild: Guild, query: str, **kwargs) -> str:
    try:
        # Search for repos containing SKILL.md files matching the query
        proc = await asyncio.create_subprocess_exec(
            "gh", "search", "code", f"SKILL.md {query}",
            "--filename", "SKILL.md",
            "--limit", "10",
            "--json", "repository,path,textMatch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            # Fallback: search by topic
            proc2 = await asyncio.create_subprocess_exec(
                "gh", "search", "repos", f"agent-skills {query}",
                "--limit", "10",
                "--json", "fullName,description,url,stargazersCount",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=30)
            if proc2.returncode != 0:
                return f"❌ Search failed: {stderr.decode().strip()} / {stderr2.decode().strip()}"

            import json
            repos = json.loads(stdout2.decode())
            if not repos:
                return f"No repositories found for '{query}'."

            lines = [f"**Community Skill Repositories** (query: '{query}'):"]
            for r in repos:
                stars = r.get("stargazersCount", 0)
                desc = r.get("description", "No description") or "No description"
                lines.append(f"- **{r['fullName']}** ⭐{stars} — {desc[:100]}")
                lines.append(f"  Install: `install_skill` with repo=`{r['fullName']}`")
            return "\n".join(lines)

        import json
        results = json.loads(stdout.decode())
        if not results:
            return f"No SKILL.md files found matching '{query}'."

        # Group by repository
        repos: dict[str, list[str]] = {}
        for r in results:
            repo_name = r.get("repository", {}).get("fullName", "unknown")
            path = r.get("path", "")
            repos.setdefault(repo_name, []).append(path)

        lines = [f"**SKILL.md files found** (query: '{query}'):"]
        for repo_name, paths in repos.items():
            lines.append(f"\n**{repo_name}** ({len(paths)} skill(s)):")
            for p in paths[:5]:
                skill_dir = p.rsplit("/", 1)[0] if "/" in p else "(root)"
                lines.append(f"  - `{skill_dir}`")
            if len(paths) > 5:
                lines.append(f"  - ...and {len(paths) - 5} more")
            lines.append(f"  Install: `install_skill` with repo=`{repo_name}`")

        return "\n".join(lines)

    except asyncio.TimeoutError:
        return "❌ Search timed out (30s limit)."
    except FileNotFoundError:
        return "❌ `gh` CLI not found. Install GitHub CLI to search community skills."
    except Exception as e:
        return f"❌ Search failed: {e}"

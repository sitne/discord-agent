"""Skills manager — discovers, loads, and manages SKILL.md files.

Follows the Vercel Agent Skills standard with Progressive Disclosure:
- discover_skills() returns lightweight metadata (cached)
- load_skill() returns full content on demand
- Skills live in skills/<name>/SKILL.md directories
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("skills")

_SKILLS_DIR = Path(__file__).parent / "skills"
_cache: list[dict] | None = None


# ---------------------------------------------------------------------------
# Frontmatter parser (no PyYAML dependency)
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter between --- delimiters.

    Returns (metadata_dict, body_text).  Uses simple string parsing
    so we don't need PyYAML.
    """
    meta: dict = {}
    body = text

    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return meta, body

    # Find second ---
    first_end = stripped.index("---") + 3
    rest = stripped[first_end:]
    second_idx = rest.find("\n---")
    if second_idx == -1:
        return meta, body

    frontmatter_block = rest[:second_idx].strip()
    body = rest[second_idx + 4:]  # skip past \n---
    body = body.lstrip("\n")

    for line in frontmatter_block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Parse YAML-style list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
            value = [item.strip().strip("'\"" ) for item in items if item.strip()]

        # Strip surrounding quotes from scalar values
        elif (value.startswith('"') and value.endswith('"')) or \
             (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        meta[key] = value

    return meta, body


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def discover_skills() -> list[dict]:
    """Scan skills/ for directories containing SKILL.md.

    Returns list of {name, description, path, keywords} — lightweight metadata only.
    Cached. Call reload_skills() to refresh.
    """
    global _cache
    if _cache is not None:
        return _cache

    if not _SKILLS_DIR.is_dir():
        log.info("Skills directory not found at %s", _SKILLS_DIR)
        _cache = []
        return _cache

    skills: list[dict] = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            log.warning("Failed to read %s: %s", skill_md, e)
            continue

        meta, _ = _parse_frontmatter(text)

        name = meta.get("name", skill_dir.name)
        description = meta.get("description", "")
        keywords = meta.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]

        skills.append({
            "name": name,
            "description": description,
            "path": str(skill_md),
            "keywords": [k.lower() for k in keywords],
        })

    log.info("Discovered %d skill(s): %s", len(skills), [s["name"] for s in skills])
    _cache = skills
    return _cache


def load_skill(name: str) -> dict | None:
    """Load full SKILL.md content for a specific skill.

    Returns {name, description, keywords, body, path} or None.
    """
    skills = discover_skills()
    skill_meta = None
    for s in skills:
        if s["name"] == name:
            skill_meta = s
            break

    if not skill_meta:
        return None

    try:
        text = Path(skill_meta["path"]).read_text(encoding="utf-8")
    except Exception as e:
        log.warning("Failed to read skill %s: %s", name, e)
        return None

    meta, body = _parse_frontmatter(text)

    return {
        "name": skill_meta["name"],
        "description": skill_meta["description"],
        "keywords": skill_meta["keywords"],
        "body": body,
        "path": skill_meta["path"],
    }


def search_installed_skills(query: str) -> list[dict]:
    """Search installed skills by keyword matching against name+description+keywords.

    Returns matching skills sorted by relevance score (descending).
    """
    skills = discover_skills()
    if not skills:
        return []

    text = query.lower()
    tokens = re.findall(r"[a-z]+|[\u3040-\u9fff\uff00-\uffef]+", text)

    scored: list[tuple[int, dict]] = []
    for skill in skills:
        score = 0
        # Search against keywords
        for kw in skill["keywords"]:
            if kw in text:
                score += 2
                continue
            for tok in tokens:
                if tok == kw:
                    score += 2
                    break
                if len(kw) >= 4 and len(tok) >= 4:
                    if tok.startswith(kw) or kw.startswith(tok):
                        score += 1
                        break

        # Search against name and description
        name_lower = skill["name"].lower().replace("-", " ")
        desc_lower = skill["description"].lower()
        for tok in tokens:
            if tok in name_lower:
                score += 2
            if tok in desc_lower:
                score += 1

        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]


def format_skills_discovery(skills: list[dict]) -> str:
    """Format skill discovery list (names + descriptions only) for system prompt."""
    if not skills:
        return ""

    lines = [
        "## Available Skills",
        "Use the `load_skill` tool to load full skill instructions when relevant.",
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill['name']}**: {skill['description']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill management
# ---------------------------------------------------------------------------

def install_skill_from_github(repo: str, skill_name: str | None = None) -> str:
    """Clone a GitHub repo and copy skill directories into skills/.

    If skill_name is specified, only install that one skill.
    Returns status message.
    This is a sync function that calls git clone in a subprocess.
    """
    # Normalise repo to a clone URL
    if not repo.startswith("http"):
        repo = f"https://github.com/{repo}.git"
    elif not repo.endswith(".git"):
        repo = repo.rstrip("/") + ".git"

    tmpdir = tempfile.mkdtemp(prefix="skill_install_")
    try:
        # Clone with depth 1 for speed
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo, tmpdir + "/repo"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"❌ Git clone failed: {result.stderr.strip()}"

        clone_dir = Path(tmpdir) / "repo"

        # Find all SKILL.md files
        skill_mds = list(clone_dir.rglob("SKILL.md"))
        if not skill_mds:
            return "❌ No SKILL.md files found in repository."

        installed = []
        warnings = []

        for skill_md in skill_mds:
            skill_dir = skill_md.parent
            dir_name = skill_dir.name

            # If specific skill requested, filter
            if skill_name and dir_name != skill_name:
                continue

            # Safety check: warn about scripts
            scripts_dir = skill_dir / "scripts"
            if scripts_dir.exists():
                warnings.append(f"⚠️ **{dir_name}** contains a `scripts/` directory — review before executing any scripts.")

            # Copy to skills/
            dest = _SKILLS_DIR / dir_name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill_dir, dest)
            installed.append(dir_name)

        if not installed:
            if skill_name:
                return f"❌ Skill '{skill_name}' not found in repository. Available: {[sm.parent.name for sm in skill_mds]}"
            return "❌ No skills could be installed."

        # Refresh cache
        reload_skills()

        msg = f"✅ Installed {len(installed)} skill(s): {', '.join(installed)}"
        if warnings:
            msg += "\n" + "\n".join(warnings)
        return msg

    except subprocess.TimeoutExpired:
        return "❌ Git clone timed out (60s limit)."
    except Exception as e:
        return f"❌ Install failed: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def create_skill(name: str, description: str, keywords: list[str], body: str) -> str:
    """Create a new skill directory with SKILL.md. Returns status message."""
    # Validate name: lowercase, hyphens, no spaces
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name) and len(name) > 1:
        # Also allow single char names
        if not re.match(r"^[a-z0-9-]+$", name):
            return f"❌ Invalid skill name '{name}'. Use lowercase letters, numbers, and hyphens only."

    skill_dir = _SKILLS_DIR / name
    if skill_dir.exists():
        return f"❌ Skill '{name}' already exists. Delete it first or choose a different name."

    # Build SKILL.md content
    kw_str = ", ".join(keywords)
    content = f"""---
name: {name}
description: {description}
keywords: [{kw_str}]
---

{body}
"""

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    except Exception as e:
        return f"❌ Failed to create skill: {e}"

    # Refresh cache
    reload_skills()
    return f"✅ Created skill '{name}' at {skill_dir}/SKILL.md"


def delete_skill(name: str) -> str:
    """Delete a skill directory. Returns status message."""
    skill_dir = _SKILLS_DIR / name
    if not skill_dir.exists():
        return f"❌ Skill '{name}' not found."
    if not (skill_dir / "SKILL.md").exists():
        return f"❌ '{name}' exists but doesn't contain SKILL.md — not a valid skill directory."

    try:
        shutil.rmtree(skill_dir)
    except Exception as e:
        return f"❌ Failed to delete skill: {e}"

    reload_skills()
    return f"✅ Deleted skill '{name}'."


def reload_skills() -> list[dict]:
    """Clear cache and rediscover."""
    global _cache
    _cache = None
    return discover_skills()

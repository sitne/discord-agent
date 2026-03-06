"""Skills manager — loads markdown skill files and matches them to user input."""

import logging
import os
import re
from pathlib import Path

log = logging.getLogger("skills")

_SKILLS_DIR = Path(__file__).parent / "skills"
_cache: list[dict] | None = None


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
            value = [item.strip().strip("'\"") for item in items if item.strip()]

        # Strip surrounding quotes from scalar values
        elif (value.startswith('"') and value.endswith('"')) or \
             (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        meta[key] = value

    return meta, body


def _load_skill_file(path: Path) -> dict | None:
    """Load a single skill file and return a skill dict, or None on error."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("Failed to read skill file %s: %s", path.name, e)
        return None

    meta, body = _parse_frontmatter(text)

    name = meta.get("name", path.stem.replace("_", " ").title())
    description = meta.get("description", "")
    keywords = meta.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    tools = meta.get("tools", [])
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]

    return {
        "name": name,
        "description": description,
        "keywords": [k.lower() for k in keywords],
        "tools": tools,
        "body": body,
        "file": path.name,
    }


def load_skills() -> list[dict]:
    """Load all .md skill files from the skills/ directory (cached)."""
    global _cache
    if _cache is not None:
        return _cache

    skills_dir = _SKILLS_DIR
    if not skills_dir.is_dir():
        log.info("Skills directory not found at %s", skills_dir)
        _cache = []
        return _cache

    skills: list[dict] = []
    for md_file in sorted(skills_dir.glob("*.md")):
        skill = _load_skill_file(md_file)
        if skill:
            skills.append(skill)

    log.info("Loaded %d skill(s): %s", len(skills), [s["name"] for s in skills])
    _cache = skills
    return _cache


def reload_skills() -> list[dict]:
    """Clear cache and reload all skills."""
    global _cache
    _cache = None
    return load_skills()


def match_skills(user_input: str, max_skills: int = 3) -> list[dict]:
    """Match skills to user input by keyword overlap.

    Counts how many keywords appear as substrings in the lowered user input.
    Returns top matches (score > 0), limited to *max_skills*.
    """
    skills = load_skills()
    if not skills:
        return []

    text = user_input.lower()
    # Tokenise: latin words + CJK characters + sequences
    tokens = re.findall(r"[a-z]+|[\u3040-\u9fff\uff00-\uffef]+", text)

    scored: list[tuple[int, dict]] = []
    for skill in skills:
        score = 0
        for kw in skill["keywords"]:
            # Direct substring check (great for CJK)
            if kw in text:
                score += 2
                continue

            # Token-level matching for latin text
            matched = False
            for tok in tokens:
                if tok == kw:
                    score += 2
                    matched = True
                    break
                # Partial/fuzzy: "translate" matches "translation" and vice-versa
                if len(kw) >= 4 and len(tok) >= 4 and (tok.startswith(kw[:4]) or kw.startswith(tok[:4])):
                    if tok.startswith(kw) or kw.startswith(tok):
                        score += 1
                        matched = True
                        break
            if not matched and len(kw) >= 5:
                # Word-boundary substring for longer keywords
                if re.search(r'\b' + re.escape(kw), text):
                    score += 2
        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max_skills]]


def format_skills_context(skills: list[dict]) -> str:
    """Format matched skills into a string for system-prompt injection."""
    if not skills:
        return ""

    parts = ["## Relevant Skills\n"]
    for skill in skills:
        parts.append(f"### {skill['name']}")
        if skill["description"]:
            parts.append(f"_{skill['description']}_")
        if skill["tools"]:
            parts.append(f"Tools: {', '.join(skill['tools'])}")
        parts.append("")
        # Include the body but trim excessive whitespace
        body = skill["body"].strip()
        parts.append(body)
        parts.append("")

    return "\n".join(parts)

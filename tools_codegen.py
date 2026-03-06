"""Code generation tools: create projects on GitHub, push code, run CI via Actions."""
import asyncio
import json
import logging
import os
import shutil
import tempfile
from textwrap import dedent

from tools import tool
from discord import Guild
from tools_permissions import is_owner

log = logging.getLogger("tools.codegen")

GH_OWNER = "sitne"  # GitHub account
MAX_OUTPUT = 3000


async def _gh(args: str, timeout: int = 60, cwd: str = None) -> tuple[int, str]:
    """Run gh CLI command. Returns (returncode, output)."""
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
    proc = await asyncio.create_subprocess_shell(
        f"gh {args}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode("utf-8", errors="replace").strip()


async def _git(args: str, cwd: str, timeout: int = 30) -> tuple[int, str]:
    """Run git command in a directory."""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "sitne",
        "GIT_AUTHOR_EMAIL": "sitne@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "sitne",
        "GIT_COMMITTER_EMAIL": "sitne@users.noreply.github.com",
    }
    proc = await asyncio.create_subprocess_shell(
        f"git {args}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# CI workflow templates per language
# ---------------------------------------------------------------------------
WORKFLOW_TEMPLATES = {
    "python": dedent("""\
        name: CI
        on: [push, pull_request]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
                with:
                  python-version: '3.12'
              - name: Install dependencies
                run: |
                  pip install -r requirements.txt 2>/dev/null || true
                  pip install pytest 2>/dev/null || true
              - name: Run
                run: |
                  if [ -f pytest.ini ] || [ -d tests ]; then
                    python -m pytest -v
                  else
                    for f in *.py; do
                      echo "=== Running $f ==="
                      python "$f" && echo "✅ $f passed" || echo "❌ $f failed"
                    done
                  fi
    """),
    "node": dedent("""\
        name: CI
        on: [push, pull_request]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-node@v4
                with:
                  node-version: '20'
              - name: Install dependencies
                run: npm install 2>/dev/null || true
              - name: Run
                run: |
                  if [ -f package.json ] && grep -q '"test"' package.json; then
                    npm test
                  else
                    for f in *.js *.mjs; do
                      [ -f "$f" ] || continue
                      echo "=== Running $f ==="
                      node "$f" && echo "✅ $f passed" || echo "❌ $f failed"
                    done
                  fi
    """),
    "go": dedent("""\
        name: CI
        on: [push, pull_request]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-go@v5
                with:
                  go-version: '1.22'
              - name: Build & Test
                run: |
                  go build ./...
                  go test -v ./...
    """),
    "rust": dedent("""\
        name: CI
        on: [push, pull_request]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: dtolnay/rust-toolchain@stable
              - name: Build & Test
                run: |
                  cargo build
                  cargo test
    """),
    "generic": dedent("""\
        name: CI
        on: [push, pull_request]
        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - name: Run
                run: echo "No CI configured — add your build/test commands here"
    """),
}


# ---------------------------------------------------------------------------
# codegen_create_project
# ---------------------------------------------------------------------------
@tool(
    "codegen_create_project",
    "Create a new GitHub repository with code files and CI. "
    "The project is pushed to GitHub and CI runs automatically. "
    "Use this for generating, storing, and testing code permanently.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Repository name (e.g. 'my-scraper'). Will be created under the configured GitHub account.",
            },
            "description": {
                "type": "string",
                "description": "Short description of the project.",
            },
            "language": {
                "type": "string",
                "enum": ["python", "node", "go", "rust", "generic"],
                "description": "Primary language — determines CI workflow template.",
            },
            "files": {
                "type": "object",
                "description": "Map of filename to file content. E.g. {'main.py': 'print(1)', 'requirements.txt': 'requests'}",
                "additionalProperties": {"type": "string"},
            },
            "private": {
                "type": "boolean",
                "description": "Make repo private (default: false, public = free CI)",
            },
        },
        "required": ["name", "language", "files"],
    },
)
async def codegen_create_project(
    guild: Guild,
    name: str,
    language: str,
    files: dict,
    description: str = "",
    private: bool = False,
    **kwargs,
) -> str:
    if not is_owner(kwargs.get("user_id", "")):
        return "⛔ codegen tools are owner-only."

    tmpdir = tempfile.mkdtemp(prefix="codegen_")
    try:
        # 1. Create GitHub repo
        visibility = "--private" if private else "--public"
        desc_flag = f'--description "{description}"' if description else ""
        rc, out = await _gh(f'repo create {GH_OWNER}/{name} {visibility} {desc_flag} --clone', cwd=tmpdir)
        if rc != 0:
            return f"❌ Failed to create repo: {out}"

        repo_dir = os.path.join(tmpdir, name)
        if not os.path.isdir(repo_dir):
            # gh might clone into tmpdir directly
            repo_dir = tmpdir

        # 2. Write files
        for filepath, content in files.items():
            full_path = os.path.join(repo_dir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)

        # 3. Write CI workflow
        workflow_dir = os.path.join(repo_dir, ".github", "workflows")
        os.makedirs(workflow_dir, exist_ok=True)
        workflow_content = WORKFLOW_TEMPLATES.get(language, WORKFLOW_TEMPLATES["generic"])
        with open(os.path.join(workflow_dir, "ci.yml"), "w") as f:
            f.write(workflow_content)

        # 4. Commit and push
        await _git("add -A", cwd=repo_dir)
        rc, out = await _git('commit -m "Initial commit: project setup with CI"', cwd=repo_dir)
        if rc != 0:
            return f"❌ Git commit failed: {out}"

        rc, out = await _git("push -u origin main", cwd=repo_dir, timeout=60)
        if rc != 0:
            # Try 'master' branch
            rc, out = await _git("push -u origin master", cwd=repo_dir, timeout=60)
            if rc != 0:
                return f"❌ Git push failed: {out}"

        repo_url = f"https://github.com/{GH_OWNER}/{name}"
        file_list = "\n".join(f"  - `{fp}`" for fp in files.keys())
        return (
            f"✅ Project created: {repo_url}\n"
            f"📁 Files:\n{file_list}\n"
            f"⚙️ CI: GitHub Actions ({language}) — will run automatically on push.\n"
            f"🔗 Actions: {repo_url}/actions"
        )

    except Exception as e:
        return f"❌ Error: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# codegen_update_files
# ---------------------------------------------------------------------------
@tool(
    "codegen_update_files",
    "Update or add files in an existing GitHub repository. "
    "Commits and pushes changes. Optionally create on a new branch for a PR.",
    {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository name (just the name, not full path). Must be under the configured account.",
            },
            "files": {
                "type": "object",
                "description": "Map of filename to new content. Set content to null/empty to delete a file.",
                "additionalProperties": {"type": "string"},
            },
            "commit_message": {
                "type": "string",
                "description": "Commit message describing the changes.",
            },
            "branch": {
                "type": "string",
                "description": "Branch name. If doesn't exist, creates from main. Default: main/master.",
            },
            "create_pr": {
                "type": "boolean",
                "description": "Create a pull request after pushing (only if branch != main). Default: false.",
            },
            "pr_title": {
                "type": "string",
                "description": "PR title (used only if create_pr=true).",
            },
        },
        "required": ["repo", "files", "commit_message"],
    },
)
async def codegen_update_files(
    guild: Guild,
    repo: str,
    files: dict,
    commit_message: str,
    branch: str = None,
    create_pr: bool = False,
    pr_title: str = None,
    **kwargs,
) -> str:
    if not is_owner(kwargs.get("user_id", "")):
        return "⛔ codegen tools are owner-only."

    tmpdir = tempfile.mkdtemp(prefix="codegen_")
    try:
        # Clone
        rc, out = await _gh(f"repo clone {GH_OWNER}/{repo} {tmpdir}/repo", timeout=60)
        if rc != 0:
            return f"❌ Clone failed: {out}"
        repo_dir = os.path.join(tmpdir, "repo")

        # Branch handling
        if branch and branch not in ("main", "master"):
            # Check if remote branch exists
            rc, _ = await _git(f"ls-remote --exit-code origin {branch}", cwd=repo_dir)
            if rc == 0:
                await _git(f"checkout {branch}", cwd=repo_dir)
            else:
                await _git(f"checkout -b {branch}", cwd=repo_dir)

        # Write files
        updated = []
        deleted = []
        for filepath, content in files.items():
            full_path = os.path.join(repo_dir, filepath)
            if content is None or content == "":
                if os.path.exists(full_path):
                    os.remove(full_path)
                    deleted.append(filepath)
            else:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w") as f:
                    f.write(content)
                updated.append(filepath)

        # Commit & push
        await _git("add -A", cwd=repo_dir)
        rc, out = await _git(f'commit -m "{commit_message}"', cwd=repo_dir)
        if rc != 0:
            if "nothing to commit" in out:
                return "ℹ️ No changes detected."
            return f"❌ Commit failed: {out}"

        push_target = branch if branch else "HEAD"
        rc, out = await _git(f"push origin {push_target}", cwd=repo_dir, timeout=60)
        if rc != 0:
            return f"❌ Push failed: {out}"

        result = f"✅ Pushed to `{GH_OWNER}/{repo}`"
        if branch:
            result += f" (branch: `{branch}`)"
        if updated:
            result += f"\n📝 Updated: {', '.join(f'`{f}`' for f in updated)}"
        if deleted:
            result += f"\n🗑️ Deleted: {', '.join(f'`{f}`' for f in deleted)}"

        # Create PR if requested
        if create_pr and branch and branch not in ("main", "master"):
            title = pr_title or commit_message
            rc, out = await _gh(
                f'pr create --repo {GH_OWNER}/{repo} --head {branch} '
                f'--title "{title}" --body "Auto-generated by Discord Agent"',
                cwd=repo_dir,
            )
            if rc == 0:
                result += f"\n🔀 PR created: {out}"
            else:
                result += f"\n⚠️ PR creation failed: {out}"

        repo_url = f"https://github.com/{GH_OWNER}/{repo}"
        result += f"\n🔗 Actions: {repo_url}/actions"
        return result

    except Exception as e:
        return f"❌ Error: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# codegen_check_ci
# ---------------------------------------------------------------------------
@tool(
    "codegen_check_ci",
    "Check the latest CI (GitHub Actions) run status and logs for a repository. "
    "Use after pushing code to see if tests passed or failed.",
    {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository name (just name, under configured account).",
            },
            "branch": {
                "type": "string",
                "description": "Branch to check (default: default branch).",
            },
            "show_logs": {
                "type": "boolean",
                "description": "Include failed job logs in output (default: true).",
            },
        },
        "required": ["repo"],
    },
)
async def codegen_check_ci(
    guild: Guild,
    repo: str,
    branch: str = None,
    show_logs: bool = True,
    **kwargs,
) -> str:
    if not is_owner(kwargs.get("user_id", "")):
        return "⛔ codegen tools are owner-only."

    full_repo = f"{GH_OWNER}/{repo}"
    branch_flag = f"--branch {branch}" if branch else ""

    # Get latest run
    rc, out = await _gh(
        f"run list --repo {full_repo} {branch_flag} --limit 1 --json databaseId,status,conclusion,name,headBranch,createdAt,updatedAt",
    )
    if rc != 0:
        return f"❌ Failed to get CI runs: {out}"

    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        return f"❌ Failed to parse runs: {out}"

    if not runs:
        return f"ℹ️ No CI runs found for `{full_repo}`."

    run = runs[0]
    run_id = run["databaseId"]
    status = run["status"]
    conclusion = run.get("conclusion", "")
    run_branch = run.get("headBranch", "?")

    # Status emoji
    if status == "completed":
        emoji = "✅" if conclusion == "success" else "❌"
    elif status == "in_progress":
        emoji = "🔄"
    elif status == "queued":
        emoji = "⏳"
    else:
        emoji = "❓"

    result = (
        f"{emoji} **{run.get('name', 'CI')}** on `{run_branch}`\n"
        f"Status: `{status}` | Conclusion: `{conclusion or 'pending'}`\n"
        f"Created: {run.get('createdAt', '?')}\n"
        f"🔗 https://github.com/{full_repo}/actions/runs/{run_id}"
    )

    # If failed and show_logs, get the failed job logs
    if show_logs and conclusion == "failure":
        rc, log_out = await _gh(
            f"run view {run_id} --repo {full_repo} --log-failed",
            timeout=30,
        )
        if rc == 0 and log_out:
            if len(log_out) > MAX_OUTPUT:
                log_out = log_out[-MAX_OUTPUT:]  # Keep the tail (most relevant)
                log_out = f"... (truncated)\n{log_out}"
            result += f"\n\n📋 **Failed logs:**\n```\n{log_out}\n```"

    return result


# ---------------------------------------------------------------------------
# codegen_list_projects
# ---------------------------------------------------------------------------
@tool(
    "codegen_list_projects",
    "List GitHub repositories owned by the configured account. Shows recent repos with CI status.",
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max repos to list (default: 10).",
            },
        },
        "required": [],
    },
)
async def codegen_list_projects(guild: Guild, limit: int = 10, **kwargs) -> str:
    limit = min(limit, 30)
    rc, out = await _gh(
        f"repo list {GH_OWNER} --limit {limit} "
        f"--json name,description,isPrivate,pushedAt,url",
    )
    if rc != 0:
        return f"❌ Failed: {out}"

    if not out:
        return "No repositories found."

    try:
        repos = json.loads(out)
    except json.JSONDecodeError:
        return f"📦 **Repositories:**\n```\n{out}\n```"

    lines = []
    for r in repos:
        vis = "🔒" if r.get("isPrivate") else "🌐"
        desc = r.get("description") or "no desc"
        name = r.get("name", "?")
        pushed = r.get("pushedAt", "?")[:10]
        lines.append(f"{vis} **{name}** — {desc} (pushed: {pushed})")

    return f"📦 **Repositories ({GH_OWNER})**\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# codegen_read_file
# ---------------------------------------------------------------------------
@tool(
    "codegen_read_file",
    "Read a file from a GitHub repository without cloning. Fast way to check code.",
    {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository name (under configured account).",
            },
            "path": {
                "type": "string",
                "description": "File path in the repo (e.g. 'src/main.py').",
            },
            "branch": {
                "type": "string",
                "description": "Branch/ref (default: default branch).",
            },
        },
        "required": ["repo", "path"],
    },
)
async def codegen_read_file(
    guild: Guild, repo: str, path: str, branch: str = None, **kwargs
) -> str:
    ref = branch or "HEAD"
    rc, out = await _gh(
        f"api /repos/{GH_OWNER}/{repo}/contents/{path}?ref={ref} --jq '.content'",
    )
    if rc != 0:
        return f"❌ Failed to read `{path}`: {out}"

    import base64
    try:
        content = base64.b64decode(out).decode("utf-8", errors="replace")
    except Exception:
        content = out

    if len(content) > MAX_OUTPUT:
        content = content[:MAX_OUTPUT] + f"\n... (truncated, {len(content)} total chars)"

    return f"📄 `{path}` (from `{GH_OWNER}/{repo}`):\n```\n{content}\n```"


# ---------------------------------------------------------------------------
# codegen_delete_file
# ---------------------------------------------------------------------------
@tool(
    "codegen_delete_file",
    "Delete a file from a GitHub repository.",
    {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository name.",
            },
            "path": {
                "type": "string",
                "description": "File path to delete.",
            },
            "commit_message": {
                "type": "string",
                "description": "Commit message.",
            },
            "branch": {
                "type": "string",
                "description": "Branch (default: default branch).",
            },
        },
        "required": ["repo", "path"],
    },
)
async def codegen_delete_file(
    guild: Guild,
    repo: str,
    path: str,
    commit_message: str = None,
    branch: str = None,
    **kwargs,
) -> str:
    if not is_owner(kwargs.get("user_id", "")):
        return "⛔ codegen tools are owner-only."

    # Get file SHA first
    ref_flag = f"?ref={branch}" if branch else ""
    rc, out = await _gh(
        f"api /repos/{GH_OWNER}/{repo}/contents/{path}{ref_flag} --jq '.sha'",
    )
    if rc != 0:
        return f"❌ File not found: {out}"

    sha = out.strip()
    msg = commit_message or f"Delete {path}"
    branch_json = f', "branch": "{branch}"' if branch else ""
    payload = f'{{"message": "{msg}", "sha": "{sha}"{branch_json}}}'

    rc, out = await _gh(
        f"api -X DELETE /repos/{GH_OWNER}/{repo}/contents/{path} --input - <<< '{payload}'",
    )
    if rc != 0:
        return f"❌ Delete failed: {out}"

    return f"✅ Deleted `{path}` from `{GH_OWNER}/{repo}`."

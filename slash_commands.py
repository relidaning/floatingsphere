"""slash_commands: what `/` offers in a session, for the web chat's command picker.

Claude Code doesn't publish its command list anywhere, so this rebuilds it from the same
places it reads: its built-ins (a fixed list, kept by hand), user and project skills
(`skills/<name>/SKILL.md`) and custom commands (`commands/**/*.md`, a subdirectory
becomes `dir:name`), the synced org skills (`anthropic-skills:<name>`) and the skills and
commands of enabled plugins (`<plugin>:<name>`). Descriptions come from the frontmatter,
or a command file's first line. A skill with `user-invocable: false` isn't offered.
"""
import glob
import json
import os
import time

import yaml

CLAUDE_DIR = os.path.expanduser("~/.claude")
CACHE_S = 10
DESC_MAX = 160

# Claude Code's own commands, including its bundled skills. Update by hand when it adds some.
BUILTIN = [
    ("add-dir", "Add a working directory"),
    ("agents", "Manage agent configurations"),
    ("claude-api", "Claude API / Anthropic SDK reference"),
    ("clear", "Clear the conversation history"),
    ("code-review", "Review the current diff for bugs"),
    ("compact", "Compact the conversation, with optional focus instructions"),
    ("config", "Open settings"),
    ("context", "Show context usage"),
    ("cost", "Show the session's cost and duration"),
    ("doctor", "Check the installation"),
    ("exit", "Exit Claude Code"),
    ("export", "Export the conversation"),
    ("fast", "Toggle fast mode"),
    ("fewer-permission-prompts", "Add an allowlist for common read-only calls"),
    ("help", "Show help"),
    ("hooks", "Manage hooks"),
    ("ide", "Manage IDE integrations"),
    ("init", "Create a CLAUDE.md for this codebase"),
    ("login", "Sign in"),
    ("logout", "Sign out"),
    ("loop", "Run a prompt on a recurring interval"),
    ("mcp", "Manage MCP servers"),
    ("memory", "Edit memory files"),
    ("model", "Set the model"),
    ("permissions", "Manage tool permissions"),
    ("plugin", "Manage plugins"),
    ("pr-comments", "Get comments from a GitHub pull request"),
    ("release-notes", "Show release notes"),
    ("resume", "Resume a conversation"),
    ("rewind", "Rewind the conversation and/or code"),
    ("run", "Launch the app to see a change working"),
    ("schedule", "Manage scheduled cloud agents"),
    ("security-review", "Security review of the pending changes"),
    ("simplify", "Clean up the changed code"),
    ("status", "Show version, model, account and connectivity"),
    ("statusline", "Set up the status line"),
    ("terminal-setup", "Set up terminal key bindings"),
    ("todos", "Show the todo list"),
    ("usage", "Show plan usage limits"),
    ("vim", "Toggle vim editing mode"),
]

_cache = {}  # cwd -> (time, list)


def _frontmatter(path):
    """(frontmatter dict, first body line) of a markdown file; ({}, "") if unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(16384)
    except OSError:
        return {}, ""
    meta, body = {}, text
    if text.startswith("---"):
        head, sep, rest = text[3:].partition("\n---")
        if sep:
            try:
                meta = yaml.safe_load(head) or {}
            except yaml.YAMLError:
                meta = {}
            body = rest.partition("\n")[2]
    if not isinstance(meta, dict):
        meta = {}
    first = next((ln.strip().lstrip("#").strip() for ln in body.splitlines() if ln.strip()), "")
    return meta, first


def _desc(meta, first=""):
    d = meta.get("description") or first or ""
    d = " ".join(str(d).split())
    return d if len(d) <= DESC_MAX else d[:DESC_MAX - 1].rstrip() + "…"


def _skills(root, prefix, src):
    out = []
    for p in sorted(glob.glob(os.path.join(root, "*", "SKILL.md"))):
        meta, _ = _frontmatter(p)
        if meta.get("user-invocable") is False:
            continue
        name = str(meta.get("name") or os.path.basename(os.path.dirname(p)))
        out.append({"name": prefix + name, "desc": _desc(meta), "src": src})
    return out


def _commands(root, prefix, src):
    out = []
    for p in sorted(glob.glob(os.path.join(root, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(p, root)[:-3]
        meta, first = _frontmatter(p)
        out.append({"name": prefix + rel.replace(os.sep, ":"), "desc": _desc(meta, first), "src": src})
    return out


def _plugins():
    try:
        with open(os.path.join(CLAUDE_DIR, "settings.json")) as f:
            enabled = {k for k, v in (json.load(f).get("enabledPlugins") or {}).items() if v}
        with open(os.path.join(CLAUDE_DIR, "plugins", "installed_plugins.json")) as f:
            installed = json.load(f).get("plugins") or {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for key in sorted(enabled):
        for inst in installed.get(key) or []:
            path = inst.get("installPath")
            if not path:
                continue
            pre = key.split("@")[0] + ":"
            out += _commands(os.path.join(path, "commands"), pre, "plugin")
            out += _skills(os.path.join(path, "skills"), pre, "plugin")
            break
    return out


def list_commands(cwd):
    """[{name, desc, src}] for a session in `cwd`, without the leading slash, sorted by name.
    Project entries shadow user ones, which shadow built-ins, as in Claude Code."""
    hit = _cache.get(cwd)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    found = [{"name": n, "desc": d, "src": "built-in"} for n, d in BUILTIN]
    found += _skills(os.path.join(CLAUDE_DIR, "skills", "synced", "*"), "anthropic-skills:", "org")
    found += _plugins()
    found += _skills(os.path.join(CLAUDE_DIR, "skills"), "", "user")
    found += _commands(os.path.join(CLAUDE_DIR, "commands"), "", "user")
    if cwd:
        found += _skills(os.path.join(cwd, ".claude", "skills"), "", "project")
        found += _commands(os.path.join(cwd, ".claude", "commands"), "", "project")
    by_name = {c["name"]: c for c in found}  # later sources win
    cmds = sorted(by_name.values(), key=lambda c: c["name"])
    _cache[cwd] = (time.monotonic(), cmds)
    return cmds

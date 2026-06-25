# WentianCode (文天) `=^_^=`

A terminal AI coding assistant built from scratch — streaming multi-model REPL with Agent Loop, permission system, MCP client, context compression, long-term memory, and a Skills system.

> **Personal research & learning project.** This is built for exploration and hands-on learning — not a polished product. It ships new features every week and the API/config may change without notice. Use at your own discretion.

---

## What it is

WentianCode (`wentian` / `wt`) is a from-scratch Python implementation of a Claude Code–style terminal AI assistant. It started as "what would it take to build this?" and grew version by version into a full coding agent — with 1700+ tests and a complete [spec-driven development history](./spec/README.md).

Each version adds a distinct capability layer:

| Version | Capability |
|---------|-----------|
| v0.1 | Streaming multi-turn REPL, Anthropic + OpenAI-compat backends, extended thinking, Markdown rendering, session persistence |
| v0.2 | Claude Code–style UX: banner, backend selector, multi-line input, status bar, blink animation, Esc interrupt |
| v0.3 | Tool system — read/write/edit files, run shell commands, find files, search content |
| v0.4 | Agent Loop — ReAct multi-round cycling, 5 stop conditions, parallel read-only tools, `/plan` + `/do` workflow |
| v0.5 | Structured system prompt (7 pluggable modules) + personality + prompt caching + `<system-reminder>` channel |
| v0.6 | Five-layer permission system: blacklist → path sandbox → rule engine → mode fallback → human-in-the-loop |
| v0.7 | MCP client — JSON-RPC 2.0, stdio + Streamable HTTP, multi-server lifecycle, namespace isolation |
| v0.8 | Context compression: large tool-result offload + LLM-based structured summary; `/compact`, circuit breaker |
| v0.9 | Memory & sessions: 3-layer `WENTIAN.md` instruction files, per-cwd JSONL sessions, background memory extraction |
| v0.10 | Slash command system: registry + parser + dispatcher, Tab completion, 13 built-in commands |
| v0.11 | Skills system: reusable AI ops as frontmatter+Markdown files, shared/isolated modes, 3-layer discovery |
| v0.12 | Hook lifecycle: 10 events, 4 action types (shell/prompt/http/notify), declarative YAML config |
| v0.13 | Sub-agent delegation: named agent definitions, foreground/background execution with auto-escalation |
| v0.14 | Thinking animation (replaces raw chain-of-thought display), custom Markdown theme |

---

## Installation

**Requirements:** Python ≥ 3.11, [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/<your-username>/wentiancode.git
cd wentiancode
uv sync
uv run wentian --help
```

Or install as a tool:

```bash
uv tool install .
wentian --help   # or: wt --help
```

---

## Configuration

Create `~/.config/wentian/config.yaml`:

```yaml
default: claude

providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-...
    thinking: true

  deepseek:
    protocol: openai
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: sk-...

# Optional: MCP servers
mcpServers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

**Config files (all optional, merged deepest-wins):**

| File | Scope |
|------|-------|
| `~/.config/wentian/config.yaml` | User-level providers + MCP |
| `<project>/.wentian/config.yaml` | Project-level overrides |
| `~/.config/wentian/settings.yaml` | User permission rules |
| `<project>/.wentian/settings.yaml` | Project permission rules |
| `<project>/WENTIAN.md` | Project instructions (injected into system prompt) |
| `~/.config/wentian/WENTIAN.md` | Global user instructions |

---

## Usage

```bash
wentian                    # start (shows backend selector if multiple configured)
wentian -p deepseek        # use a specific provider
wentian --continue         # resume the most recent session
wentian --resume <id>      # resume a specific session by id
```

**During a session:**

| Command | What it does |
|---------|-------------|
| `/help` | Show all commands |
| `/clear` | Clear context |
| `/compact` | Manually compress context |
| `/plan` | Enter plan mode (read-only tools) |
| `/do [msg]` | Exit plan mode and execute |
| `/session` | List / resume / new sessions |
| `/memory` | Show long-term memory index |
| `/permission` | Show / change permission mode |
| `/status` | Token usage and session info |
| `/skills` | List Skills; `/skills reload` to hot-reload |
| `/agents` | List background agent tasks |
| `/exit` | Exit |

`Shift+Tab` cycles permission modes: `default → acceptEdits → plan → bypassPermissions`

---

## Permission system

Every tool call passes through a 5-layer pipeline before execution:

1. **Blacklist** — blocks known dangerous patterns (`rm -rf /`, fork bombs…), cannot be bypassed
2. **Path sandbox** — file operations confined to the project root
3. **Rule engine** — declarative allow/deny rules with glob matching
4. **Mode fallback** — per-category defaults when no rule matches
5. **Human-in-the-loop** — interactive 3-choice prompt on `Ask`

| Mode | Read-only | File write | Shell |
|------|-----------|------------|-------|
| `default` | auto | ask | ask |
| `acceptEdits` | auto | auto | ask |
| `plan` | auto | ask | ask |
| `bypassPermissions` | auto | auto | auto |

---

## Skills

Skills are reusable AI operations — Markdown files with YAML frontmatter:

```markdown
---
name: commit
description: Stage changes and write a conventional commit
mode: shared
allowed_tools: [run_command, read_file]
---
Stage relevant changes and write a conventional commit message. $ARGUMENTS
```

Place them in `.wentian/skills/` (project) or `~/.config/wentian/skills/` (user). Built-ins: `commit`, `review`, `test`.

---

## Development

```bash
uv sync --dev
uv run pytest          # 1700+ tests, no external services required
uv run ruff check src/
```

The full design history lives in [`spec/`](./spec/) — every feature was spec'd before code was written.

---

## License

MIT — see [LICENSE](./LICENSE).

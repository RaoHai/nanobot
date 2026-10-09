# Common Gotchas

## Do not use `ruff format`

`CONTRIBUTING.md` mentions `ruff format`, but **do not run it** — it destroys git blame history. Only `ruff check` should be used.

## Config `${VAR}` References

`config/loader.py` resolves `${VAR}` patterns in `config.json` at load time. This is **not** a shell-like default-value syntax. If the environment variable is missing, `load_config` raises `ValueError` and the agent falls back to default configuration.

Example valid usage:
```json
{ "providers": { "openrouter": { "apiKey": "${OPENROUTER_KEY}" } } }
```

## Windows Compatibility

nanobot explicitly supports Windows. Key differences to keep in mind:
- `ExecTool` defaults to PowerShell on Windows (`pwsh` when available, otherwise Windows PowerShell); pass `shell="cmd"` for cmd.exe syntax or cmd built-ins (`shell.py`).
- `cli/commands.py` forces `sys.stdout`/`stderr` to UTF-8 on startup to handle emoji and multilingual input.
- MCP stdio server commands are normalized for Windows path separators (`mcp.py`).
- Always use `pathlib.Path` for path manipulation; do not assume `/` separators.

## Prompt Templates

Agent system prompts and scenario-specific instructions live in `nanobot/templates/` as Jinja2 markdown files (`identity.md`, `platform_policy.md`, `HEARTBEAT.md`, `SOUL.md`, etc.). Changing these files alters agent behavior as directly as changing Python code. They are loaded by `utils/prompt_templates.py`.

Tool descriptions, skills, and replayed session history also shape model behavior. Treat changes to those surfaces like runtime code: keep them narrow, add a focused regression test when possible, and avoid teaching the model to repeat internal markers, local paths, or tool-call text.

## Context Pollution Persists

Anything written into memory, session history, or prompt inputs can be replayed into future LLM calls. Metadata such as timestamps, local media paths, tool-call echoes, and raw fallback dumps must be bounded and sanitized before they become examples for the model to imitate.

## Skills as Extension Point

Built-in skills live in `nanobot/skills/` (markdown + YAML frontmatter format). Agent capabilities that are "know-how" rather than code should be added as skills, not hardcoded into the agent loop. External skills can be published to and installed from ClawHub.

## Atomic Session Writes

`agent/memory.py` writes `history.jsonl` atomically (temp file + fsync + rename + directory fsync). This guarantees durability across crashes. Do not replace this with a plain `open(..., "w")` write.

## [LOCAL] Silent-Marker Suppression (not upstream)

Models signal "say nothing" by emitting a marker (`[SILENT]`, `[NO_RESPONSE]`, `[NO_REPLY]`, `[SKIP]`) as the entire message. Upstream HKUDS/nanobot has no such handling — this is a local customization that has been lost to upstream merges twice. Keep all three layers when merging:

1. **Matcher**: `nanobot/utils/silent_markers.py` (`is_silent_marker`, `is_silent_marker_prefix`). Tolerant: trims whitespace, strips one pair of markdown emphasis/brackets, ignores case; only whole-message markers match.
2. **Source suppression**: `agent/turn_delivery.py` holds back stream deltas while the accumulated text is still a possible marker prefix (pure-marker turns publish nothing); `agent/loop.py` `_assemble_outbound` returns `None` for marker finals.
3. **Safety net**: `channels/manager.py` `_should_drop_silent` drops marker-content messages in `_dispatch_outbound`, including stream-event-wrapped ones (deltas/streamed finals leak past older versions of this check — do not re-add the "any event → keep" early return).

Regression tests: `tests/channels/test_channel_manager_silent_marker.py`, `tests/agent/test_turn_delivery_silent_holdback.py`.

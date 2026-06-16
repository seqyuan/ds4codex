# Changelog

## [0.1.6] — 2025-06-16

### Tool Call Fixes (Responses ↔ Chat Completions translation)

This release fixes several critical tool-call issues discovered by comparing
`ds4codex` against the more mature `annodex` translation layer.  Without these
fixes, multi-turn tool use (especially with streaming) would silently fail or
produce incomplete results.

- **Inject `reasoning_content` placeholder for DeepSeek tool calls**
  DeepSeek's Chat Completions API rejects assistant messages that carry
  `tool_calls` but lack `reasoning_content`. Every `function_call` and
  `custom_tool_call` item now includes `"reasoning_content": "tool call"`,
  and a post-processing pass (`_ensure_tool_reasoning`) guards any remaining
  messages that carry tool calls without reasoning content.

- **Handle streaming tool-call deltas**
  The streaming translator (`translate_stream`) now processes
  `delta.tool_calls` incrementally, emitting
  `response.output_item.added` / `response.function_call_arguments.delta`
  / `.done` events for each tool call as arguments arrive.  Previously the
  translator only forwarded text deltas and could not relay tool calls in
  streaming mode.

- **Inline `<think>` tag parsing in stream content**
  Providers that embed reasoning inside `<think>…</think>` tags (instead of
  using the `reasoning_content` delta field) are now supported via the new
  `_process_stream_content_delta` helper.

- **Namespace tool expansion**
  `_translate_tool` now handles `type: "namespace"` tool definitions (used
  by MCP servers) and flattens them into `namespace__tool` entries that
  Chat Completions APIs can consume.

- **`custom_tool_call` / `custom_tool_call_output` input items**
  The input translator now maps these Codex item types into the standard
  assistant `tool_calls` / `tool` message format, preventing silent drops
  when Codex sends custom or search tool calls.

### Other

- Expanded test suite from 3 to 13 tests covering reasoning_content
  injection, custom tool calls, namespace expansion, and think-tag parsing.

## [0.1.5] — 2025-05-20

- Add `--port` option to `ds4codex init`.

## [0.1.4] — 2025-05-20

- Map Responses-style function tools to nested Chat Completions `function`
  shape that DeepSeek expects.

## [0.1.3] — 2025-05-20

- Normalize Codex `developer` role to DeepSeek-compatible `system` role.

## [0.1.2] — 2025-05-20

- Fall back to built-in model template when local `codex` binary is not
  executable.

## [0.1.1] — 2025-05-20

- Fix workflow secret gating and PyPI publish workflow.

## [0.1.0] — 2025-05-20

- Initial release.

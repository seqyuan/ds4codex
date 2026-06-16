"""Responses API to Chat Completions proxy."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from .config import ProxySettings


log = logging.getLogger("ds4codex")
PLACEHOLDER_TOKENS = {
    "",
    "local-proxy",
    "changeme",
    "your-api-key",
    "sk-your-deepseek-api-key",
}


def responses_to_chat(body: dict[str, Any], *, default_thinking: str) -> dict[str, Any]:
    """Translate a Responses API request body into Chat Completions format."""
    chat: dict[str, Any] = {"model": body.get("model", "deepseek-v4-flash")}

    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions", "")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            translated = _translate_input_item(item)
            if translated is not None:
                messages.append(translated)
    elif isinstance(raw_input, dict):
        translated = _translate_input_item(raw_input)
        if translated is not None:
            messages.append(translated)

    chat["messages"] = _ensure_tool_reasoning(messages)

    passthrough = {
        "temperature": "temperature",
        "top_p": "top_p",
        "max_output_tokens": "max_tokens",
        "stream": "stream",
        "stop": "stop",
        "frequency_penalty": "frequency_penalty",
        "presence_penalty": "presence_penalty",
        "response_format": "response_format",
    }
    for source_key, target_key in passthrough.items():
        if source_key in body:
            chat[target_key] = body[source_key]

    tools = body.get("tools", [])
    if tools:
        translated_tools: list[dict[str, Any]] = []
        for tool in tools:
            result = _translate_tool(tool)
            if isinstance(result, list):
                translated_tools.extend(result)
            elif result is not None:
                translated_tools.append(result)
        chat["tools"] = translated_tools

    if "tool_choice" in body:
        chat["tool_choice"] = _translate_tool_choice(body["tool_choice"])

    thinking_payload, reasoning_effort = _resolve_thinking_controls(body.get("reasoning"), default_thinking)
    chat["thinking"] = thinking_payload
    if reasoning_effort is not None:
        chat["reasoning_effort"] = reasoning_effort

    return chat


def chat_to_responses(chat_resp: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate a non-streaming Chat Completions response into Responses API format."""
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    output: list[dict[str, Any]] = []
    choices = chat_resp.get("choices", [])

    if choices:
        message = choices[0].get("message", {})
        text = message.get("content") or message.get("reasoning_content", "")
        if text:
            output.append(
                {
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            )

        for tool_call in message.get("tool_calls") or []:
            output.append(
                {
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": tool_call.get("id", ""),
                    "name": tool_call.get("function", {}).get("name", ""),
                    "arguments": tool_call.get("function", {}).get("arguments", ""),
                }
            )

    usage = chat_resp.get("usage", {})
    response_usage: dict[str, Any] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }

    details = usage.get("completion_tokens_details", {})
    if "reasoning_tokens" in details:
        response_usage["output_tokens_details"] = {"reasoning_tokens": details["reasoning_tokens"]}

    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "usage": response_usage,
    }


async def translate_stream(source: Any, write: Any, model: str) -> None:
    """Translate Chat Completions SSE chunks into Responses API SSE events.

    Handles streaming deltas for text, reasoning_content, and tool_calls,
    including inline <think> tag parsing and incremental tool-call argument
    accumulation.
    """
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    reasoning_id = f"rs_{response_id}"
    created_at = int(time.time())

    empty_response = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "in_progress",
        "model": model,
        "output": [],
    }

    await _write_event(write, "response.created", {"type": "response.created", "response": empty_response})
    await _write_event(
        write,
        "response.in_progress",
        {"type": "response.in_progress", "response": empty_response},
    )

    # Streaming state
    full_text = ""
    reasoning_text = ""
    final_usage: dict[str, Any] | None = None
    text_item_started = False
    text_content_started = False
    reasoning_started = False
    reasoning_summary_started = False
    output_index: int = 0

    # Inline <think> tag state (for providers that embed reasoning in content)
    inline_think_mode = "detecting"  # "detecting" | "reasoning" | "text"
    inline_think_buffer = ""

    # Tool call accumulation: keyed by tool call index
    tool_call_state: dict[int, dict[str, Any]] = {}

    buffer = ""
    finish_reason: str | None = None

    async for chunk, _ in source:
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()

            if not line or line.startswith(":"):
                continue
            if line == "data: [DONE]":
                buffer = ""
                break
            if not line.startswith("data: "):
                continue

            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            choices = data.get("choices", [])
            if not choices:
                if "usage" in data:
                    final_usage = data["usage"]
                continue

            choice = choices[0]
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
            if "usage" in data:
                final_usage = data["usage"]

            delta = choice.get("delta", {})

            # --- Reasoning content (explicit reasoning_content / reasoning field) ---
            rc_delta = delta.get("reasoning_content") or delta.get("reasoning")
            if rc_delta:
                if not reasoning_started:
                    reasoning_started = True
                    await _write_event(write, "response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": output_index,
                        "item": {
                            "id": reasoning_id,
                            "type": "reasoning",
                            "status": "in_progress",
                            "reasoning_content": "",
                            "summary": [],
                        },
                    })
                    output_index += 1
                if not reasoning_summary_started:
                    reasoning_summary_started = True
                    await _write_event(write, "response.reasoning_summary_part.added", {
                        "type": "response.reasoning_summary_part.added",
                        "item_id": reasoning_id,
                        "output_index": 0,
                        "summary_index": 0,
                        "part": {"type": "summary_text", "text": ""},
                    })
                reasoning_text += rc_delta
                await _write_event(write, "response.reasoning_summary_text.delta", {
                    "type": "response.reasoning_summary_text.delta",
                    "item_id": reasoning_id,
                    "output_index": 0,
                    "summary_index": 0,
                    "delta": rc_delta,
                })

            # --- Content delta (may contain inline <think> tags) ---
            content_delta = delta.get("content") or ""
            if content_delta:
                text_deltas, reasoning_deltas, inline_think_mode, inline_think_buffer = (
                    _process_stream_content_delta(
                        content_delta, inline_think_mode, inline_think_buffer,
                    )
                )
                for rdelta in reasoning_deltas:
                    if not reasoning_started:
                        reasoning_started = True
                        await _write_event(write, "response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": output_index,
                            "item": {
                                "id": reasoning_id,
                                "type": "reasoning",
                                "status": "in_progress",
                                "reasoning_content": "",
                                "summary": [],
                            },
                        })
                        output_index += 1
                    if not reasoning_summary_started:
                        reasoning_summary_started = True
                        await _write_event(write, "response.reasoning_summary_part.added", {
                            "type": "response.reasoning_summary_part.added",
                            "item_id": reasoning_id,
                            "output_index": 0,
                            "summary_index": 0,
                            "part": {"type": "summary_text", "text": ""},
                        })
                    reasoning_text += rdelta
                    await _write_event(write, "response.reasoning_summary_text.delta", {
                        "type": "response.reasoning_summary_text.delta",
                        "item_id": reasoning_id,
                        "output_index": 0,
                        "summary_index": 0,
                        "delta": rdelta,
                    })
                for tdelta in text_deltas:
                    full_text += tdelta
                    if not text_item_started:
                        text_item_started = True
                        await _write_event(write, "response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": output_index,
                            "item": {
                                "id": message_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            },
                        })
                        output_index += 1
                    if not text_content_started:
                        text_content_started = True
                        await _write_event(write, "response.content_part.added", {
                            "type": "response.content_part.added",
                            "item_id": message_id,
                            "output_index": 0,
                            "content_index": 0,
                            "part": {"type": "output_text", "text": "", "annotations": []},
                        })
                    await _write_event(write, "response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": tdelta,
                    })

            # --- Tool call deltas ---
            tc_deltas = delta.get("tool_calls")
            if tc_deltas:
                for tc in tc_deltas:
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index", 0)
                    if idx not in tool_call_state:
                        call_id = tc.get("id", f"call_{idx}")
                        fn = tc.get("function", {})
                        name = fn.get("name", "unknown_tool") if isinstance(fn, dict) else "unknown_tool"
                        item_id = f"fc_{call_id}"
                        tool_call_state[idx] = {
                            "call_id": call_id,
                            "name": name,
                            "arguments": "",
                            "item_id": item_id,
                            "output_index": output_index,
                        }
                        output_index += 1
                        await _write_event(write, "response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": tool_call_state[idx]["output_index"],
                            "item": {
                                "id": item_id,
                                "type": "function_call",
                                "status": "in_progress",
                                "call_id": call_id,
                                "name": name,
                                "arguments": "",
                            },
                        })
                    else:
                        tc_id = tc.get("id")
                        if tc_id:
                            tool_call_state[idx]["call_id"] = tc_id
                            tool_call_state[idx]["item_id"] = f"fc_{tc_id}"
                        fn = tc.get("function", {})
                        if isinstance(fn, dict):
                            tc_name = fn.get("name")
                            if tc_name:
                                tool_call_state[idx]["name"] = tc_name
                            tc_args = fn.get("arguments", "")
                            if tc_args:
                                tool_call_state[idx]["arguments"] += tc_args
                                await _write_event(write, "response.function_call_arguments.delta", {
                                    "type": "response.function_call_arguments.delta",
                                    "item_id": tool_call_state[idx]["item_id"],
                                    "output_index": tool_call_state[idx]["output_index"],
                                    "delta": tc_args,
                                })

    # Flush inline think buffer on stream end
    if inline_think_buffer:
        if inline_think_mode == "reasoning" and inline_think_buffer:
            reasoning_text += inline_think_buffer
        elif inline_think_mode == "text" and inline_think_buffer:
            full_text += inline_think_buffer

    # --- Finalize: reasoning ---
    if reasoning_started and reasoning_text:
        await _write_event(write, "response.reasoning_summary_text.done", {
            "type": "response.reasoning_summary_text.done",
            "item_id": reasoning_id,
            "output_index": 0,
            "summary_index": 0,
            "text": reasoning_text,
        })
        await _write_event(write, "response.reasoning_summary_part.done", {
            "type": "response.reasoning_summary_part.done",
            "item_id": reasoning_id,
            "output_index": 0,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": reasoning_text},
        })
        await _write_event(write, "response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": reasoning_id,
                "type": "reasoning",
                "reasoning_content": reasoning_text,
                "summary": [{"type": "summary_text", "text": reasoning_text}],
            },
        })

    # --- Finalize: text message ---
    output_items: list[dict[str, Any]] = []
    if reasoning_started and reasoning_text:
        output_items.append({
            "id": reasoning_id,
            "type": "reasoning",
            "reasoning_content": reasoning_text,
            "summary": [{"type": "summary_text", "text": reasoning_text}],
        })

    if text_item_started:
        completed_message = {
            "id": message_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text, "annotations": []}],
        }
        await _write_event(write, "response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": 1 if reasoning_started else 0,
            "item": completed_message,
        })
        if full_text:
            await _write_event(write, "response.content_part.done", {
                "type": "response.content_part.done",
                "item_id": message_id,
                "output_index": 1 if reasoning_started else 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": full_text, "annotations": []},
            })
        output_items.append(completed_message)

    # --- Finalize: tool calls ---
    sorted_tcs = sorted(tool_call_state.values(), key=lambda tc: tc["output_index"])
    for tc in sorted_tcs:
        args = tc["arguments"]
        await _write_event(write, "response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "item_id": tc["item_id"],
            "output_index": tc["output_index"],
            "arguments": args,
        })
        tc_item = {
            "id": tc["item_id"],
            "type": "function_call",
            "status": "completed",
            "call_id": tc["call_id"],
            "name": tc["name"],
            "arguments": args,
        }
        output_items.append(tc_item)
        await _write_event(write, "response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": tc["output_index"],
            "item": tc_item,
        })

    # --- response.completed ---
    status = "incomplete" if finish_reason == "length" else "completed"
    response_usage: dict[str, Any] = {}
    if final_usage:
        response_usage = {
            "input_tokens": final_usage.get("prompt_tokens", 0),
            "output_tokens": final_usage.get("completion_tokens", 0),
            "total_tokens": final_usage.get("total_tokens", 0),
        }

    completed_response: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": model,
        "output": output_items,
        "usage": response_usage,
    }
    if status == "incomplete":
        completed_response["incomplete_details"] = {"reason": "max_output_tokens"}

    await _write_event(write, "response.completed", {
        "type": "response.completed",
        "response": completed_response,
    })


def _process_stream_content_delta(
    delta_text: str,
    mode: str,
    buffer: str,
) -> tuple[list[str], list[str], str, str]:
    """Parse inline <think> tags from a stream content delta.

    Returns (text_deltas, reasoning_deltas, new_mode, new_buffer).
    """
    text_deltas: list[str] = []
    reasoning_deltas: list[str] = []

    buffer += delta_text

    while buffer:
        if mode == "detecting":
            stripped = buffer.lstrip()
            if not stripped:
                break
            if stripped.startswith("<think>"):
                mode = "reasoning"
                buffer = stripped[len("<think>"):]
            elif "<think>".startswith(stripped):
                break  # partial tag, wait for more
            else:
                mode = "text"
        elif mode == "reasoning":
            close_pos = buffer.find("</think>")
            if close_pos == -1:
                if buffer:
                    reasoning_deltas.append(buffer)
                    buffer = ""
                break
            else:
                if close_pos > 0:
                    reasoning_deltas.append(buffer[:close_pos])
                post = buffer[close_pos + len("</think>"):]
                mode = "text"
                buffer = post
        elif mode == "text":
            close_pos = buffer.find("<think>")
            if close_pos == -1:
                partial = buffer.rfind("<")
                if partial != -1 and "<think>".startswith(buffer[partial:]):
                    if partial > 0:
                        text_deltas.append(buffer[:partial])
                    buffer = buffer[partial:]
                    mode = "detecting"
                    break
                elif buffer:
                    text_deltas.append(buffer)
                    buffer = ""
                break
            else:
                if close_pos > 0:
                    text_deltas.append(buffer[:close_pos])
                buffer = buffer[close_pos + len("<think>"):]
                mode = "reasoning"

    return text_deltas, reasoning_deltas, mode, buffer

async def handle_responses(request: web.Request) -> web.StreamResponse:
    """Handle POST /v1/responses requests."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    settings: ProxySettings = request.app["settings"]
    api_key, auth_source = resolve_upstream_api_key(request, settings)
    if not api_key:
        return web.json_response(
            {
                "error": (
                    "No upstream API key configured. Set DS4CODEX_API_KEY, "
                    f"set {settings.api_key_env}, pass --api-key, or put the real key in ~/.codex/config.toml "
                    "and let ds4codex forward the incoming Bearer token."
                )
            },
            status=401,
        )

    model = body.get("model", "deepseek-v4-flash")
    is_stream = body.get("stream", False)

    try:
        chat_body = responses_to_chat(body, default_thinking=settings.thinking)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)

    if is_stream:
        chat_body.setdefault("stream_options", {})["include_usage"] = True

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    timeout = ClientTimeout(total=settings.request_timeout)
    log.info("proxy request model=%s stream=%s auth=%s target=%s", model, is_stream, auth_source, settings.target_url)

    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(settings.target_url, json=chat_body, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return web.Response(
                        status=response.status,
                        text=error_text,
                        content_type="application/json",
                    )

                if is_stream:
                    stream = web.StreamResponse(
                        status=200,
                        headers={
                            "Content-Type": "text/event-stream",
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                        },
                    )
                    await stream.prepare(request)

                    async def write(payload: str) -> None:
                        await stream.write(payload.encode("utf-8"))

                    await translate_stream(response.content.iter_chunks(), write, model)
                    await stream.write_eof()
                    return stream

                payload = await response.json()
                return web.json_response(chat_to_responses(payload, model))
    except Exception as exc:
        log.exception("upstream request failed")
        return web.json_response({"error": str(exc)}, status=502)


async def health(request: web.Request) -> web.Response:
    """Return a small health payload."""
    settings: ProxySettings = request.app["settings"]
    return web.json_response(
        {
            "status": "ok",
            "target": settings.target_url,
            "thinking": settings.thinking,
            "auth_sources": _available_auth_sources(settings),
        }
    )


def create_app(settings: ProxySettings) -> web.Application:
    """Build the aiohttp application."""
    app = web.Application()
    app["settings"] = settings
    app.router.add_post("/v1/responses", handle_responses)
    app.router.add_get("/health", health)
    return app


def run_proxy(settings: ProxySettings, *, log_level: str = "INFO") -> None:
    """Run the aiohttp proxy server."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("starting ds4codex host=%s port=%s target=%s", settings.host, settings.port, settings.target_url)
    web.run_app(create_app(settings), host=settings.host, port=settings.port)


def resolve_upstream_api_key(request: web.Request, settings: ProxySettings) -> tuple[str, str]:
    """Resolve the upstream API key for a single request."""
    if settings.static_api_key and not _is_placeholder_token(settings.static_api_key):
        return settings.static_api_key, "proxy.api_key"

    env_api_key = os.environ.get("DS4CODEX_API_KEY")
    if env_api_key and not _is_placeholder_token(env_api_key):
        return env_api_key, "DS4CODEX_API_KEY"

    if settings.api_key_env:
        configured_env_key = os.environ.get(settings.api_key_env)
        if configured_env_key and not _is_placeholder_token(configured_env_key):
            return configured_env_key, settings.api_key_env

    bearer_token = _parse_bearer_token(request.headers.get("Authorization", ""))
    if bearer_token and not _is_placeholder_token(bearer_token):
        return bearer_token, "incoming.Authorization"

    return "", "missing"


def _ensure_tool_reasoning(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DeepSeek requires reasoning_content on assistant messages that carry tool_calls.

    Some providers reject assistant tool-call messages without reasoning_content.
    This injects a minimal placeholder so multi-turn tool use works.
    """
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            if not msg.get("reasoning_content", "").strip():
                msg["reasoning_content"] = "tool call"
    return messages


def _translate_input_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {"role": "user", "content": item}

    if not isinstance(item, dict):
        return None

    item_type = item.get("type", "message")
    if item_type == "message":
        role = _normalize_chat_role(item.get("role", "user"))
        content = _content_to_text(item.get("content", ""))
        return {"role": role, "content": content}

    if item_type == "function_call":
        return {
            "role": "assistant",
            "content": None,
            "reasoning_content": "tool call",
            "tool_calls": [
                {
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                }
            ],
        }

    if item_type == "custom_tool_call":
        input_val = item.get("input", "")
        if not isinstance(input_val, str):
            input_val = json.dumps(input_val, ensure_ascii=False)
        return {
            "role": "assistant",
            "content": None,
            "reasoning_content": "tool call",
            "tool_calls": [
                {
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": json.dumps({"input": input_val}),
                    },
                }
            ],
        }

    if item_type in ("function_call_output", "custom_tool_call_output"):
        call_id = item.get("call_id", "")
        output = item.get("output", "")
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        }

    return None


def _translate_tool(tool: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Translate a Responses API tool definition into Chat Completions format.

    Returns a single tool dict, a list of tool dicts (for namespace expansion),
    or None if the tool cannot be translated.
    """
    if not isinstance(tool, dict):
        return None

    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        return tool

    if tool.get("type") == "namespace":
        namespace = tool.get("name", "")
        children = tool.get("tools", [])
        if not children or not isinstance(children, list):
            return None
        results: list[dict[str, Any]] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            fn = child.get("function", {}) if isinstance(child.get("function"), dict) else {}
            child_name = child.get("name") or fn.get("name")
            if not child_name:
                continue
            chat_name = f"{namespace}__{child_name}" if namespace else child_name
            child_desc = child.get("description") or fn.get("description", "")
            child_params = child.get("parameters") or fn.get("parameters") or child.get("input_schema") or {}
            results.append({
                "type": "function",
                "function": {
                    "name": chat_name,
                    "description": child_desc,
                    "parameters": child_params,
                },
            })
        return results if results else None

    function = {key: value for key, value in tool.items() if key != "type" and value is not None}
    if "name" not in function:
        return None

    return {"type": "function", "function": function}


def _translate_tool_choice(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return tool_choice

    choice_type = tool_choice.get("type")
    if choice_type in {"auto", "none", "required"}:
        return choice_type

    if choice_type == "function" and isinstance(tool_choice.get("function"), dict):
        return tool_choice

    if "name" in tool_choice:
        return {"type": "function", "function": {"name": tool_choice["name"]}}

    return tool_choice


def _normalize_chat_role(role: Any) -> str:
    """Map Responses roles to the Chat Completions roles DeepSeek accepts."""
    normalized = str(role or "user").strip().lower()
    if normalized == "developer":
        return "system"
    if normalized in {"system", "user", "assistant", "tool", "latest_reminder"}:
        return normalized
    return "user"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type and part_type.startswith("input_image"):
                continue
            text = part.get("text")
            if text:
                parts.append(str(text))
                continue
            if part_type == "input_text" and "text" in part:
                parts.append(str(part["text"]))
        return "\n".join(parts)

    if content is None:
        return ""

    return str(content)


def _resolve_thinking_controls(reasoning: Any, default_thinking: str) -> tuple[dict[str, Any], str | None]:
    """Map Codex reasoning controls to DeepSeek's thinking + reasoning_effort fields."""
    if reasoning and isinstance(reasoning, dict):
        if not reasoning.get("enabled", True):
            return {"type": "disabled"}, None
        return {"type": "enabled"}, _map_reasoning_effort(reasoning.get("effort"))

    normalized = default_thinking.strip().lower()
    if normalized in {"", "disabled", "off", "0", "false"}:
        return {"type": "disabled"}, None
    if normalized in {"enabled", "on", "1", "true"}:
        return {"type": "enabled"}, None
    return {"type": "enabled"}, _map_reasoning_effort(default_thinking)


def _map_reasoning_effort(value: Any) -> str | None:
    """DeepSeek documents only `high` and `max`; lower Codex values are collapsed for compatibility."""
    if value is None:
        return None

    normalized = str(value).strip().lower()
    if normalized in {"", "disabled", "off", "0", "false"}:
        return None
    if normalized in {"max", "xhigh"}:
        return "max"
    if normalized in {"minimal", "low", "medium", "high", "enabled", "on", "1", "true"}:
        return "high"
    return str(value)


def _parse_bearer_token(header_value: str) -> str:
    if not header_value:
        return ""
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _available_auth_sources(settings: ProxySettings) -> list[str]:
    sources: list[str] = ["incoming.Authorization"]
    if settings.static_api_key and not _is_placeholder_token(settings.static_api_key):
        sources.insert(0, "proxy.api_key")
    if os.environ.get("DS4CODEX_API_KEY"):
        sources.insert(0, "DS4CODEX_API_KEY")
    if settings.api_key_env and os.environ.get(settings.api_key_env):
        sources.insert(0, settings.api_key_env)
    return sources


def _is_placeholder_token(value: str) -> bool:
    return value.strip() in PLACEHOLDER_TOKENS


async def _write_event(write: Any, event: str, payload: dict[str, Any]) -> None:
    await write(f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n")

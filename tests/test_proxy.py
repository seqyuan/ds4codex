#!/usr/bin/env python3
"""Tests for ds4codex Responses ↔ Chat Completions translation."""

import unittest
import json
from ds4codex.proxy import (
    responses_to_chat,
    _translate_tool,
    _translate_input_item,
    _ensure_tool_reasoning,
    _process_stream_content_delta,
)


class ReasoningContentTests(unittest.TestCase):
    """P0-1: reasoning_content placeholder for DeepSeek tool calls."""

    def test_function_call_injects_reasoning(self) -> None:
        result = _translate_input_item({
            "type": "function_call",
            "call_id": "call_abc",
            "name": "exec_command",
            "arguments": '{"cmd":"ls"}',
        })
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["reasoning_content"], "tool call")
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "exec_command")

    def test_custom_tool_call_injects_reasoning(self) -> None:
        result = _translate_input_item({
            "type": "custom_tool_call",
            "call_id": "call_xyz",
            "name": "web_search",
            "input": "weather",
        })
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["reasoning_content"], "tool call")

    def test_ensure_tool_reasoning_post_pass(self) -> None:
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]},
        ]
        fixed = _ensure_tool_reasoning(messages)
        self.assertEqual(fixed[1]["reasoning_content"], "tool call")

    def test_ensure_tool_reasoning_keeps_existing(self) -> None:
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}],
             "reasoning_content": "I need to run a command."},
        ]
        fixed = _ensure_tool_reasoning(messages)
        self.assertEqual(fixed[0]["reasoning_content"], "I need to run a command.")


class CustomToolCallTests(unittest.TestCase):
    """P1-2: custom_tool_call and custom_tool_call_output support."""

    def test_custom_tool_call(self) -> None:
        result = _translate_input_item({
            "type": "custom_tool_call",
            "call_id": "call_1",
            "name": "web_search",
            "input": "test query",
        })
        fn = result["tool_calls"][0]["function"]
        self.assertEqual(fn["name"], "web_search")
        args = json.loads(fn["arguments"])
        self.assertEqual(args["input"], "test query")

    def test_custom_tool_call_output(self) -> None:
        result = _translate_input_item({
            "type": "custom_tool_call_output",
            "call_id": "call_1",
            "output": "search results here",
        })
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "call_1")
        self.assertEqual(result["content"], "search results here")

    def test_mixed_output_roundtrip(self) -> None:
        """Test: function_call + function_call_output roundtrip in messages."""
        chat = responses_to_chat({
            "model": "deepseek-v4-flash",
            "input": [
                {"type": "message", "role": "user", "content": "run pwd"},
                {"type": "function_call", "call_id": "c1", "name": "exec_command", "arguments": '{"cmd":"pwd"}'},
                {"type": "function_call_output", "call_id": "c1", "output": "/home/user"},
            ],
        }, default_thinking="disabled")
        msgs = chat["messages"]
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["reasoning_content"], "tool call")
        self.assertEqual(msgs[2]["role"], "tool")


class NamespaceToolTests(unittest.TestCase):
    """P1-1: namespace tool expansion."""

    def test_namespace_expands_to_flat_tools(self) -> None:
        result = _translate_tool({
            "type": "namespace",
            "name": "mcp",
            "tools": [
                {"name": "list_dir", "description": "List directory", "parameters": {"type": "object"}},
                {"name": "read_file", "description": "Read file", "parameters": {"type": "object"}},
            ],
        })
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["function"]["name"], "mcp__list_dir")
        self.assertEqual(result[1]["function"]["name"], "mcp__read_file")

    def test_namespace_preserves_description(self) -> None:
        result = _translate_tool({
            "type": "namespace",
            "name": "mcp",
            "tools": [{"name": "tool_a", "description": "Does A"}],
        })
        self.assertEqual(result[0]["function"]["description"], "Does A")

    def test_namespace_empty_children(self) -> None:
        result = _translate_tool({"type": "namespace", "name": "mcp", "tools": []})
        self.assertIsNone(result)


class StreamingToolsTests(unittest.TestCase):
    """P0-2: stream content delta parsing and think-tag handling."""

    def test_think_tag_extraction(self) -> None:
        text, reasoning, mode, buf = _process_stream_content_delta(
            "<think>I need to check</think>answer here", "detecting", ""
        )
        self.assertEqual(reasoning, ["I need to check"])
        self.assertEqual(text, ["answer here"])
        self.assertEqual(mode, "text")

    def test_think_tag_split_across_chunks(self) -> None:
        # First chunk: partial tag
        text1, _, mode1, buf1 = _process_stream_content_delta("<thin", "detecting", "")
        self.assertEqual(text1, [])
        self.assertEqual(mode1, "detecting")
        self.assertEqual(buf1, "<thin")

        # Second chunk: complete tag
        text2, reasoning2, mode2, _ = _process_stream_content_delta("k>reasoning</think>text", mode1, buf1)
        self.assertEqual(reasoning2, ["reasoning"])
        self.assertEqual(text2, ["text"])
        self.assertEqual(mode2, "text")

    def test_plain_text_no_think(self) -> None:
        text, reasoning, mode, _ = _process_stream_content_delta("hello world", "detecting", "")
        self.assertEqual(text, ["hello world"])
        self.assertEqual(reasoning, [])
        self.assertEqual(mode, "text")


if __name__ == "__main__":
    unittest.main()

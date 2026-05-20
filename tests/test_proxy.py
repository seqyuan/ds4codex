import unittest

from ds4codex.proxy import responses_to_chat


class ResponsesToChatTests(unittest.TestCase):
    def test_maps_developer_role_to_system(self) -> None:
        chat = responses_to_chat(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "Follow project instructions.",
                    }
                ],
            },
            default_thinking="disabled",
        )

        self.assertEqual(chat["messages"][0]["role"], "system")
        self.assertEqual(chat["messages"][0]["content"], "Follow project instructions.")

    def test_maps_responses_tool_to_chat_function_tool(self) -> None:
        chat = responses_to_chat(
            {
                "model": "deepseek-v4-flash",
                "input": "hello",
                "tools": [
                    {
                        "type": "function",
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    }
                ],
            },
            default_thinking="disabled",
        )

        self.assertEqual(
            chat["tools"][0],
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
        )

    def test_maps_responses_tool_choice_to_chat_function_choice(self) -> None:
        chat = responses_to_chat(
            {
                "model": "deepseek-v4-flash",
                "input": "hello",
                "tool_choice": {"type": "function", "name": "read_file"},
            },
            default_thinking="disabled",
        )

        self.assertEqual(chat["tool_choice"], {"type": "function", "function": {"name": "read_file"}})


if __name__ == "__main__":
    unittest.main()

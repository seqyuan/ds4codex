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


if __name__ == "__main__":
    unittest.main()

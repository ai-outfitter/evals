import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.pi import (
    build_pi_command,
    parse_pi_output,
    unwrap_json_fence,
    validate_json_schema_output,
)


def agent_end_line(text: str, usage: dict | None = None) -> str:
    return json.dumps(
        {
            "type": "agent_end",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "usage": usage or {"input": 10, "output": 5, "totalTokens": 15},
                    "stopReason": "end_turn",
                    "responseModel": "qwen3:4b",
                },
            ],
        }
    )


class PiTests(unittest.TestCase):
    def test_build_command_thinking_and_no_tools(self):
        command = build_pi_command(
            provider="ollama", model="qwen3:4b", thinking="off", prompt="p", no_tools=True
        )
        self.assertEqual(command[:2], ["pi", "--provider"])
        self.assertIn("--thinking", command)
        self.assertEqual(command[command.index("--thinking") + 1], "off")
        self.assertIn("--no-tools", command)
        self.assertEqual(command[-1], "p")

    def test_parse_pi_output(self):
        parsed = parse_pi_output(agent_end_line("hello") + "\nnot json\n")
        self.assertEqual(parsed["usage"]["total_tokens"], 15)
        self.assertEqual(parsed["chat_history"][-1]["text"], "hello")
        self.assertEqual(parsed["response_models"], ["qwen3:4b"])
        self.assertEqual(parsed["stop_reason"], "end_turn")
        self.assertTrue(parsed["parse_errors"])

    def test_unwrap_fence(self):
        self.assertEqual(unwrap_json_fence('```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(unwrap_json_fence('{"a": 1}'), '{"a": 1}')

    def test_validate_json_schema_output(self):
        schema = {"type": "object", "required": ["overall"], "properties": {"overall": {"enum": ["A", "B", "tie"]}}}
        parsed = parse_pi_output(agent_end_line('```json\n{"overall": "A"}\n```'))
        validation = validate_json_schema_output(parsed["chat_history"], schema)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["parsed"]["overall"], "A")

        bad = parse_pi_output(agent_end_line('{"overall": "C"}'))
        self.assertFalse(validate_json_schema_output(bad["chat_history"], schema)["valid"])


if __name__ == "__main__":
    unittest.main()

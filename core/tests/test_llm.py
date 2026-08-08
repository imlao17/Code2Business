import unittest


class LlmContractTests(unittest.TestCase):
    def test_fenced_json_is_parsed_but_non_object_roots_are_rejected(self):
        from c2b.llm import LLMError, _parse_json_object

        self.assertEqual(_parse_json_object("```json\n{\"ok\": true}\n```"), {"ok": True})
        with self.assertRaises(LLMError):
            _parse_json_object("[1, 2, 3]")

    def test_schema_validator_rejects_missing_wrong_and_extra_fields(self):
        from c2b.llm import LLMError, _validate_json_schema

        schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
            "additionalProperties": False,
        }
        _validate_json_schema({"items": ["ok"]}, schema)
        for invalid in ({}, {"items": [1]}, {"items": [], "extra": True}):
            with self.subTest(invalid=invalid), self.assertRaises(LLMError):
                _validate_json_schema(invalid, schema)

        with self.assertRaises(LLMError):
            _validate_json_schema(101, {"type": "integer", "maximum": 100})

    def test_cache_fingerprint_includes_endpoint_schema_and_temperature(self):
        from c2b.llm import _cache_fingerprint

        common = dict(model="m", system="s", user="u", prompt_version="v1")
        first = _cache_fingerprint(
            base_url="https://one.example/v1",
            schema={"type": "object"},
            temperature=0.1,
            **common,
        )
        variants = [
            _cache_fingerprint(
                base_url="https://two.example/v1",
                schema={"type": "object"},
                temperature=0.1,
                **common,
            ),
            _cache_fingerprint(
                base_url="https://one.example/v1",
                schema={"type": "array"},
                temperature=0.1,
                **common,
            ),
            _cache_fingerprint(
                base_url="https://one.example/v1",
                schema={"type": "object"},
                temperature=0.2,
                **common,
            ),
        ]
        self.assertEqual(len({first, *variants}), 4)

    def test_api_mode_rejects_private_llm_endpoint(self):
        from c2b.llm import LLMClient, LLMError

        with self.assertRaises(LLMError):
            LLMClient(
                mode="api",
                base_url="http://127.0.0.1:11434/v1",
                api_key="test",
            )


if __name__ == "__main__":
    unittest.main()

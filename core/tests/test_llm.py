import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


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
        anthropic = _cache_fingerprint(
            base_url="https://one.example/v1", schema={"type": "object"},
            temperature=0.1, provider="anthropic", **common,
        )
        self.assertNotEqual(first, anthropic)

    def test_anthropic_messages_request_and_usage_are_normalized(self):
        from c2b.llm import LLMClient

        response = MagicMock(status_code=200)
        response.json.return_value = {
            "content": [{"type": "text", "text": '{"ok": true}'}],
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }
        http = MagicMock()
        http.__enter__.return_value = http
        http.__exit__.return_value = False
        http.post.return_value = response
        with patch("c2b.llm.validate_outbound_url", return_value="https://api.anthropic.com"), \
                patch("c2b.llm.httpx.Client", return_value=http):
            result = LLMClient(
                provider="anthropic", mode="api", cache=False, max_retries=1,
                base_url="https://api.anthropic.com", api_key="test-key", model="claude-test",
            ).chat_json("system", "user", {"type": "object"})

        self.assertEqual(result, {"ok": True})
        url = http.post.call_args.args[0]
        kwargs = http.post.call_args.kwargs
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(kwargs["headers"]["x-api-key"], "test-key")
        self.assertEqual(kwargs["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": "user"}])
        self.assertEqual(kwargs["json"]["max_tokens"], 8192)

    def test_api_mode_rejects_private_llm_endpoint(self):
        from c2b.llm import LLMClient, LLMError

        with self.assertRaises(LLMError):
            LLMClient(
                mode="api",
                base_url="http://127.0.0.1:11434/v1",
                api_key="test",
            )

    def test_existing_settings_default_to_compatible_provider(self):
        from c2b import settings_store

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".c2b"
            with patch.object(settings_store, "CONFIG_DIR", config_dir), \
                    patch.object(settings_store, "CONFIG_FILE", config_dir / "config.json"):
                self.assertEqual(settings_store.load_settings()["llm_provider"], "openai_compatible")
                settings_store.save_settings({"llm_provider": "anthropic"})
                self.assertEqual(settings_store.load_settings()["llm_provider"], "anthropic")

    def test_desktop_api_key_is_stored_in_macos_keychain(self):
        from c2b import settings_store

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "settings"
            config_file = config_dir / "config.json"
            with patch.object(settings_store, "CONFIG_DIR", config_dir), \
                    patch.object(settings_store, "CONFIG_FILE", config_file), \
                    patch.object(settings_store, "_desktop_keychain_enabled", return_value=True), \
                    patch.object(settings_store, "_write_keychain_secret") as write_secret, \
                    patch.object(settings_store, "_read_keychain_secret", return_value="desktop-secret"):
                saved = settings_store.save_settings({"llm_api_key": "desktop-secret"})

            write_secret.assert_called_once_with("desktop-secret")
            on_disk = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertNotIn("llm_api_key", on_disk)
            self.assertEqual(on_disk["llm_api_key_storage"], "macos_keychain")
            self.assertEqual(saved["llm_api_key"], "desktop-secret")
            self.assertEqual(saved["llm_api_key_storage"], "macos_keychain")

    def test_desktop_settings_save_migrates_existing_plaintext_key(self):
        from c2b import settings_store

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "settings"
            config_dir.mkdir()
            config_file = config_dir / "config.json"
            config_file.write_text(
                json.dumps({"llm_api_key": "legacy-secret"}), encoding="utf-8"
            )
            with patch.object(settings_store, "CONFIG_DIR", config_dir), \
                    patch.object(settings_store, "CONFIG_FILE", config_file), \
                    patch.object(settings_store, "_desktop_keychain_enabled", return_value=True), \
                    patch.object(settings_store, "_write_keychain_secret") as write_secret, \
                    patch.object(settings_store, "_read_keychain_secret", return_value="legacy-secret"):
                settings_store.save_settings({"llm_model": "local-model"})

            write_secret.assert_called_once_with("legacy-secret")
            on_disk = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertNotIn("llm_api_key", on_disk)
            self.assertEqual(on_disk["llm_model"], "local-model")

    def test_keychain_failure_keeps_existing_plaintext_configuration(self):
        from c2b import settings_store

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "settings"
            config_dir.mkdir()
            config_file = config_dir / "config.json"
            config_file.write_text(
                json.dumps({"llm_api_key": "legacy-secret"}), encoding="utf-8"
            )
            with patch.object(settings_store, "CONFIG_DIR", config_dir), \
                    patch.object(settings_store, "CONFIG_FILE", config_file), \
                    patch.object(settings_store, "_desktop_keychain_enabled", return_value=True), \
                    patch.object(
                        settings_store,
                        "_write_keychain_secret",
                        side_effect=settings_store.SettingsStoreError("keychain unavailable"),
                    ):
                with self.assertRaises(settings_store.SettingsStoreError):
                    settings_store.save_settings({"llm_model": "local-model"})

            on_disk = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["llm_api_key"], "legacy-secret")
            self.assertNotIn("llm_model", on_disk)

    def test_desktop_empty_api_key_removes_keychain_item(self):
        from c2b import settings_store

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "settings"
            config_file = config_dir / "config.json"
            with patch.object(settings_store, "CONFIG_DIR", config_dir), \
                    patch.object(settings_store, "CONFIG_FILE", config_file), \
                    patch.object(settings_store, "_desktop_keychain_enabled", return_value=True), \
                    patch.object(settings_store, "_delete_keychain_secret") as delete_secret, \
                    patch.object(settings_store, "_read_keychain_secret", return_value=""):
                saved = settings_store.save_settings({"llm_api_key": ""})

            delete_secret.assert_called_once_with()
            self.assertFalse(saved["llm_api_key"])
            self.assertNotIn(
                "llm_api_key", json.loads(config_file.read_text(encoding="utf-8"))
            )


if __name__ == "__main__":
    unittest.main()

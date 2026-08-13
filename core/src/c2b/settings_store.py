"""LLM 配置持久化：默认 ``~/.c2b/config.json``（chmod 600）。

优先级：环境变量 C2B_LLM_* > ~/.c2b/config.json > 默认值。
Web 设置界面通过此文件保存配置，进程内即时生效（LLMClient 每次初始化读取）。

Desktop may set ``C2B_CONFIG_DIR`` to its Application Support directory so a
local workbench neither leaks settings into the CLI profile nor needs a
global writable location.  On macOS Desktop, API keys are stored in the
login Keychain rather than the JSON file.
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("C2B_CONFIG_DIR", Path.home() / ".c2b"))
CONFIG_FILE = CONFIG_DIR / "config.json"
_WRITE_LOCK = threading.Lock()
_KEYCHAIN_SERVICE = "com.lao17.code2business.llm"
_KEYCHAIN_ACCOUNT = "default-api-key"
_MISSING = object()

_DEFAULTS = {
    # OpenAI-compatible is the backwards-compatible default.  Existing
    # ~/.c2b/config.json files gain this field automatically on load.
    "llm_provider": "openai_compatible",
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "",
    "llm_model": "deepseek-chat",
    "llm_mode": "api",   # api / mock
}


class SettingsStoreError(RuntimeError):
    """Raised when protected Desktop settings cannot be persisted."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _desktop_keychain_enabled() -> bool:
    return sys.platform == "darwin" and _env_flag("C2B_DESKTOP_MODE")


def _read_keychain_secret() -> str:
    """Read the Desktop API key without exposing command output on failure."""
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-a", _KEYCHAIN_ACCOUNT, "-s", _KEYCHAIN_SERVICE, "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.rstrip("\r\n") if result.returncode == 0 else ""


def _write_keychain_secret(value: str) -> None:
    command = [
        "/usr/bin/security", "add-generic-password", "-U",
        "-a", _KEYCHAIN_ACCOUNT, "-s", _KEYCHAIN_SERVICE,
        # Keep -w last so security reads the secret from stdin instead of a
        # process argument that can briefly appear in process listings.
        "-T", "/usr/bin/security", "-w",
    ]
    try:
        result = subprocess.run(
            command,
            # The interactive form asks for the value twice.
            input=value + "\n" + value + "\n",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SettingsStoreError("无法访问 macOS 钥匙串") from exc
    if result.returncode != 0:
        raise SettingsStoreError("无法将模型 API Key 保存到 macOS 钥匙串")


def _delete_keychain_secret() -> None:
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "delete-generic-password",
                "-a", _KEYCHAIN_ACCOUNT, "-s", _KEYCHAIN_SERVICE,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SettingsStoreError("无法访问 macOS 钥匙串") from exc
    # security returns 44 when the item does not exist; deleting an already
    # absent secret is still a successful idempotent operation.
    if result.returncode not in {0, 44}:
        raise SettingsStoreError("无法从 macOS 钥匙串删除模型 API Key")


def _ensure_file():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(_DEFAULTS), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def load_settings() -> dict:
    """读取配置：env > 文件 > 默认。"""
    _ensure_file()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    merged = {**_DEFAULTS, **data}
    if _desktop_keychain_enabled():
        keychain_secret = _read_keychain_secret()
        if keychain_secret:
            merged["llm_api_key"] = keychain_secret
            merged["llm_api_key_storage"] = "macos_keychain"
    # 环境变量优先
    if os.environ.get("C2B_LLM_BASE_URL"):
        merged["llm_base_url"] = os.environ["C2B_LLM_BASE_URL"]
    if os.environ.get("C2B_LLM_PROVIDER"):
        merged["llm_provider"] = os.environ["C2B_LLM_PROVIDER"]
    if os.environ.get("C2B_LLM_API_KEY"):
        merged["llm_api_key"] = os.environ["C2B_LLM_API_KEY"]
    if os.environ.get("C2B_LLM_MODEL"):
        merged["llm_model"] = os.environ["C2B_LLM_MODEL"]
    if os.environ.get("C2B_LLM_MODE"):
        merged["llm_mode"] = os.environ["C2B_LLM_MODE"]
    return merged


def save_settings(overrides: dict) -> dict:
    """保存配置，并在 macOS Desktop 中把 API Key 放入钥匙串。"""
    with _WRITE_LOCK:
        _ensure_file()
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = dict(_DEFAULTS)
        key_override = overrides.get("llm_api_key", _MISSING)
        if _desktop_keychain_enabled():
            # Migrate a v0.3.0 plaintext key on the next settings save.  A new
            # key always wins; leaving the field blank in the UI means the
            # existing Keychain item remains unchanged.
            key_to_store = key_override
            if key_to_store is _MISSING and data.get("llm_api_key"):
                key_to_store = data["llm_api_key"]
            if key_to_store is not _MISSING:
                if key_to_store:
                    _write_keychain_secret(str(key_to_store))
                else:
                    _delete_keychain_secret()
                data.pop("llm_api_key", None)
                data["llm_api_key_storage"] = "macos_keychain"

        for k, v in overrides.items():
            if k in _DEFAULTS:
                if k == "llm_api_key" and _desktop_keychain_enabled():
                    continue
                data[k] = v
        temp = CONFIG_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(CONFIG_FILE)
    return load_settings()

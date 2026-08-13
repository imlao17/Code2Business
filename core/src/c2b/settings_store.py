"""LLM 配置持久化：默认 ``~/.c2b/config.json``（chmod 600）。

优先级：环境变量 C2B_LLM_* > ~/.c2b/config.json > 默认值。
Web 设置界面通过此文件保存配置，进程内即时生效（LLMClient 每次初始化读取）。

Desktop may set ``C2B_CONFIG_DIR`` to its Application Support directory so a
local workbench neither leaks settings into the CLI profile nor needs a
global writable location.
"""
import json
import os
import threading
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("C2B_CONFIG_DIR", Path.home() / ".c2b"))
CONFIG_FILE = CONFIG_DIR / "config.json"
_WRITE_LOCK = threading.Lock()

_DEFAULTS = {
    # OpenAI-compatible is the backwards-compatible default.  Existing
    # ~/.c2b/config.json files gain this field automatically on load.
    "llm_provider": "openai_compatible",
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "",
    "llm_model": "deepseek-chat",
    "llm_mode": "api",   # api / mock
}


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
    """保存配置（仅文件，env 优先逻辑不变）。返回保存后的完整配置。"""
    with _WRITE_LOCK:
        _ensure_file()
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = dict(_DEFAULTS)
        for k, v in overrides.items():
            if k in _DEFAULTS:
                data[k] = v
        temp = CONFIG_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(CONFIG_FILE)
    return load_settings()

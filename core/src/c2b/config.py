"""全局配置：数据库地址、LLM 参数。

配置优先级：环境变量 > .env 文件 > 默认值。
.env 文件位置：当前工作目录或项目根（c2b/.env）。
"""
import os
from pathlib import Path


def _load_dotenv():
    """轻量 .env 加载（无第三方依赖）。"""
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except OSError:
                pass


_load_dotenv()

DB_URL = os.environ.get("C2B_DB_URL", f"sqlite:///{Path.cwd() / 'c2b.db'}")
# LLM 模式: api（默认，调用云端）/ mock（内置模拟，验证管线用）
LLM_MODE = os.environ.get("C2B_LLM_MODE", "api")
LLM_BASE_URL = os.environ.get("C2B_LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("C2B_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("C2B_LLM_MODEL", "deepseek-chat")

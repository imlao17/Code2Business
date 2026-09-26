"""全局配置：数据库地址、LLM 参数。

配置优先级：环境变量 > .env 文件 > 默认值。
.env 文件位置：当前工作目录或项目根（c2b/.env）。

默认数据库统一放在 ~/.c2b/c2b.db（与 `c2b mcp` 一致）：CLI、Skills 与 MCP
共用同一份数据，不再随启动目录在每个项目里新建 c2b.db。若发现当前目录
存在旧版孤儿库且 ~/.c2b/c2b.db 尚不存在，导入时自动收编一次（合并 WAL、
安全无损）；之后可用 `c2b clean` 清理当前目录的孤儿库残留。
"""
import os
import sqlite3
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

# c2b 的本机数据目录（Desktop 等场景可用 C2B_CONFIG_DIR 重定向）
CONFIG_DIR = Path(os.environ.get("C2B_CONFIG_DIR", Path.home() / ".c2b"))
# CLI 中间产物（证据导出 / 语义导入文件）的默认落点，避免污染工作目录
TMP_DIR = CONFIG_DIR / "tmp"


def _adopt_orphan_db(orphan: Path, target: Path) -> bool:
    """把旧版“当前目录默认库”一次性收编进统一库。

    使用 sqlite3 backup API：即使源库的数据还躺在未合并的 WAL 里也能完整
    迁移，并在目标库落成已 checkpoint 的干净状态。任何失败都静默放弃，
    由调用方继续使用空目标库（老数据留在原地，不丢）。
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # as_uri() 会做百分号转义，路径含空格/中文/特殊字符也能正确打开
        src = sqlite3.connect(orphan.resolve().as_uri() + "?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def _default_db_url() -> str:
    home_db = CONFIG_DIR / "c2b.db"
    try:
        home_db.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not home_db.exists():
        orphan = Path.cwd() / "c2b.db"
        if orphan.exists() and orphan.resolve() != home_db.resolve():
            _adopt_orphan_db(orphan, home_db)
    return f"sqlite:///{home_db}"


DB_URL = os.environ.get("C2B_DB_URL") or _default_db_url()
# LLM 模式: api（默认，调用云端）/ mock（内置模拟，验证管线用）
LLM_MODE = os.environ.get("C2B_LLM_MODE", "api")
LLM_BASE_URL = os.environ.get("C2B_LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("C2B_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("C2B_LLM_MODEL", "deepseek-chat")

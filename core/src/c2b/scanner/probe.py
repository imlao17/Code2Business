"""规模探测（阶段 0）：秒级统计代码量、模块分布、编码、历史版本、档位判定。

从 skills/code2business/scripts/probe.py 迁移并内聚为库函数。
"""
import re
from collections import defaultdict
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".metadata", "target", "bin", "node_modules", "out", "dist",
    "build", ".idea", ".vscode", "__pycache__", ".svn",
}
EXCLUDE_EXTS = {
    ".class", ".jar", ".war", ".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".pdf", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".obj", ".classpath",
}
EXCLUDE_FILES = {".project", ".classpath", ".settings", ".DS_Store"}
CODE_EXTS = {
    ".java", ".vue", ".js", ".ts", ".jsx", ".tsx", ".jsp", ".jspx", ".sql",
    ".xml", ".yml", ".yaml", ".properties", ".py", ".go", ".cs", ".c", ".h",
    ".cpp", ".php", ".rb", ".ini", ".cfg", ".conf", ".sh", ".bat", ".html",
}
ARCHIVE_DIR_HINT = ("backup", "archive", "履歴", "バックアップ", "旧")
FILE_VERSION_HINT = ("_old", "backup", "履歴", "移行", "不要", "失敗", "special",
                     "保留", "廃止", "一時")


def detect_encoding(b: bytes) -> str:
    for enc in ("utf-8", "shift_jis", "gb18030"):
        try:
            b.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "unknown"


def is_archive_dir(name: str) -> bool:
    n = name.lower()
    return any(h in n for h in ARCHIVE_DIR_HINT)


def is_version_file(stem: str) -> bool:
    s = stem.lower()
    return any(h in s for h in FILE_VERSION_HINT)


def probe(root: Path, module_depth: int = 2) -> dict:
    """返回规模报告（不含 modules_detail 之外的中间结构）。"""
    total_files = 0
    total_lines = 0
    excluded_files = 0
    lines_by_ext = defaultdict(int)
    module_lines = defaultdict(int)
    module_files = defaultdict(int)
    modules_detail = defaultdict(list)
    archive_lines = archive_files = 0
    version_files = version_lines = 0
    enc_count = defaultdict(int)
    enc_samples = 0
    giant_files = []

    code_exts = {e.lower() for e in CODE_EXTS}
    exclude_dirs = {d.lower() for d in EXCLUDE_DIRS}

    def module_of(parts):
        depth = min(module_depth, len(parts) - 1)
        return "/".join(parts[:depth])

    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        parts = rel.parts
        if any(p.lower() in exclude_dirs for p in parts[:-1]):
            continue
        if f.name in EXCLUDE_FILES:
            continue
        ext = f.suffix.lower()
        if ext in EXCLUDE_EXTS:
            excluded_files += 1
            continue
        if ext not in code_exts:
            continue
        total_files += 1
        try:
            with f.open("rb") as fh:
                n_lines = sum(1 for _ in fh)
        except OSError:
            n_lines = 0
        total_lines += n_lines
        lines_by_ext[ext] += n_lines

        stem = f.stem.lower()
        if is_version_file(stem):
            version_files += 1
            version_lines += n_lines
        if (total_files - 1) % 40 == 0 or n_lines > 5000:
            try:
                enc = detect_encoding(f.read_bytes()[:8192])
            except OSError:
                enc = "unknown"
            enc_count[enc] += 1
            enc_samples += 1

        mod = module_of(parts)
        module_lines[mod] += n_lines
        module_files[mod] += 1
        modules_detail[mod].append({"path": str(rel), "lines": n_lines})
        if is_archive_dir(parts[0]):
            archive_lines += n_lines
            archive_files += 1
        if n_lines > 10000:
            giant_files.append({"file": str(rel), "lines": n_lines})

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "excluded_files": excluded_files,
        "lines_by_ext": dict(sorted(lines_by_ext.items(), key=lambda x: -x[1])),
        "module_lines_top": [
            {"module": m, "lines": l, "files": module_files[m]}
            for l, m in sorted(
                ((v, k) for k, v in module_lines.items()), reverse=True)[:30]
        ],
        "module_count": len(module_lines),
        "archive_lines": archive_lines,
        "archive_files": archive_files,
        "version_files": version_files,
        "version_lines": version_lines,
        "encoding_samples": dict(sorted(enc_count.items(), key=lambda x: -x[1])),
        "giant_files": giant_files[:20],
        "modules_detail": dict(modules_detail),
        "tier": tier_of(total_lines),
    }


def tier_of(total_lines: int) -> str:
    if total_lines <= 100_000:
        return "S"
    if total_lines <= 1_000_000:
        return "M"
    if total_lines <= 2_000_000:
        return "L"
    return "XL"


def build_shard_plan(modules_detail: dict, total_lines: int, tier: str,
                     max_shards: int = 16) -> dict:
    """贪心装载分片：模块行数降序装入，超载模块按文件粒度拆开。"""
    if tier in ("S", "M"):
        return {"enabled": False, "reason": f"档位 {tier} 不需要分片"}

    n = min(max(total_lines // 80_000, 4), max_shards)
    target = total_lines / n
    shards: list[dict] = []
    current = {"name": "shard-1", "files": [], "lines": 0, "modules": set()}

    items = sorted(modules_detail.items(),
                   key=lambda kv: -sum(f["lines"] for f in kv[1]))

    def flush():
        nonlocal current
        if current["files"]:
            shards.append(current)
        current = {"name": f"shard-{len(shards) + 2}", "files": [],
                   "lines": 0, "modules": set()}

    for mod, files in items:
        mod_lines = sum(f["lines"] for f in files)
        if mod_lines > target * 1.3:
            for fl in files:
                if current["lines"] >= target:
                    flush()
                current["files"].append(fl["path"])
                current["lines"] += fl["lines"]
                current["modules"].add(mod)
        else:
            if current["lines"] >= target:
                flush()
            current["files"].extend(f["path"] for f in files)
            current["lines"] += mod_lines
            current["modules"].add(mod)
    flush()

    return {
        "enabled": True,
        "tier": tier,
        "target_lines_per_shard": round(target),
        "shard_count": len(shards),
        "shards": [
            {"name": s["name"], "lines": s["lines"],
             "modules": sorted(s["modules"]), "files": s["files"]}
            for s in shards
        ],
    }

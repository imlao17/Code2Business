#!/usr/bin/env python3
"""Code2Business 规模探测 + 分片方案生成（最低参考版）

用法:
    python3 probe.py <code_root> [--output <dir>]

产出:
    <output>/scale_report.json   规模报告（行数/语言/模块分布/编码/档位）
    <output>/shard_plan.json     分片方案（XL 档及以上）
    同时打印 Markdown 摘要到 stdout

原则:
    - 纯标准库，无第三方依赖
    - 只做文件级统计与目录归属，不解析语法树（探测应秒级完成）
    - 排除编译产物与 IDE 垃圾目录
"""
import argparse
import json
import os
import sys
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
ARCHIVE_DIR_HINT = ("_", "backup", "archive", "旧", "履歴", "履歴", "バックアップ")
# 文件级历史版本特征（如 OrderEntryNo_200929.java、xxx_差異移植前.java）
FILE_VERSION_HINT = ("_old", "backup", "履歴", "移行", "前", "特殊", "不要", "失敗",
                     "special", "special_", "保留", "廃止", "一時")


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
    return any(h in n for h in ("_", "backup", "archive", "履歴", "バックアップ", "旧"))


def scan(root: Path, module_depth: int = 2):
    total_files = 0
    total_lines = 0
    excluded_files = 0
    lines_by_ext = defaultdict(int)
    module_lines = defaultdict(int)       # 模块 → 代码行数
    module_files = defaultdict(int)       # 模块 → 代码文件数
    modules_detail = defaultdict(list)    # 模块 → [{"path","lines"}, ...]（分片用）
    archive_lines = 0
    archive_files = 0
    version_files = 0
    version_lines = 0
    enc_count = defaultdict(int)
    enc_samples = 0
    giant_files = []                      # >1 万行的代码文件
    top_modules = []                      # (行数, 名称, 文件数)

    code_exts = {e.lower() for e in CODE_EXTS}
    exclude_dirs = {d.lower() for d in EXCLUDE_DIRS}
    file_hints = {h.lower() for h in FILE_VERSION_HINT}

    def module_of(parts):
        """模块归属：默认两级目录；不足两级时取一级。"""
        depth = min(module_depth, len(parts) - 1)
        return "/".join(parts[:depth])

    for f in root.rglob("*"):
        rel = f.relative_to(root)
        parts = rel.parts
        if any(p.lower() in exclude_dirs or (p.lower().startswith(".") and p != ".")
               for p in parts[:-1]):
            continue
        if not f.is_file():
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
            n_lines = sum(1 for _ in f.open("rb"))
        except OSError:
            n_lines = 0
        total_lines += n_lines
        lines_by_ext[ext] += n_lines

        # 编码抽样：每 40 个文件抽 1 个 + 所有 >5000 行文件
        stem = f.stem.lower()
        is_version_file = any(h in stem for h in file_hints)
        if is_version_file:
            version_files += 1
            version_lines += n_lines
        if enc_samples % 40 == 0 or n_lines > 5000:
            try:
                enc = detect_encoding(f.read_bytes()[:8192])
            except OSError:
                enc = "unknown"
            enc_count[enc] += 1
            enc_samples += 1
        # 模块归属
        mod = module_of(parts)
        module_lines[mod] += n_lines
        module_files[mod] += 1
        modules_detail[mod].append({"path": str(rel), "lines": n_lines})
        if is_archive_dir(parts[0]):
            archive_lines += n_lines
            archive_files += 1
        if n_lines > 10000:
            giant_files.append({"file": str(rel), "lines": n_lines})

    top_modules = sorted(
        ((v, k, module_files[k]) for k, v in module_lines.items()),
        reverse=True,
    )[:30]

    return {
        "total_files": total_files,
        "total_lines": total_lines,
        "excluded_files": excluded_files,
        "lines_by_ext": dict(sorted(lines_by_ext.items(), key=lambda x: -x[1])),
        "module_lines_top": [{"module": m, "lines": l, "files": fl}
                             for l, m, fl in top_modules],
        "module_count": len(module_lines),
        "archive_lines": archive_lines,
        "archive_files": archive_files,
        "version_files": version_files,
        "version_lines": version_lines,
        "encoding_samples": dict(sorted(enc_count.items(), key=lambda x: -x[1])),
        "giant_files": giant_files[:20],
        "modules_detail": dict(modules_detail),
    }


def tier_of(total_lines: int) -> str:
    if total_lines <= 100_000:
        return "S"
    if total_lines <= 1_000_000:
        return "M"
    if total_lines <= 2_000_000:
        return "L"
    return "XL"


def build_shard_plan(modules_detail, total_lines, tier, max_shards=16):
    """贪心装载分片：模块行数降序装入，超载模块按文件粒度拆开。"""
    if tier in ("S", "M"):
        return {"enabled": False, "reason": f"档位 {tier} 不需要分片，单机全量即可"}

    n = min(max(total_lines // 80_000, 4), max_shards)
    target = total_lines / n
    shards = []
    current = {"name": "shard-1", "files": [], "lines": 0, "modules": set()}

    items = sorted(modules_detail.items(),
                   key=lambda kv: -sum(f["lines"] for f in kv[1]))

    def flush():
        nonlocal current
        if current["files"]:
            shards.append(current)
        current = {"name": f"shard-{len(shards) + 2}", "files": [], "lines": 0, "modules": set()}

    for mod, files in items:
        mod_lines = sum(f["lines"] for f in files)
        if mod_lines > target * 1.3:
            # 超载模块：按文件拆散
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
             "modules": sorted(s["modules"]),
             "files": s["files"]}
            for s in shards
        ],
    }


def main():
    ap = argparse.ArgumentParser(description="Code2Business 规模探测与分片方案")
    ap.add_argument("code_root", help="待分析代码根目录")
    ap.add_argument("--output", default=".", help="输出目录（默认当前目录）")
    ap.add_argument("--json-only", action="store_true", help="只输出 JSON")
    ap.add_argument("--module-depth", type=int, default=2,
                    help="模块归属目录深度（默认 2：businese/Sms01206 粒度）")
    args = ap.parse_args()

    root = Path(args.code_root)
    if not root.is_dir():
        print(f"错误: {root} 不是目录", file=sys.stderr)
        sys.exit(1)

    print(f"正在扫描 {root} ...", file=sys.stderr)
    report = scan(root, module_depth=args.module_depth)
    report["tier"] = tier_of(report["total_lines"])

    plan = build_shard_plan(report["modules_detail"], report["total_lines"],
                            report["tier"])

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    report_json = {k: v for k, v in report.items() if k != "modules_detail"}
    (out / "scale_report.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "shard_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json_only:
        print(json.dumps({"scale": report_json, "shard_plan": plan},
                         ensure_ascii=False, indent=2))
        return

    m = report
    print(f"# 规模报告\n")
    print(f"- **档位**: {m['tier']}（S≤10万 / M≤100万 / L≤200万 / XL>200万）")
    print(f"- **代码文件**: {m['total_files']}，**总行数**: {m['total_lines']:,}")
    print(f"- **已排除产物**: {m['excluded_files']} 个文件")
    print(f"- **归档目录代码**: {m['archive_files']} 文件 / {m['archive_lines']:,} 行")
    print(f"- **疑似文件级历史版本**: {m['version_files']} 文件 / {m['version_lines']:,} 行"
          f"（文件名含 _old/履歴/移行 等特征，分析时注意同功能多版本并存）")
    print(f"- **编码分布(抽样)**: {m['encoding_samples']}")
    print("\n## 语言分布")
    for ext, n in m["lines_by_ext"].items():
        print(f"- `{ext}`: {n:,} 行")
    print("\n## 模块分布 Top15")
    for mod in m["module_lines_top"][:15]:
        print(f"- {mod['module']}: {mod['lines']:,} 行 / {mod['files']} 文件")
    print("\n## 巨型文件（>1 万行）")
    for g in m["giant_files"]:
        print(f"- {g['file']}: {g['lines']:,} 行")
    print("\n## 分片方案")
    if plan["enabled"]:
        print(f"- 分片数: {plan['shard_count']}，目标每片: {plan['target_lines_per_shard']:,} 行")
        for s in plan["shards"]:
            print(f"- {s['name']}: {s['lines']:,} 行 / 模块 {','.join(s['modules'])}")
    else:
        print(f"- {plan['reason']}")
    print(f"\n报告已写入: {out / 'scale_report.json'}、{out / 'shard_plan.json'}")


if __name__ == "__main__":
    main()

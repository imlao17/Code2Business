#!/usr/bin/env python3
"""Code2Business 入口提取（最低参考版）：快速定位系统中所有程序入口

用法:
    python3 extract_entries.py <code_root> [--output <dir>]

提取的入口类型:
    - batch:    static void main（批处理程序）
    - api:      @Controller/@RestController + @RequestMapping/@GetMapping 等
    - schedule: @Scheduled / @Scheduling
    - mq:       @RabbitListener / @KafkaListener
    - gui:      继承 JFrame/Dialog 的 GUI 类（老式 Swing/APWORKS 系统）

产出:
    <output>/entries.csv     全部入口清单
    <output>/entries.md      按类型分组的 Markdown 清单（打印到 stdout）
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

MAIN_RE = re.compile(r"static\s+void\s+main\s*\(\s*String")
API_CLASS_RE = re.compile(r"@(?:Rest)?Controller")
API_METHOD_RE = re.compile(
    r"@(?:RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)"
    r"\s*\(\s*(?:value\s*=\s*)?[\"']([^\"']+)")
SCHED_RE = re.compile(r"@Scheduled\s*\(")
MQ_RE = re.compile(r"@(?:RabbitListener|KafkaListener)\s*\(")
GUI_RE = re.compile(r"extends\s+(?:JFrame|JDialog|CDJDialog|JPanel|JApplet)")

EXCLUDE_DIRS = {".git", ".metadata", "target", "bin", "node_modules", "out",
                "dist", "build", ".idea", "__pycache__", ".svn"}


def find_java_files(root: Path):
    for f in root.rglob("*.java"):
        rel = f.relative_to(root)
        if any(p.lower() in EXCLUDE_DIRS for p in rel.parts[:-1]):
            continue
        yield f, rel


def extract(path: Path):
    """返回该文件中的所有入口 (type, name, line) 列表。"""
    hits = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        if MAIN_RE.search(line):
            hits.append(("batch", "main", i))
        if SCHED_RE.search(line):
            hits.append(("schedule", "scheduled", i))
        if MQ_RE.search(line):
            hits.append(("mq", "mq-listener", i))
        m = API_METHOD_RE.search(line)
        if m:
            hits.append(("api", m.group(1), i))
    cls = [API_CLASS_RE.search(l) for l in lines[:50]]
    if any(cls):
        hits.append(("api-class", "controller", 1))
    gui = [GUI_RE.search(l) for l in lines[:80]]
    if any(gui):
        hits.append(("gui", "gui-class", 1))
    return hits


def main():
    ap = argparse.ArgumentParser(description="Code2Business 入口提取")
    ap.add_argument("code_root")
    ap.add_argument("--output", default=".")
    args = ap.parse_args()

    root = Path(args.code_root)
    rows = []  # (rel_path, type, name, line)
    for f, rel in find_java_files(root):
        for t, name, line in extract(f):
            rows.append((str(rel), t, name, line))

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "entries.csv", "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["file", "type", "name", "line"])
        w.writerows(rows)

    by_type = {}
    for r in rows:
        by_type.setdefault(r[1], []).append(r)

    print("# 入口清单\n")
    for t in ("api", "batch", "schedule", "mq", "gui", "api-class"):
        group = by_type.get(t, [])
        if not group:
            continue
        print(f"## {t}（{len(group)} 个）")
        for rel, _, name, line in group[:40]:
            print(f"- `{rel}:{line}` {name}")
        if len(group) > 40:
            print(f"- …共 {len(group)} 个，其余见 entries.csv")
        print()
    print(f"全部清单已写入: {out / 'entries.csv'}")


if __name__ == "__main__":
    main()

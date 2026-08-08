"""图谱构建与落库编排：probe → 分片 → 逐片 parse → 合并写入 PG/SQLite。

MVP 串行执行（scan_tasks 记录任务状态，规模化阶段并行化）；跨文件
方法解析用内存符号索引（类.方法 → 对象 id），未命中记 UNKNOWN。
"""
import json
import re
from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy.orm import Session

from .. import models
from . import parse as parse_mod
from . import probe as probe_mod
from . import sqlx

PATH_ANNS = {"RequestMapping", "GetMapping", "PostMapping", "PutMapping",
             "DeleteMapping", "PatchMapping"}
CONTROLLER_ANNS = {"Controller", "RestController"}


class Scanner:
    def __init__(self, session: Session, root: Path, name: str,
                 git_commit: str | None = None, module_depth: int = 2,
                 max_files: int | None = None):
        self.s = session
        self.root = root
        self.module_depth = module_depth
        self.max_files = max_files
        self.git_commit = git_commit
        self.project = models.Project(name=name, repo_path=str(root))
        self.scan = None

    # ---------- 项目与扫描 ----------
    def create(self, project_id: int | None = None) -> None:
        if project_id is not None:
            self.project = self.s.get(models.Project, project_id)
            if self.project is None:
                raise ValueError(f"项目 {project_id} 不存在")
        else:
            self.s.add(self.project)
            self.s.flush()
        self.scan = models.Scan(project_id=self.project.id,
                                git_commit=self.git_commit)
        self.s.add(self.scan)
        self.s.flush()

    def fail(self, error: str) -> None:
        self.scan.status = models.ScanStatus.FAILED
        self.scan.error = error[:2000]
        self.scan.finished_at = models.utcnow()
        self.s.commit()

    def finish(self) -> None:
        self.scan.status = models.ScanStatus.DONE
        self.scan.finished_at = models.utcnow()
        self.s.commit()

    # ---------- 阶段 0：探测 ----------
    def run_probe(self) -> dict:
        self.scan.status = models.ScanStatus.PROBING
        self.scan.started_at = models.utcnow()
        report = probe_mod.probe(self.root, module_depth=self.module_depth)
        self.scan.tier = report["tier"]
        self.scan.meta = {k: v for k, v in report.items() if k != "modules_detail"}
        self.s.commit()
        return report

    # ---------- 阶段 1-3：解析与合并 ----------
    def run_parse(self, report: dict, shards: list[dict] | None = None,
                  progress=None) -> None:
        """shards 为 None 时全量（S/M 档）；否则逐片解析。"""
        self.scan.status = models.ScanStatus.PARSING
        self.s.commit()

        shard_tasks: dict[str, models.ScanTask] = {}
        if shards:
            tasks = []
            for sh in shards:
                tasks.append(models.ScanTask(
                    scan_id=self.scan.id, shard_key=sh["name"],
                    stage="parse", status=models.TaskStatus.PENDING,
                    file_list=sh["files"]))
            self.s.add_all(tasks)
            self.s.commit()
            shard_tasks = {task.shard_key: task for task in tasks}

        total = 0
        seen: set[str] = set()
        parse_errors: list[dict[str, str]] = []
        max_files_reached = False
        parsed_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".jsonl", delete=False
            ) as tmp:
                parsed_path = Path(tmp.name)
                if shards:
                    for shard_index, sh in enumerate(shards):
                        task = shard_tasks[sh["name"]]
                        self._mark_task(task, models.TaskStatus.RUNNING)
                        try:
                            total, shard_errors, truncated = self._parse_shard_files(
                                self._paths_for_shard(sh["files"]),
                                tmp,
                                seen,
                                total,
                                progress,
                            )
                        except Exception as exc:
                            self._mark_task(task, models.TaskStatus.FAILED, str(exc))
                            raise
                        parse_errors.extend(shard_errors)
                        task_errors = [
                            f"{item['file']}: {item['error']}"
                            for item in shard_errors[:5]
                        ]
                        if truncated:
                            max_files_reached = True
                            task_errors.append(
                                f"stopped by max_files={self.max_files}"
                            )
                        if task_errors:
                            self._mark_task(
                                task,
                                models.TaskStatus.FAILED,
                                f"{len(shard_errors)} parse error(s); "
                                + "; ".join(task_errors),
                            )
                        else:
                            self._mark_task(task, models.TaskStatus.DONE)
                        if self.max_files and total >= self.max_files:
                            max_files_reached = True
                            for remaining in shards[shard_index + 1:]:
                                pending_task = shard_tasks[remaining["name"]]
                                self._mark_task(
                                    pending_task,
                                    models.TaskStatus.FAILED,
                                    f"skipped: stopped by max_files={self.max_files}",
                                )
                            break
                else:
                    total, parse_errors, max_files_reached = self._parse_shard_files(
                        self._all_java_paths(),
                        tmp,
                        seen,
                        total,
                        progress,
                    )

            self.scan.meta = {
                **(self.scan.meta or {}),
                "attempted_files": total,
                "parsed_files": total - len(parse_errors),
                "parse_errors": len(parse_errors),
                "parse_error_samples": parse_errors[:20],
                "max_files_reached": max_files_reached,
            }
            self.s.commit()
            self._merge(parsed_path)
        finally:
            if parsed_path is not None:
                parsed_path.unlink(missing_ok=True)

    def _merge(self, parsed_path: Path) -> None:
        self.scan.status = models.ScanStatus.MERGING
        self.s.commit()

        # 模块
        module_cache: dict[str, models.Module] = {}
        for pf in self._iter_parsed(parsed_path):
            mod = pf.get("module") or "(root)"
            if mod not in module_cache:
                module_cache[mod] = models.Module(
                    scan_id=self.scan.id, name=mod, lines=0, file_count=0,
                    is_archive=probe_mod.is_archive_dir(mod.split("/")[0]))
            module_cache[mod].file_count += 1
            module_cache[mod].lines += pf.get("lines") or 0
        self.s.add_all(module_cache.values())
        self.s.flush()

        # 类与方法对象；符号索引 class.method -> CodeObject
        obj_index: dict[tuple, models.CodeObject] = {}
        for pf in self._iter_parsed(parsed_path):
            if "error" in pf:
                continue
            mod = module_cache.get(pf.get("module") or "(root)")
            for c in pf["classes"]:
                obj = models.CodeObject(
                    scan_id=self.scan.id, module_id=mod.id if mod else None,
                    obj_type=models.ObjType(c["type"]),
                    name=c["name"] or Path(pf["file"]).stem,
                    fq_name=(pf["package"] + "." + c["name"]) if c["name"] else None,
                    file_path=pf["file"], start_line=c["start"], end_line=c["end"],
                    annotations=[a["name"] for a in c["annotations"]],
                    comment=c["comment"], is_entry=c["is_entry"],
                    entry_kind=c["entry_kind"],
                    is_version_file=pf.get("version_file", False))
                self.s.add(obj)
                obj_index[("class", pf["file"], c["start"])] = obj
            for m in pf["methods"]:
                obj = models.CodeObject(
                    scan_id=self.scan.id, module_id=mod.id if mod else None,
                    obj_type=models.ObjType.METHOD, name=m["name"],
                    fq_name=_method_fq_name(pf["package"], m),
                    file_path=pf["file"], start_line=m["start"], end_line=m["end"],
                    signature=m["signature"],
                    annotations=[a["name"] for a in m["annotations"]],
                    comment=m["comment"], is_entry=m["is_entry"],
                    entry_kind=m["entry_kind"],
                    is_version_file=pf.get("version_file", False))
                self.s.add(obj)
                obj_index[("method", pf["file"], m["start"])] = obj
        self.s.flush()

        # 方法符号索引（跨文件解析）：显式类接收者 + 方法名 + 参数个数
        method_index: dict[tuple[str, str], list[tuple[int, models.CodeObject]]] = {}
        for pf in self._iter_parsed(parsed_path):
            if "error" in pf:
                continue
            for m in pf["methods"]:
                if not m.get("class_name") or not m.get("name"):
                    continue
                obj = obj_index.get(("method", pf["file"], m["start"]))
                if obj is not None:
                    method_index.setdefault(
                        (m["class_name"], m["name"]), []).append((m.get("param_count", 0), obj))

        # 调用边
        edges = 0
        for pf in self._iter_parsed(parsed_path):
            if "error" in pf:
                continue
            for call in pf["calls"]:
                target, rtype = _resolve_call(call, method_index)
                from_obj = obj_index.get(("method", pf["file"], call.get("ctx_line")))
                if from_obj is None and call.get("cls_start") is not None:
                    from_obj = obj_index.get(("class", pf["file"], call["cls_start"]))
                if from_obj is None:
                    continue  # 无法归属到任何对象，跳过该边
                self.s.add(models.CallEdge(
                    scan_id=self.scan.id, from_obj_id=from_obj.id,
                    to_obj_id=target.id if target else None,
                    to_name=call["method"] if target is None else None,
                    resolve_type=rtype, file_path=pf["file"], line=call["line"]))
                edges += 1

        # API 端点（类路径 + 方法路径）
        api_count = 0
        for pf in self._iter_parsed(parsed_path):
            if "error" in pf:
                continue
            for c in pf["classes"]:
                cobj = obj_index.get(("class", pf["file"], c["start"]))
                if cobj is None:
                    continue
                is_ctrl = any(a["name"] in CONTROLLER_ANNS for a in c["annotations"])
                base = ""
                for a in c["annotations"]:
                    if a["name"] == "RequestMapping" and a["value"]:
                        base = a["value"]
                for m in pf["methods"]:
                    if m.get("class_start") != c["start"]:
                        continue
                    mobj = obj_index.get(("method", pf["file"], m["start"]))
                    path, method = None, None
                    for a in m["annotations"]:
                        if a["name"] in PATH_ANNS:
                            method = a["name"].replace("Mapping", "").upper()
                            if method == "REQUEST":
                                method = None
                            path = (a["value"] or "")
                            break
                    if is_ctrl and path is not None and mobj is not None:
                        self.s.add(models.ApiEndpoint(
                            scan_id=self.scan.id, method=method,
                            path=base + path if base else path,
                            controller_obj_id=mobj.id,
                            description=m["comment"]))
                        api_count += 1

        # 表与 SQL 操作
        table_cache: dict[str, models.DataTable] = {}
        sql_ops = 0
        for pf in self._iter_parsed(parsed_path):
            if "error" in pf:
                continue
            for st in pf["strings"]:
                host = obj_index.get(
                    ("method", pf["file"], st.get("ctx_line"))) or obj_index.get(
                    ("class", pf["file"], st.get("cls_start")))
                parsed = sqlx.parse_sql(st["text"]) if not st.get("template") else None
                if parsed is None and not st.get("template"):
                    parsed = None
                if parsed is None:
                    # 动态 SQL 或解析失败：表名靠 SELECT/INSERT 后单词粗提取
                    rough = _rough_tables(st["text"])
                    for t in rough:
                        tbl = table_cache.get(t)
                        if tbl is None:
                            tbl = models.DataTable(scan_id=self.scan.id,
                                                   name=t, source="string")
                            self.s.add(tbl)
                            self.s.flush()
                            table_cache[t] = tbl
                        self.s.add(models.SqlOperation(
                            scan_id=self.scan.id, table_id=tbl.id, table_name=t,
                            op=_rough_op(st["text"]),
                            host_obj_id=host.id if host else None,
                            sql_text=st["text"][:2000],
                            confidence=40 if st.get("template") else 60,
                            file_path=pf["file"], line=st["line"]))
                        sql_ops += 1
                    continue
                for item in parsed:
                    tbl = table_cache.get(item["table"])
                    if tbl is None:
                        tbl = models.DataTable(scan_id=self.scan.id,
                                               name=item["table"], source="string")
                        self.s.add(tbl)
                        self.s.flush()
                        table_cache[item["table"]] = tbl
                    self.s.add(models.SqlOperation(
                        scan_id=self.scan.id, table_id=tbl.id,
                        table_name=item["table"], op=models.SqlOp(item["op"]),
                        fields=item["fields"],
                        host_obj_id=host.id if host else None,
                        sql_text=st["text"][:2000],
                        confidence=90, file_path=pf["file"], line=st["line"]))
                    sql_ops += 1
        self.s.flush()
        self.s.commit()

        self.scan.stats = {
            "modules": len(module_cache),
            "classes": sum(1 for x in obj_index.values()
                           if x.obj_type != models.ObjType.METHOD),
            "methods": sum(1 for x in obj_index.values()
                           if x.obj_type == models.ObjType.METHOD),
            "call_edges": edges,
            "api_endpoints": api_count,
            "data_tables": len(table_cache),
            "sql_operations": sql_ops,
            "parsed_files": self.scan.meta.get("parsed_files", 0),
        }
        self.s.commit()

    # ---------- 分片 ----------
    def shard_plan(self, report: dict) -> dict:
        return probe_mod.build_shard_plan(
            report["modules_detail"], report["total_lines"], report["tier"])

    def _all_java_paths(self) -> list[Path]:
        return [
            f for f in sorted(self.root.rglob("*.java"))
            if not any(p.lower() in probe_mod.EXCLUDE_DIRS
                       for p in f.relative_to(self.root).parts[:-1])
        ]

    def _paths_for_shard(self, files: Iterable[str]) -> list[Path]:
        paths: list[Path] = []
        for file_name in files:
            rel = Path(file_name)
            if rel.suffix.lower() != ".java":
                continue
            if any(part.lower() in probe_mod.EXCLUDE_DIRS for part in rel.parts[:-1]):
                continue
            paths.append(self.root / rel)
        return paths

    def _parse_shard_files(self, paths: Iterable[Path], sink, seen: set[str],
                           total: int, progress=None
                           ) -> tuple[int, list[dict[str, str]], bool]:
        start_total = total
        parse_errors: list[dict[str, str]] = []
        truncated = False
        for path in paths:
            rel = path.relative_to(self.root)
            key = str(rel)
            if key in seen:
                continue
            if self.max_files and total >= self.max_files:
                truncated = True
                break
            seen.add(key)
            total += 1
            try:
                parsed = parse_mod.parse_file(path, key, _module_of(rel, self.module_depth))
            except Exception as exc:  # 单文件失败不中断
                message = str(exc)[:500]
                parse_errors.append({"file": key, "error": message})
                parsed = {"file": key, "error": message}
            sink.write(json.dumps(parsed, ensure_ascii=False) + "\n")
            if progress and total % 100 == 0:
                progress(total)
        if progress and total != start_total and total % 100:
            progress(total)
        return total, parse_errors, truncated

    def _iter_parsed(self, parsed_path: Path):
        with parsed_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                parsed = json.loads(line)
                if parsed.get("error"):
                    continue
                yield parsed

    def _mark_task(self, task: models.ScanTask, status: models.TaskStatus,
                   error: str | None = None) -> None:
        task.status = status
        if status == models.TaskStatus.RUNNING:
            task.started_at = models.utcnow()
            task.error = None
        if status in (models.TaskStatus.DONE, models.TaskStatus.FAILED):
            task.finished_at = models.utcnow()
        if error:
            task.error = error[:2000]
        self.s.commit()


def _module_of(rel: Path, depth: int) -> str:
    parts = rel.parts
    d = min(depth, len(parts) - 1)
    return "/".join(parts[:d])


def _method_fq_name(package: str, method: dict) -> str | None:
    name = method.get("name")
    class_name = method.get("class_name")
    if not name:
        return None
    head = ".".join(part for part in (package, class_name, name) if part)
    return f"{head}{method.get('params') or '()'}"


def _resolve_call(call: dict, method_index: dict[tuple[str, str], list[tuple[int, models.CodeObject]]]
                  ) -> tuple[models.CodeObject | None, models.ResolveType]:
    receiver = call.get("receiver")
    if not receiver or call.get("receiver_kind") != "identifier":
        return None, models.ResolveType.UNKNOWN

    candidates = method_index.get((receiver, call["method"]), [])
    if not candidates:
        return None, models.ResolveType.UNKNOWN

    exact = [obj for argc, obj in candidates if argc == call.get("arg_count", 0)]
    if len(exact) == 1:
        return exact[0], models.ResolveType.STATIC
    return None, models.ResolveType.UNKNOWN


def _rough_tables(sql_text: str) -> list[str]:
    out = []
    for kw in ("from", "into", "update", "join"):
        for m in re.finditer(rf"\b{kw}\s+([A-Za-z_][A-Za-z0-9_$]*)",
                             sql_text, re.IGNORECASE):
            t = m.group(1)
            if t.lower() not in ("select", "dual", "where", "set"):
                out.append(t)
    return list(dict.fromkeys(out))


def _rough_op(sql_text: str) -> models.SqlOp:
    first = re.match(r"\s*(select|insert|update|delete|merge)", sql_text, re.I)
    return models.SqlOp((first.group(1).lower() if first else "select"))

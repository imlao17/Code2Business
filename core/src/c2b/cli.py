"""c2b CLI：项目创建、探测、扫描。"""
import json
import sys
from datetime import datetime
from pathlib import Path

import click

from . import models
from .db import SessionLocal, init_db
from .scanner.graph import Scanner


@click.group()
def cli():
    """Code2Business —— 遗留系统业务知识还原（MVP）"""


@cli.command()
def init():
    """初始化数据库（建表）。"""
    init_db()
    click.echo("数据库初始化完成")


@cli.command()
@click.argument("code_root", type=click.Path(exists=True, path_type=Path))
@click.option("--name", help="项目名（默认取目录名）")
@click.option("--git-commit", default=None, help="Git commit（可选）")
@click.option("--module-depth", default=2, help="模块归属目录深度（默认 2）")
@click.option("--max-files", default=None, type=int, help="限制解析文件数（调试用）")
@click.option("--probe-only", is_flag=True, help="只做探测，不解析")
@click.option("--project-id", default=None, type=int,
              help="复用已有项目并创建新的扫描（用于接续 probe-only）")
@click.option("--json", "json_out", is_flag=True, help="输出 JSON")
def scan(code_root: Path, name, git_commit, module_depth, max_files,
         probe_only, project_id, json_out):
    """扫描代码仓库：探测 → 分片 → 解析 → 图谱落库。"""
    init_db()
    s = SessionLocal()
    try:
        if project_id is not None:
            existing = s.get(models.Project, project_id)
            if existing is None:
                raise click.ClickException(f"项目 {project_id} 不存在")
            if Path(existing.repo_path).resolve() != code_root.resolve():
                raise click.ClickException(
                    "--project-id 对应的代码目录与本次 code_root 不一致，"
                    "为避免串写项目已拒绝")
        sc = Scanner(s, code_root, name or code_root.name,
                     git_commit=git_commit, module_depth=module_depth,
                     max_files=max_files)
        sc.create(project_id=project_id)
        if not json_out:
            click.echo(f"[1/4] 探测 {code_root} ...")
        report = sc.run_probe()
        plan = sc.shard_plan(report)

        if probe_only:
            # run_probe already committed PROBING + scale metadata.  Do not
            # call finish(): no code graph exists yet, so DONE would be false.
            _echo_report(
                report, plan, json_out,
                project_id=sc.project.id,
                scan_id=sc.scan.id,
                status=sc.scan.status.value,
            )
            return

        click.echo(f"[2/4] 档位 {report['tier']}，"
                   f"{report['total_lines']:,} 行 / {report['total_files']} 文件")
        if plan["enabled"]:
            click.echo(f"[3/4] 分片 {plan['shard_count']} 片并行解析（MVP 串行执行）")
            shards = plan["shards"]
        else:
            click.echo(f"[3/4] 无需分片（{plan['reason']}），全量解析")
            shards = None

        def progress(n):
            if n % 500 == 0:
                click.echo(f"      已解析 {n} 个文件 ...")

        sc.run_parse(report, shards=shards, progress=progress)
        sc.finish()
        click.echo(f"[4/4] 完成")
        click.echo(json.dumps(sc.scan.stats, ensure_ascii=False, indent=2))
        click.echo(f"项目 ID: {sc.project.id}，扫描 ID: {sc.scan.id}")
    except Exception as e:
        try:
            sc.fail(str(e))
        except Exception:
            pass
        click.echo(f"扫描失败: {e}", err=True)
        sys.exit(1)
    finally:
        s.close()


def _echo_report(report: dict, plan: dict, json_out: bool, *,
                 project_id: int, scan_id: int, status: str) -> None:
    if json_out:
        click.echo(json.dumps({
            "project_id": project_id,
            "scan_id": scan_id,
            "status": status,
            "scale": report,
            "shard_plan": plan,
        },
                              ensure_ascii=False, indent=2))
        return
    click.echo(f"项目 ID: {project_id}，探测扫描 ID: {scan_id}")
    click.echo("说明: 本次仅完成规模探测，尚未生成代码图谱。")
    m = report
    click.echo(f"档位: {m['tier']}")
    click.echo(f"代码文件: {m['total_files']}，总行数: {m['total_lines']:,}")
    click.echo(f"已排除产物: {m['excluded_files']} 文件")
    click.echo(f"疑似文件级历史版本: {m['version_files']} 文件 / "
               f"{m['version_lines']:,} 行")
    click.echo(f"编码分布(抽样): {m['encoding_samples']}")
    click.echo("模块 Top10:")
    for mod in m["module_lines_top"][:10]:
        click.echo(f"  - {mod['module']}: {mod['lines']:,} 行 / {mod['files']} 文件")
    if plan["enabled"]:
        click.echo(f"分片方案: {plan['shard_count']} 片，"
                   f"目标每片 {plan['target_lines_per_shard']:,} 行")
    else:
        click.echo(f"分片: {plan['reason']}")


@cli.command()
@click.argument("project_id", type=int)
@click.option("--limit", default=50, help="标注方法数上限（默认 50）")
@click.option("--concurrency", default=16, help="LLM 并发数（默认 16）")
@click.option("--entry-only", is_flag=True, help="只标注入口方法")
@click.option("--no-cache", is_flag=True, help="禁用 LLM 结果缓存")
@click.option("--model", default=None, help="LLM 模型（如 deepseek-chat）")
def label(project_id, limit, concurrency, entry_only, no_cache, model):
    """AI 语义标注：业务域 → 规则卡片 → 可信度落库。"""
    from .llm import LLMClient
    from .semantics.run import run_label

    init_db()
    s = SessionLocal()
    try:
        client = LLMClient(cache=not no_cache, model=model or None)
        result = run_label(s, project_id, limit=limit, concurrency=concurrency,
                           entry_only=entry_only, client=client)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        click.echo(f"标注失败: {e}", err=True)
        sys.exit(1)
    finally:
        s.close()


@cli.command("agent-schema")
def agent_schema():
    """输出 Agent Native 语义 JSON 模板（无需 API Key）。"""
    from .semantics.agent_native import AGENT_SEMANTIC_TEMPLATE

    click.echo(json.dumps(AGENT_SEMANTIC_TEMPLATE, ensure_ascii=False, indent=2))


@cli.command("export-evidence")
@click.argument("project_id", type=int)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path),
              required=True, help="导出的图谱证据 JSON 文件")
@click.option("--max-methods", default=500, show_default=True, type=int,
              help="导出的方法数上限")
def export_evidence_command(project_id: int, output: Path, max_methods: int):
    """导出供当前 Agent 阅读的确定性图谱证据（无需 API Key）。"""
    from .semantics.agent_native import AgentSemanticError, export_evidence

    init_db()
    s = SessionLocal()
    try:
        evidence = export_evidence(s, project_id, max_methods=max_methods)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        click.echo(f"已导出 Agent Native 证据：{output}（{len(evidence['methods'])} 个方法）")
    except AgentSemanticError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        s.close()


@cli.command("import-semantic")
@click.argument("project_id", type=int)
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False,
              readable=True, path_type=Path), required=True,
              help="当前 Agent 生成的语义 JSON 文件")
def import_semantic_command(project_id: int, input_path: Path):
    """校验并导入 Agent Native 的业务域、规则、对象、流程和推演。"""
    from .semantics.agent_native import AgentSemanticError, import_semantic

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"无法读取语义 JSON: {exc}") from exc
    init_db()
    s = SessionLocal()
    try:
        result = import_semantic(s, project_id, payload)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except AgentSemanticError as exc:
        raise click.ClickException(f"未导入：{exc}") from exc
    finally:
        s.close()


@cli.command()
@click.argument("project_id", type=int)
def status(project_id):
    """查看项目处理流程状态（pipeline）。"""
    from sqlalchemy import select
    from .api.routes import pipeline as pipeline_fn
    init_db()
    s = SessionLocal()
    try:
        p = pipeline_fn(project_id, s)
        click.echo(f"项目 {project_id} 流程状态：")
        for st in p["steps"]:
            mark = {"done": "✅", "running": "🔄", "pending": "⬜",
                    "ready": "➡️"}[st["status"]]
            extra = ""
            if st.get("count") is not None:
                extra = f"（{st['count']} 项）"
            elif st.get("stage"):
                extra = f"（{st['stage']} {st.get('progress', 0)}/{st.get('total', 0)}）"
            click.echo(f"  {mark} {st['label']}{extra}")
        if p["next"]:
            click.echo(f"下一步：{p['next']['label']}（{p['next']['page']}）")
    finally:
        s.close()


@cli.command("evaluate")
@click.argument("project_id", type=int)
@click.option("--manifest", "manifest_path", type=click.Path(
    exists=True, dir_okay=False, readable=True, path_type=Path), required=True,
              help="已知代码事实的质量基线 JSON 清单")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path),
              default=None, help="可选：写入 JSON 结果文件")
def evaluate(project_id: int, manifest_path: Path, output: Path | None):
    """针对已完成扫描执行可复跑的确定性质量基线检查。"""
    from .evaluation import EvaluationError, evaluate_scan, load_manifest

    init_db()
    s = SessionLocal()
    try:
        manifest = load_manifest(manifest_path)
        result = evaluate_scan(s, project_id, manifest)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
        click.echo(rendered)
        if result["missing"]:
            raise click.ClickException(
                "质量基线未通过：" + ", ".join(result["missing"])
            )
    except EvaluationError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        s.close()


@cli.command()
@click.argument("project_id", type=int)
@click.option("--module", "module_id", type=int, default=None,
              help="指定模块 id 分析；缺省则全部模块")
@click.option("--force", is_flag=True, help="绕过缓存强制重新分析")
@click.option("--name", default=None, help="按模块名匹配（如 Sms01206）")
def module_specs(project_id, module_id, force, name):
    """模块需求规格分析：全部模块或指定模块。"""
    from sqlalchemy import select
    from .llm import LLMClient
    from .semantics.module_analysis import analyze_module
    from . import models
    init_db()
    s = SessionLocal()
    try:
        scan = s.execute(
            select(models.Scan).where(
                models.Scan.project_id == project_id,
                models.Scan.status == models.ScanStatus.DONE,
            )
            .order_by(models.Scan.id.desc()).limit(1)).scalar_one_or_none()
        if scan is None:
            click.echo("项目还没有扫描记录，先执行 c2b scan", err=True)
            sys.exit(1)
        client = LLMClient(cache=not force)
        mods = []
        if module_id:
            m = s.get(models.Module, module_id)
            if m and m.scan_id == scan.id:
                mods = [m]
        elif name:
            mods = list(s.execute(
                select(models.Module).where(
                    models.Module.scan_id == scan.id,
                    models.Module.name.contains(name))).scalars().all())
        else:
            mods = list(s.execute(
                select(models.Module).where(models.Module.scan_id == scan.id)
                .order_by(models.Module.lines.desc())).scalars().all())
        if not mods:
            click.echo("未找到匹配模块", err=True)
            sys.exit(1)
        for i, m in enumerate(mods, 1):
            click.echo(f"[{i}/{len(mods)}] 分析 {m.name} ...")
            r = analyze_module(s, scan.id, m.id, client, force=force)
            cached = "（缓存）" if r.get("cached") else "（新生成）"
            click.echo(f"  完成{cached}：{r['content'].get('module_name', m.name)}")
    finally:
        s.close()


@cli.command()
@click.argument("project_id", type=int)
@click.option("--type", "doc_type", default="overview",
              help="文档类型: overview/functions/rules/dictionary/apis/acceptance")
@click.option("--fmt", default="md", help="md / word")
@click.option("--output", default=None, help="输出文件路径（默认打印到 stdout）")
@click.option("--draft", is_flag=True, help="包含 AI 未确认业务知识（正式文档默认不包含）")
def docs(project_id, doc_type, fmt, output, draft):
    """生成交付文档。"""
    from sqlalchemy import select
    from . import models
    from .documents import DOC_TYPES, _gather, render, to_word
    init_db()
    s = SessionLocal()
    try:
        if doc_type not in DOC_TYPES:
            click.echo(f"未知文档类型: {doc_type}，可选 {list(DOC_TYPES)}", err=True)
            sys.exit(1)
        scan = s.execute(
            select(models.Scan).where(
                models.Scan.project_id == project_id,
                models.Scan.status == models.ScanStatus.DONE,
            )
            .order_by(models.Scan.id.desc()).limit(1)).scalar_one_or_none()
        if scan is None:
            click.echo("项目还没有扫描记录", err=True)
            sys.exit(1)
        d = _gather(s, scan.id, include_unconfirmed=draft)
        md = render(doc_type, d)
        if fmt == "word":
            bio = to_word(md, DOC_TYPES[doc_type][0])
            data = bio.getvalue()
        else:
            data = md.encode("utf-8")
        if output:
            Path(output).write_bytes(data)
            click.echo(f"已写入 {output}")
        else:
            click.echo(md)
    finally:
        s.close()


@cli.command("pipeline")
@click.argument("code_root", type=click.Path(exists=True, file_okay=False,
                                              path_type=Path))
@click.option("--name", default=None, help="项目名（默认取目录名）")
@click.option("--project-id", default=None, type=int,
              help="复用已有项目（缺省则新建项目）")
@click.option("--out", "out_dir", default="c2b-deliverables",
              type=click.Path(file_okay=False, path_type=Path),
              show_default=True, help="交付文档输出目录")
@click.option("--fmt", default="both", type=click.Choice(["md", "word", "both"]),
              help="交付文档格式（默认同时输出 Markdown 与 Word）")
@click.option("--draft/--official", default=True,
              help="文档口径：草稿（默认，含 AI 未确认项）/ 正式（仅人工确认项）")
@click.option("--no-ai", is_flag=True,
              help="只做确定性扫描，跳过 AI 标注与模块需求规格")
@click.option("--limit", default=200, show_default=True, help="标注方法数上限")
@click.option("--concurrency", default=8, show_default=True, help="LLM 并发数")
def pipeline(code_root: Path, name, project_id, out_dir: Path, fmt, draft,
             no_ai, limit, concurrency):
    """一条命令：扫描 → AI 标注 → 模块需求规格 → 全部交付文档落盘。

    产出为草稿口径时可立即交付评审；正式口径文档仅包含人工已确认知识，
    请先在 Web 工作台或通过确认流程核实规则后使用 --official 重新导出。
    """
    from .automation import ai_ready, run_auto
    from .documents import DOC_TYPES, _gather, render, to_word
    from sqlalchemy import select

    init_db()
    s = SessionLocal()
    try:
        if project_id is not None:
            project = s.get(models.Project, project_id)
            if project is None:
                raise click.ClickException(f"项目 {project_id} 不存在")
        else:
            project = models.Project(name=name or code_root.name,
                                     repo_path=str(code_root))
            s.add(project)
            s.commit()
            click.echo(f"已创建项目 ID: {project.id}")
        pid = project.id
    finally:
        s.close()

    last_shown = {"key": None}

    def progress(stage: str, done=None, total=None):
        key = (stage, done, total)
        if last_shown["key"] == key:
            return
        last_shown["key"] = key
        if stage == "scan" or stage.startswith("scan:"):
            click.echo(f"[1/3] 确定性扫描 {stage} ...")
        elif stage == "label" or stage.startswith("label:"):
            if done is None:
                click.echo("[2/3] AI 语义标注 ...")
            else:
                click.echo(f"[2/3] AI 语义标注 {stage.split(':', 1)[-1]} "
                           f"{done}/{total}")
        elif stage == "modules":
            click.echo(f"[3/3] 模块需求规格 {done}/{total}")

    try:
        result = run_auto(pid, run_ai=not no_ai, label_limit=limit,
                          concurrency=concurrency, progress_cb=progress)
    except Exception as e:
        click.echo(f"自动分析失败: {e}", err=True)
        sys.exit(1)

    for note in result.get("notes", []):
        click.echo(f"说明：{note}")

    s = SessionLocal()
    try:
        scan = s.execute(
            select(models.Scan).where(
                models.Scan.project_id == pid,
                models.Scan.status == models.ScanStatus.DONE,
            ).order_by(models.Scan.id.desc()).limit(1)).scalar_one_or_none()
        if scan is None:
            raise click.ClickException("没有已完成的扫描，无法生成文档")
        d = _gather(s, scan.id, include_unconfirmed=draft)
        if not draft and not d["rules"]:
            click.echo("提醒：正式口径下没有任何已确认规则，正式文档将接近空壳；"
                       "请先确认规则或改用 --draft。", err=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for n, (key, (title, _desc)) in enumerate(DOC_TYPES.items(), 1):
            md = render(key, d)
            if fmt in ("md", "both"):
                p = out_dir / f"{n:02d}-{key}-{title}.md"
                p.write_text(md, encoding="utf-8")
                written.append(p)
            if fmt in ("word", "both"):
                p = out_dir / f"{n:02d}-{key}-{title}.docx"
                p.write_bytes(to_word(md, title).getvalue())
                written.append(p)
    finally:
        s.close()

    click.echo("")
    click.echo(f"完成：项目 {pid}，交付文档 {len(written)} 份已写入 {out_dir}/")
    for p in written:
        click.echo(f"  - {p}")
    if draft:
        click.echo("当前为草稿口径（含 AI 未确认内容）；人工确认规则后可用 "
                   "--official 重新导出正式版。")


@cli.command()
@click.argument("project_id", type=int)
@click.argument("question")
def qa(project_id, question):
    """AI 问答（基于业务图谱）。"""
    from sqlalchemy import select
    from . import models
    from .llm import LLMClient
    from .semantics.qa import answer
    init_db()
    s = SessionLocal()
    try:
        scan = s.execute(
            select(models.Scan).where(
                models.Scan.project_id == project_id,
                models.Scan.status == models.ScanStatus.DONE,
            )
            .order_by(models.Scan.id.desc()).limit(1)).scalar_one_or_none()
        if scan is None:
            click.echo("项目还没有扫描记录", err=True)
            sys.exit(1)
        r = answer(s, scan.id, question, LLMClient())
        click.echo(f"结论：{r['conclusion']}")
        click.echo(f"触发：{r['trigger'] or '-'}")
        click.echo("步骤：")
        for st in r.get("steps", []):
            click.echo(f"  - {st}")
        if r.get("evidence"):
            click.echo("证据：")
            for e in r["evidence"][:5]:
                click.echo(f"  - {e}")
        click.echo(f"可信度：{r['confidence']}%")
        if r.get("uncovered"):
            click.echo(f"未覆盖：{'；'.join(r['uncovered'][:3])}")
    finally:
        s.close()


@cli.command()
@click.argument("project_id", type=int)
@click.option("--output", default=None, help="zip 输出路径（默认 module-specs.zip）")
def export_specs(project_id, output):
    """打包下载全部模块需求规格（zip）。"""
    import zipfile
    from sqlalchemy import select
    from . import models
    from .semantics.module_analysis import materialize_analysis
    init_db()
    s = SessionLocal()
    try:
        scan = s.execute(
            select(models.Scan).where(
                models.Scan.project_id == project_id,
                models.Scan.status == models.ScanStatus.DONE,
            )
            .order_by(models.Scan.id.desc()).limit(1)).scalar_one_or_none()
        if scan is None:
            click.echo("项目还没有扫描记录", err=True)
            sys.exit(1)
        rows = list(s.execute(
            select(models.ModuleAnalysis)
            .where(models.ModuleAnalysis.scan_id == scan.id)
            .order_by(models.ModuleAnalysis.module_name)).scalars().all())
        if not rows:
            click.echo("还没有已分析的模块，先执行 c2b module-specs", err=True)
            sys.exit(1)
        out = output or "module-specs.zip"
        module_names = dict(s.execute(
            select(models.Module.id, models.Module.name).where(
                models.Module.scan_id == scan.id
            )
        ).all())
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            index = ["# 模块需求规格索引\n"]
            for a in rows:
                canonical_name = module_names.get(a.module_id, a.module_name)
                _content, markdown = materialize_analysis(a, canonical_name)
                name = canonical_name.replace("/", "__").replace("\\", "__")
                zf.writestr(f"{name}-需求规格.md", markdown)
                index.append(f"- {canonical_name}")
            zf.writestr("00-索引.md", "\n".join(index))
        click.echo(f"已打包 {len(rows)} 份规格到 {out}")
    finally:
        s.close()


if __name__ == "__main__":
    cli()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.cli import cli
from c2b.db import Base


class ProbeOnlyCliTests(unittest.TestCase):
    def test_probe_only_returns_reusable_ids_without_marking_scan_done(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
            engine = create_engine(f"sqlite:///{Path(td) / 'cli.db'}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine, expire_on_commit=False)

            with patch("c2b.cli.init_db"), patch("c2b.cli.SessionLocal", Session):
                result = CliRunner().invoke(
                    cli, ["scan", str(root), "--probe-only", "--json"]
                )

            self.assertEqual(result.exit_code, 0, result.output)
            payload, _ = json.JSONDecoder().raw_decode(
                result.output[result.output.index("{"):]
            )
            self.assertIsInstance(payload["project_id"], int)
            self.assertIsInstance(payload["scan_id"], int)
            self.assertEqual(payload["status"], "probing")
            with Session() as session:
                scan = session.get(models.Scan, payload["scan_id"])
                self.assertEqual(scan.status, models.ScanStatus.PROBING)
                project_id = scan.project_id

            with patch("c2b.cli.init_db"), patch("c2b.cli.SessionLocal", Session):
                resumed = CliRunner().invoke(
                    cli,
                    [
                        "scan", str(root), "--project-id", str(project_id),
                        "--max-files", "1",
                    ],
                )
            self.assertEqual(resumed.exit_code, 0, resumed.output)
            with Session() as session:
                projects = session.execute(select(models.Project)).scalars().all()
                scans = session.execute(select(models.Scan)).scalars().all()
                self.assertEqual(len(projects), 1)
                self.assertEqual(len(scans), 2)
                self.assertEqual(scans[-1].status, models.ScanStatus.DONE)
            engine.dispose()


class PipelineCliTests(unittest.TestCase):
    def test_pipeline_no_ai_writes_full_deliverable_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / "Demo.java").write_text(
                "public class Demo {\n"
                "    /** 创建订单 */\n"
                "    public void createOrder(int a) {\n"
                "        if (a > 0) {\n"
                "        }\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            out = Path(td) / "deliverables"
            engine = create_engine(f"sqlite:///{Path(td) / 'pipe.db'}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine, expire_on_commit=False)

            with patch("c2b.cli.init_db"), patch(
                "c2b.cli.SessionLocal", Session
            ), patch("c2b.db.SessionLocal", Session), patch(
                "c2b.settings_store.CONFIG_FILE", Path(td) / "cfg.json"
            ):
                result = CliRunner().invoke(cli, [
                    "pipeline", str(root), "--name", "demo",
                    "--no-ai", "--fmt", "md", "--out", str(out),
                ])

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("跳过 AI 标注", result.output)
            files = sorted(p.name for p in out.iterdir())
            # 7 类交付文档各一份 Markdown（combined 置首）
            self.assertEqual(len(files), 7)
            self.assertEqual(files[0], "01-combined-客户交付总报告.md")
            combined = (out / files[0]).read_text(encoding="utf-8")
            self.assertIn("客户交付总报告", combined)
            self.assertIn("## 执行摘要", combined)
            engine.dispose()

    def test_pipeline_reuses_project_and_official_mode_warns_without_rules(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / "Demo.java").write_text(
                "public class Demo {\n"
                "    /** 创建订单 */\n"
                "    public void createOrder(int a) {\n"
                "        if (a > 0) {\n"
                "        }\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            out = Path(td) / "deliverables"
            engine = create_engine(f"sqlite:///{Path(td) / 'pipe.db'}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine, expire_on_commit=False)

            with patch("c2b.cli.init_db"), patch(
                "c2b.cli.SessionLocal", Session
            ), patch("c2b.db.SessionLocal", Session), patch(
                "c2b.settings_store.CONFIG_FILE", Path(td) / "cfg.json"
            ):
                first = CliRunner().invoke(cli, [
                    "pipeline", str(root), "--name", "demo",
                    "--no-ai", "--fmt", "md", "--out", str(out),
                ])
                self.assertEqual(first.exit_code, 0, first.output)
                with Session() as session:
                    project_id = session.execute(
                        select(models.Project.id)).scalars().first()
                second = CliRunner().invoke(cli, [
                    "pipeline", str(root), "--project-id", str(project_id),
                    "--no-ai", "--official", "--fmt", "md", "--out", str(out),
                ])

            self.assertEqual(second.exit_code, 0, second.output)
            # 复用项目：不再新建
            with Session() as session:
                self.assertEqual(len(list(session.execute(
                    select(models.Project)).scalars())), 1)
            # 正式口径下没有已确认规则 → 输出提醒
            self.assertIn("没有任何已确认规则", second.output)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

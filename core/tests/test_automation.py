import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.automation import ai_ready, run_auto
from c2b.db import Base


class AutomationTestBase(unittest.TestCase):
    """为 run_auto 准备独立数据库与微型 Java 仓库。

    run_auto 在调用时从 c2b.db 取 SessionLocal，因此测试通过 patch
    ``c2b.db.SessionLocal`` 注入会话工厂；settings_store 的配置文件同样
    指到临时目录，避免读到开发机的真实 ~/.c2b/config.json。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "Demo.java").write_text(
            "package demo;\n"
            "public class Demo {\n"
            "    /** 创建订单 */\n"
            "    public void createOrder(int amount) {\n"
            "        if (amount > 100) {\n"
            "            throw new IllegalArgumentException(\"超限\");\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        self.settings_json = Path(self.tmp.name) / "config.json"
        engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'auto.db'}")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        s = self.Session()
        self.project = models.Project(name="demo", repo_path=str(self.repo))
        s.add(self.project)
        s.commit()
        s.close()
        self._stack = ExitStack()
        self._stack.enter_context(patch("c2b.db.SessionLocal", self.Session))
        self._stack.enter_context(
            patch("c2b.settings_store.CONFIG_FILE", self.settings_json))

    def tearDown(self):
        self._stack.close()
        self.Session.kw["bind"].dispose()
        self.tmp.cleanup()

    def done_scans(self):
        s = self.Session()
        try:
            return list(s.execute(
                select(models.Scan).where(
                    models.Scan.status == models.ScanStatus.DONE)).scalars())
        finally:
            s.close()


class UnconfiguredAiTests(AutomationTestBase):
    def test_ai_ready_false_without_key_or_mock(self):
        self.assertFalse(ai_ready())

    def test_run_auto_stops_after_scan_and_explains_gap(self):
        notes: list[str] = []
        stages: list[str] = []
        result = run_auto(
            self.project.id,
            progress_cb=lambda stage, done, total: stages.append(stage),
        )
        result_notes = result["notes"]
        s = self.Session()
        try:
            questions = s.execute(select(models.BusinessRule)).scalars().all()
        finally:
            s.close()
        self.assertTrue(result["scanned"])
        self.assertFalse(result["ai_ran"])
        self.assertEqual(questions, [])
        self.assertEqual(len(self.done_scans()), 1)
        self.assertTrue(any("尚未配置 AI 服务" in n for n in result_notes))
        self.assertTrue(any(stage.startswith("scan") for stage in stages))
        self.assertFalse(any(stage.startswith("label") for stage in stages))
        notes.clear()


class NoAiFlagTests(AutomationTestBase):
    def test_run_auto_with_no_ai_skips_semantic_stages(self):
        result = run_auto(self.project.id, run_ai=False)
        self.assertTrue(result["scanned"])
        self.assertFalse(result["ai_ran"])
        self.assertTrue(any("跳过 AI 标注" in n for n in result["notes"]))
        s = self.Session()
        try:
            self.assertEqual(
                list(s.execute(select(models.BusinessRule)).scalars()), [])
        finally:
            s.close()


class MockAiPipelineTests(AutomationTestBase):
    def test_run_auto_full_pipeline_with_mock_llm(self):
        stages: list[str] = []
        with patch.dict("os.environ", {"C2B_LLM_MODE": "mock"}):
            self.assertTrue(ai_ready())
            result = run_auto(
                self.project.id,
                progress_cb=lambda stage, done, total: stages.append(stage),
            )
        self.assertTrue(result["scanned"])
        self.assertTrue(result["ai_ran"])
        self.assertIn("label", result)
        self.assertGreaterEqual(result["label"]["rules"], 1)
        self.assertFalse(any("尚未配置" in n for n in result["notes"]))
        s = self.Session()
        try:
            rules = list(s.execute(select(models.BusinessRule)).scalars())
            analyses = list(s.execute(select(models.ModuleAnalysis)).scalars())
        finally:
            s.close()
        # mock 模式确定性产出规则卡片，模块分析不失败
        self.assertGreater(len(rules), 0)
        self.assertEqual(result["modules"]["failed"], 0)
        self.assertEqual(result["modules"]["analyzed"], len(analyses))
        self.assertTrue(any(stage.startswith("label") for stage in stages))
        self.assertTrue(any(stage == "modules" for stage in stages))

    def test_second_run_reuses_existing_scan(self):
        with patch.dict("os.environ", {"C2B_LLM_MODE": "mock"}):
            run_auto(self.project.id, run_ai=False)
            result = run_auto(self.project.id, run_ai=False)
        self.assertFalse(result["scanned"])
        self.assertEqual(len(self.done_scans()), 1)


class MissingProjectTests(AutomationTestBase):
    def test_run_auto_rejects_unknown_project(self):
        with self.assertRaises(ValueError):
            run_auto(99999)

    def test_run_auto_rejects_unavailable_code_root(self):
        s = self.Session()
        try:
            ghost = models.Project(name="ghost",
                                   repo_path=str(Path(self.tmp.name) / "nope"))
            s.add(ghost)
            s.commit()
            pid = ghost.id
        finally:
            s.close()
        with self.assertRaises(ValueError):
            run_auto(pid)


if __name__ == "__main__":
    unittest.main()

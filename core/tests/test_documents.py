import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from docx import Document
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.db import Base
from c2b.documents import (
    DOC_TYPES, _doc_acceptance, _doc_combined, _doc_functions, _doc_rules,
    _gather, _table, render, to_word,
)


class DocumentTrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'doc.db'}")
        Base.metadata.create_all(self.engine)
        self.s = sessionmaker(bind=self.engine, expire_on_commit=False)()
        project = models.Project(name="p", repo_path=self.tmp.name)
        self.s.add(project)
        self.s.flush()
        self.scan = models.Scan(project_id=project.id, status=models.ScanStatus.DONE)
        self.s.add(self.scan)
        self.s.flush()

    def tearDown(self):
        self.s.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_formal_documents_only_include_confirmed_or_modified_rules(self):
        self.s.add_all(
            [
                models.BusinessRule(
                    scan_id=self.scan.id, name="confirmed", state=models.RuleState.CONFIRMED
                ),
                models.BusinessRule(
                    scan_id=self.scan.id, name="modified", state=models.RuleState.MODIFIED
                ),
                models.BusinessRule(
                    scan_id=self.scan.id, name="draft", state=models.RuleState.AI_LABELED
                ),
                models.BusinessRule(
                    scan_id=self.scan.id, name="discarded", state=models.RuleState.DISCARDED
                ),
            ]
        )
        self.s.commit()

        formal = _gather(self.s, self.scan.id)
        draft = _gather(self.s, self.scan.id, include_unconfirmed=True)

        self.assertEqual({r.name for r in formal["rules"]}, {"confirmed", "modified"})
        self.assertEqual(
            {r.name for r in draft["rules"]}, {"confirmed", "modified", "draft"}
        )

    def test_formal_documents_exclude_unconfirmed_objects_flows_and_chain(self):
        domain = models.BusinessDomain(
            scan_id=self.scan.id, name="已确认域", confirmed=True
        )
        self.s.add(domain)
        self.s.flush()
        self.s.add_all(
            [
                models.BusinessObject(
                    scan_id=self.scan.id,
                    domain_id=domain.id,
                    name="已确认对象",
                    actions=["确认动作"],
                    confirmed=True,
                ),
                models.BusinessObject(
                    scan_id=self.scan.id,
                    domain_id=domain.id,
                    name="未确认对象",
                    actions=["不应进入正式验收"],
                    confirmed=False,
                ),
                models.BusinessFlow(
                    scan_id=self.scan.id, name="已确认流程", confirmed=True
                ),
                models.BusinessFlow(
                    scan_id=self.scan.id, name="未确认流程", confirmed=False
                ),
                models.BusinessChain(
                    scan_id=self.scan.id,
                    mainline=["未确认对象", "另一个对象"],
                    design_narrative="AI 推断主线",
                ),
            ]
        )
        self.s.commit()

        formal = _gather(self.s, self.scan.id)
        draft = _gather(self.s, self.scan.id, include_unconfirmed=True)
        acceptance = _doc_acceptance(formal)

        self.assertEqual([o.name for o in formal["objects"]], ["已确认对象"])
        self.assertEqual([f.name for f in formal["flows"]], ["已确认流程"])
        self.assertIsNone(formal["chain"])
        self.assertIn("已确认对象——确认动作", acceptance)
        self.assertNotIn("不应进入正式验收", acceptance)
        self.assertNotIn("AI 推断主线", acceptance)
        self.assertEqual(
            {o.name for o in draft["objects"]}, {"已确认对象", "未确认对象"}
        )
        self.assertEqual(
            {f.name for f in draft["flows"]}, {"已确认流程", "未确认流程"}
        )
        self.assertIsNotNone(draft["chain"])

    def test_function_document_does_not_repeat_every_object_under_every_domain(self):
        first = models.BusinessDomain(scan_id=self.scan.id, name="域一")
        second = models.BusinessDomain(scan_id=self.scan.id, name="域二")
        self.s.add_all([first, second])
        self.s.flush()
        self.s.add_all(
            [
                models.BusinessObject(
                    scan_id=self.scan.id, domain_id=first.id, name="对象一", actions=[]
                ),
                models.BusinessObject(
                    scan_id=self.scan.id, domain_id=None, name="未归类对象", actions=[]
                ),
            ]
        )
        self.s.commit()

        rendered = _doc_functions(
            _gather(self.s, self.scan.id, include_unconfirmed=True)
        )

        self.assertEqual(rendered.count("### 对象一"), 1)
        self.assertEqual(rendered.count("### 未归类对象"), 1)
        self.assertIn("## 未归类业务对象", rendered)
        self.assertIn("核实状态：AI 推断（未确认）", rendered)

    def test_markdown_tables_escape_pipes_and_multiline_cells(self):
        rendered = _table([
            ["字段", "内容"],
            ["A|B", "第一行\n第二行"],
        ])

        self.assertIn(r"A\|B", rendered)
        self.assertIn("第一行<br>第二行", rendered)

    def test_rule_document_shows_runtime_level_without_leaking_draft_targets(self):
        confirmed = models.BusinessRule(
            scan_id=self.scan.id, name="confirmed",
            confidence=72, state=models.RuleState.CONFIRMED,
        )
        draft = models.BusinessRule(
            scan_id=self.scan.id, name="draft",
            state=models.RuleState.AI_LABELED,
        )
        self.s.add_all([confirmed, draft])
        self.s.flush()
        self.s.add_all([
            models.RuntimeEvidence(
                scan_id=self.scan.id, target_type="rule",
                target_id=confirmed.id, level="R2", evidence_type="trace",
                environment="staging", summary="staging passed",
                reference="trace-123",
            ),
            models.RuntimeEvidence(
                scan_id=self.scan.id, target_type="rule",
                target_id=draft.id, level="R3", evidence_type="log",
                environment="production", summary="draft only",
            ),
        ])
        self.s.commit()

        formal = _gather(self.s, self.scan.id)
        rendered = _doc_rules(formal)

        self.assertEqual(len(formal["runtime_evidence"]), 1)
        self.assertIn("运行验证 | R2", rendered)
        self.assertIn("staging passed", rendered)
        self.assertNotIn("draft only", rendered)


class CombinedReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'doc.db'}")
        Base.metadata.create_all(self.engine)
        self.s = sessionmaker(bind=self.engine, expire_on_commit=False)()
        project = models.Project(name="老系统", repo_path=self.tmp.name)
        self.s.add(project)
        self.s.flush()
        self.scan = models.Scan(
            project_id=project.id, status=models.ScanStatus.DONE,
            meta={"total_lines": 1000, "total_files": 10}, stats={},
        )
        self.s.add(self.scan)
        self.s.flush()

    def tearDown(self):
        self.s.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_combined_is_registered_as_first_document_type(self):
        self.assertEqual(list(DOC_TYPES)[0], "combined")
        self.assertEqual(DOC_TYPES["combined"][0], "客户交付总报告")
        # render() 分发必须能取到 combined，前端文档页由 /documents/types 驱动
        md = render("combined", _gather(self.s, self.scan.id))
        self.assertIn("客户交付总报告", md)

    def test_combined_sections_and_scope_boundary(self):
        domain = models.BusinessDomain(
            scan_id=self.scan.id, name="订单域", confirmed=True
        )
        self.s.add(domain)
        self.s.flush()
        self.s.add_all([
            models.BusinessObject(
                scan_id=self.scan.id, domain_id=domain.id, name="订单",
                actions=["创建订单"], confirmed=True,
            ),
            models.BusinessRule(
                scan_id=self.scan.id, name="已确认规则",
                confidence=90, state=models.RuleState.CONFIRMED,
                logic="金额超限时需要审批",
            ),
            models.Question(
                scan_id=self.scan.id, desc="该定时任务的归属不明",
                suggestion="确认调度方", status="open",
            ),
            models.Question(
                scan_id=self.scan.id, desc="已解决的问题",
                status="resolved",
            ),
        ])
        self.s.commit()

        draft = _doc_combined(_gather(self.s, self.scan.id, include_unconfirmed=True))
        formal = _doc_combined(_gather(self.s, self.scan.id))

        for section in ("## 执行摘要", "## 1. 系统概览", "## 2. 业务功能",
                        "## 3. 业务流程与业务主线", "## 4. 核心业务规则",
                        "## 5. 数据概览", "## 6. 程序入口概览",
                        "## 7. 验收清单", "## 8. 待确认事项",
                        "## 9. 生成说明与可信度声明"):
            self.assertIn(section, draft)
        self.assertIn("该定时任务的归属不明", draft)
        self.assertNotIn("已解决的问题", draft)
        # 业务域只在功能章节出现一次（不重复现状说明书的域表）
        self.assertEqual(draft.count("订单域"), 1)
        # 草稿与正式的边界声明
        self.assertIn("本版本为草稿", draft)
        self.assertIn("仅包含已人工确认的业务知识", formal)
        self.assertIn("人工已确认规则 1 条", draft)

    def test_draft_combined_appendix_carries_full_rules_but_formal_does_not(self):
        self.s.add_all([
            models.BusinessRule(
                scan_id=self.scan.id, name="高可信规则",
                confidence=95, state=models.RuleState.AI_LABELED,
                logic="完整处理逻辑说明",
            ),
        ])
        self.s.commit()

        draft = _doc_combined(_gather(self.s, self.scan.id, include_unconfirmed=True))
        formal = _doc_combined(_gather(self.s, self.scan.id))

        self.assertIn("## 附录：完整业务规则清单", draft)
        self.assertIn("完整处理逻辑说明", draft)
        self.assertNotIn("## 附录：完整业务规则清单", formal)


class WordExportTests(unittest.TestCase):
    def test_word_tables_are_real_tables(self):
        md = "\n\n".join([
            "# 标题",
            _table([["指标", "数值"], ["代码行数", "1,000"], ["A|B", "第一行<br>第二行"]]),
            "正文段落，**关键结论**加粗。",
            "- [ ] 验收项：订单创建",
            "> 证据口径说明",
        ])
        data = to_word(md, "客户交付总报告").getvalue()

        doc = Document(BytesIO(data))
        self.assertEqual(len(doc.tables), 1)
        table = doc.tables[0]
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(table.cell(0, 0).text, "指标")
        self.assertEqual(table.cell(1, 1).text, "1,000")
        self.assertEqual(table.cell(2, 0).text, "A|B")
        self.assertEqual(table.cell(2, 1).text, "第一行\n第二行")
        # 表头加粗
        self.assertTrue(all(run.bold for run in table.cell(0, 0).paragraphs[0].runs))

        texts = [p.text for p in doc.paragraphs]
        self.assertTrue(any("□ 验收项：订单创建" in t for t in texts))
        self.assertTrue(any("关键结论" in t for t in texts))
        bold_run = next(
            run for p in doc.paragraphs for run in p.runs
            if run.text == "关键结论"
        )
        self.assertTrue(bold_run.bold)

    def test_word_render_of_combined_report_never_crashes(self):
        # 使用完整数据库夹具回归：确认 combined 全文可无损转 Word
        with tempfile.TemporaryDirectory() as td:
            engine = create_engine(f"sqlite:///{Path(td) / 'w.db'}")
            Base.metadata.create_all(engine)
            s = sessionmaker(bind=engine, expire_on_commit=False)()
            project = models.Project(name="p", repo_path=td)
            s.add(project)
            s.flush()
            scan = models.Scan(project_id=project.id, status=models.ScanStatus.DONE)
            s.add(scan)
            s.flush()
            s.add(models.BusinessRule(
                scan_id=scan.id, name="r1", confidence=80,
                state=models.RuleState.AI_LABELED, logic="逻辑",
            ))
            s.commit()
            md = _doc_combined(_gather(s, scan.id, include_unconfirmed=True))
            data = to_word(md, "客户交付总报告").getvalue()
            doc = Document(BytesIO(data))
            self.assertGreater(len(doc.paragraphs), 5)
            # 系统概览 + 核心规则摘要 + 附录规则卡片 至少三张表
            self.assertGreaterEqual(len(doc.tables), 3)
            s.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

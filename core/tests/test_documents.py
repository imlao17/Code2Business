import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.db import Base
from c2b.documents import _doc_acceptance, _doc_functions, _gather, _table


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


if __name__ == "__main__":
    unittest.main()

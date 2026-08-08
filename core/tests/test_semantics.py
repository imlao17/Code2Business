import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.db import Base
from c2b.llm import LLMClient
from c2b.semantics.chain import run_chain
from c2b.semantics.run import pick_methods, run_label


class _RulesFailClient:
    model = "failure-test"

    def chat_json(self, _system, _user, schema=None, **_kwargs):
        if schema and "domains" in schema.get("properties", {}):
            return {"domains": []}
        raise RuntimeError("simulated provider failure")


class _UnsupportedChainClient:
    model = "chain-test"

    def chat_json(self, _system, _user, schema=None, **_kwargs):
        return {
            "mainline": ["对象A", "并不存在的对象", "对象B"],
            "relationships": [{
                "from": "对象A",
                "to": "对象B",
                "relation": "驱动",
                "evidence": "模型自称有证据",
            }],
            "design_narrative": "模型推演",
            "assumptions": [],
        }


class RelabelSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'semantic.db'}")
        Base.metadata.create_all(self.engine)
        self.s = sessionmaker(bind=self.engine, expire_on_commit=False)()
        project = models.Project(name="p", repo_path=str(self.root))
        self.s.add(project)
        self.s.flush()
        self.project_id = project.id
        self.scan = models.Scan(project_id=project.id, status=models.ScanStatus.DONE)
        self.s.add(self.scan)
        self.s.flush()
        module = models.Module(
            scan_id=self.scan.id, name="businese/orders", lines=50, file_count=1
        )
        self.s.add(module)
        self.s.flush()
        source = self.root / "OrderService.java"
        source.write_text(
            "class OrderService {\n"
            "  void approveOrder() { if (true) { return; } }\n"
            "  void updateOrder() { if (true) { return; } }\n"
            "}\n",
            encoding="utf-8",
        )
        self.reviewed_method = models.CodeObject(
            scan_id=self.scan.id,
            module_id=module.id,
            obj_type=models.ObjType.METHOD,
            name="approveOrder",
            fq_name="OrderService.approveOrder()",
            file_path=source.name,
            start_line=2,
            end_line=2,
        )
        self.generated_method = models.CodeObject(
            scan_id=self.scan.id,
            module_id=module.id,
            obj_type=models.ObjType.METHOD,
            name="updateOrder",
            fq_name="OrderService.updateOrder()",
            file_path=source.name,
            start_line=3,
            end_line=3,
        )
        self.s.add_all([self.reviewed_method, self.generated_method])
        self.s.flush()
        self.reviewed = models.BusinessRule(
            scan_id=self.scan.id,
            source_method_id=self.reviewed_method.id,
            name="人工确认规则",
            logic="人工内容",
            state=models.RuleState.MODIFIED,
        )
        self.generated = models.BusinessRule(
            scan_id=self.scan.id,
            source_method_id=self.generated_method.id,
            name="旧 AI 规则",
            logic="旧内容",
            state=models.RuleState.AI_LABELED,
        )
        self.s.add_all([self.reviewed, self.generated])
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_relabel_preserves_human_decisions_and_replaces_generated_rule(self):
        result = run_label(
            self.s,
            self.project_id,
            limit=20,
            concurrency=1,
            client=LLMClient(mode="mock"),
        )

        rules = self.s.execute(
            select(models.BusinessRule).where(models.BusinessRule.scan_id == self.scan.id)
        ).scalars().all()
        reviewed = next(rule for rule in rules if rule.name == "人工确认规则")
        self.assertEqual(reviewed.logic, "人工内容")
        self.assertEqual(reviewed.state, models.RuleState.MODIFIED)
        self.assertNotIn("旧 AI 规则", {rule.name for rule in rules})
        self.assertEqual(result["preserved_human_rules"], 1)
        self.assertEqual(result["rules"], 1)

    def test_total_llm_failure_leaves_old_generated_data_untouched(self):
        with self.assertRaises(RuntimeError):
            run_label(
                self.s,
                self.project_id,
                limit=20,
                concurrency=1,
                client=_RulesFailClient(),
            )

        names = set(self.s.execute(
            select(models.BusinessRule.name).where(
                models.BusinessRule.scan_id == self.scan.id
            )
        ).scalars().all())
        self.assertEqual(names, {"人工确认规则", "旧 AI 规则"})

    def test_entry_only_is_applied_before_candidate_limit(self):
        entry = models.CodeObject(
            scan_id=self.scan.id,
            module_id=self.generated_method.module_id,
            obj_type=models.ObjType.METHOD,
            name="entryHandler",
            fq_name="OrderService.entryHandler()",
            file_path="OrderService.java",
            start_line=4,
            end_line=4,
            is_entry=True,
            entry_kind="api",
        )
        self.s.add(entry)
        self.s.flush()
        # Give the non-entry method a higher business score so it would occupy
        # the single result slot if truncation happened before entry filtering.
        self.s.add(models.SqlOperation(
            scan_id=self.scan.id,
            host_obj_id=self.generated_method.id,
            table_name="orders",
            op=models.SqlOp.UPDATE,
        ))
        self.s.commit()

        picked = pick_methods(
            self.s, self.scan.id, limit=1, entry_only=True
        )

        self.assertEqual([method.id for method in picked], [entry.id])


class ChainEvidenceTests(unittest.TestCase):
    def test_unverified_relationships_are_moved_to_assumptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'chain.db'}")
            Base.metadata.create_all(engine)
            session = sessionmaker(bind=engine, expire_on_commit=False)()
            try:
                project = models.Project(name="p", repo_path=tmp)
                session.add(project)
                session.flush()
                scan = models.Scan(
                    project_id=project.id, status=models.ScanStatus.DONE
                )
                session.add(scan)
                session.flush()
                session.add_all([
                    models.BusinessObject(scan_id=scan.id, name="对象A"),
                    models.BusinessObject(scan_id=scan.id, name="对象B"),
                ])
                session.flush()

                chain = run_chain(session, scan.id, _UnsupportedChainClient())

                self.assertEqual(chain.mainline, ["对象A", "对象B"])
                self.assertEqual(chain.relationships, [])
                self.assertEqual(chain.confidence, 30)
                self.assertTrue(any("无确定性证据" in item
                                    for item in chain.assumptions))
            finally:
                session.close()
                engine.dispose()


if __name__ == "__main__":
    unittest.main()

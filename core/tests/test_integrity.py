import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.api.routes import (
    BatchConfirmIn,
    ConfirmIn,
    RuntimeEvidenceIn,
    _create_project_job,
    _excel_safe,
    _latest_scan,
    analyze_all_modules,
    biz_chain,
    confirm_rule,
    confirm_rules_batch,
    delete_project,
    pipeline,
    runtime_evidence_create,
    runtime_evidence_delete,
    runtime_evidence_list,
)
from c2b.db import Base, make_engine, recover_stale_label_jobs


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "test.db"
        self.engine = make_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.s = self.Session()

    def tearDown(self):
        self.s.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def project_with_scan(self, name: str, status=models.ScanStatus.DONE):
        p = models.Project(name=name, repo_path=self.tmp.name)
        self.s.add(p)
        self.s.flush()
        scan = models.Scan(project_id=p.id, status=status)
        self.s.add(scan)
        self.s.flush()
        return p, scan


class LatestScanTests(DatabaseTestCase):
    def test_latest_scan_prefers_last_successful_result(self):
        project, good = self.project_with_scan("project")
        self.s.add_all(
            [
                models.Scan(project_id=project.id, status=models.ScanStatus.FAILED),
                models.Scan(project_id=project.id, status=models.ScanStatus.PARSING),
            ]
        )
        self.s.commit()

        self.assertEqual(_latest_scan(self.s, project.id).id, good.id)
        self.assertNotEqual(
            _latest_scan(self.s, project.id, completed_only=False).id, good.id
        )

    def test_legacy_unverified_chain_edges_are_hidden_from_the_graph(self):
        project, scan = self.project_with_scan("legacy-chain")
        self.s.add(models.BusinessChain(
            scan_id=scan.id,
            mainline=["A", "B"],
            relationships=[{
                "from": "A", "to": "B", "relation": "推断",
                "verification": "V0",
            }],
            assumptions=[],
            confidence=70,
        ))
        self.s.commit()

        result = biz_chain(project.id, self.s)

        self.assertEqual(result["relationships"], [])
        self.assertEqual(result["confidence"], 30)
        self.assertTrue(any("缺少确定性证据" in item
                            for item in result["assumptions"]))


class ConfirmationIsolationTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.p1, self.scan1 = self.project_with_scan("p1")
        self.p2, self.scan2 = self.project_with_scan("p2")
        self.rule1 = models.BusinessRule(
            scan_id=self.scan1.id,
            name="rule-one",
            logic="old",
            confidence=60,
            state=models.RuleState.AI_LABELED,
        )
        self.rule2 = models.BusinessRule(
            scan_id=self.scan2.id,
            name="rule-two",
            logic="old",
            confidence=60,
            state=models.RuleState.AI_LABELED,
        )
        self.s.add_all([self.rule1, self.rule2])
        self.s.commit()

    def test_single_confirmation_is_project_scoped_and_audited(self):
        with self.assertRaises(HTTPException) as cm:
            confirm_rule(
                self.p1.id,
                self.rule2.id,
                ConfirmIn(action="confirm"),
                self.s,
            )
        self.assertEqual(cm.exception.status_code, 404)

        result = confirm_rule(
            self.p1.id,
            self.rule1.id,
            ConfirmIn(action="modify", fields={"logic": "new"}),
            self.s,
        )
        self.assertEqual(result["state"], "modified")
        audit = self.s.execute(select(models.Confirmation)).scalar_one()
        self.assertEqual(audit.scan_id, self.scan1.id)
        self.assertEqual(self.rule1.logic, "new")

    def test_modify_rejects_model_and_identity_fields(self):
        for field in ("id", "scan_id", "state", "confidence", "created_at"):
            with self.subTest(field=field), self.assertRaises(HTTPException) as cm:
                confirm_rule(
                    self.p1.id,
                    self.rule1.id,
                    ConfirmIn(action="modify", fields={field: 999}),
                    self.s,
                )
            self.assertEqual(cm.exception.status_code, 400)

        for fields in ({"name": "x" * 501}, {"logic": "x" * 100_001},
                       {"domain_id": True}):
            with self.subTest(fields=fields), self.assertRaises(HTTPException) as cm:
                confirm_rule(
                    self.p1.id,
                    self.rule1.id,
                    ConfirmIn(action="modify", fields=fields),
                    self.s,
                )
            self.assertEqual(cm.exception.status_code, 400)

    def test_batch_confirmation_requires_every_id_in_current_project(self):
        with self.assertRaises(HTTPException) as cm:
            confirm_rules_batch(
                self.p1.id,
                BatchConfirmIn(ids=[self.rule1.id, self.rule2.id]),
                self.s,
            )
        self.assertEqual(cm.exception.status_code, 404)
        self.s.refresh(self.rule1)
        self.assertEqual(self.rule1.state, models.RuleState.AI_LABELED)

    def test_pipeline_confirmation_count_is_not_global(self):
        confirm_rule(
            self.p1.id, self.rule1.id, ConfirmIn(action="confirm"), self.s
        )
        confirm_rule(
            self.p1.id, self.rule1.id, ConfirmIn(action="confirm"), self.s
        )
        confirm_rule(
            self.p2.id, self.rule2.id, ConfirmIn(action="confirm"), self.s
        )

        result = pipeline(self.p1.id, self.s)
        confirm_step = next(step for step in result["steps"] if step["key"] == "confirm")

        self.assertEqual(confirm_step["count"], 1)

    def test_confirmation_does_not_modify_semantic_confidence(self):
        confirm_rule(
            self.p1.id, self.rule1.id, ConfirmIn(action="confirm"), self.s
        )
        confirm_rule(
            self.p1.id, self.rule1.id, ConfirmIn(action="confirm"), self.s
        )

        self.s.refresh(self.rule1)
        self.assertEqual(self.rule1.confidence, 60)

    def test_batch_confirmation_audit_contains_full_before_and_after_state(self):
        confirm_rules_batch(
            self.p1.id,
            BatchConfirmIn(ids=[self.rule1.id], action="confirm"),
            self.s,
        )

        audit = self.s.execute(select(models.Confirmation)).scalar_one()
        self.assertEqual(audit.before["logic"], "old")
        self.assertEqual(audit.before["confidence"], 60)
        self.assertEqual(audit.after["confidence"], 60)


class RuntimeEvidenceTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.p1, self.scan1 = self.project_with_scan("runtime-one")
        self.p2, self.scan2 = self.project_with_scan("runtime-two")
        self.rule = models.BusinessRule(scan_id=self.scan1.id, name="rule")
        self.other_rule = models.BusinessRule(scan_id=self.scan2.id, name="other")
        self.flow = models.BusinessFlow(scan_id=self.scan1.id, name="flow")
        self.chain = models.BusinessChain(scan_id=self.scan1.id, mainline=[])
        self.s.add_all([self.rule, self.other_rule, self.flow, self.chain])
        self.s.commit()

    def create(self, target_type, target_id, level, environment):
        return runtime_evidence_create(
            self.p1.id,
            RuntimeEvidenceIn(
                target_type=target_type,
                target_id=target_id,
                level=level,
                evidence_type="test" if level == "R1" else "trace",
                environment=environment,
                summary=f"{level} evidence",
                reference="test_runtime_evidence",
            ),
            self.s,
        )

    def test_runtime_level_is_derived_independently_for_each_target(self):
        self.assertEqual(
            runtime_evidence_list(
                self.p1.id, "rule", self.rule.id, 500, self.s
            )["runtime_level"],
            "R0",
        )
        self.assertEqual(
            self.create("rule", self.rule.id, "R1", "test")["runtime_level"],
            "R1",
        )
        self.assertEqual(
            self.create("rule", self.rule.id, "R2", "staging")["runtime_level"],
            "R2",
        )
        self.assertEqual(
            self.create("rule", self.rule.id, "R3", "production")["runtime_level"],
            "R3",
        )
        flow_result = self.create("flow", self.flow.id, "R1", "test")
        chain_result = self.create("chain", self.chain.id, "R2", "controlled")
        self.assertEqual(flow_result["runtime_level"], "R1")
        self.assertEqual(chain_result["runtime_level"], "R2")

    def test_runtime_target_and_environment_are_validated(self):
        with self.assertRaises(HTTPException) as cm:
            self.create("rule", self.other_rule.id, "R1", "test")
        self.assertEqual(cm.exception.status_code, 404)

        with self.assertRaises(HTTPException) as cm:
            self.create("rule", self.rule.id, "R3", "staging")
        self.assertEqual(cm.exception.status_code, 400)

    def test_runtime_evidence_can_be_corrected_without_changing_rule_state(self):
        result = self.create("rule", self.rule.id, "R1", "test")
        evidence_id = result["item"]["id"]
        deleted = runtime_evidence_delete(self.p1.id, evidence_id, self.s)

        self.s.refresh(self.rule)
        self.assertEqual(deleted["runtime_level"], "R0")
        self.assertEqual(self.rule.state, models.RuleState.AI_LABELED)
        self.assertEqual(self.rule.confidence, 0)


class ProjectDeletionTests(DatabaseTestCase):
    def test_delete_project_rejects_a_running_background_job(self):
        project, _scan = self.project_with_scan("busy")
        self.s.add(models.LabelJob(project_id=project.id, status="running"))
        self.s.commit()

        with self.assertRaises(HTTPException) as cm:
            delete_project(project.id, delete_files=False, s=self.s)

        self.assertEqual(cm.exception.status_code, 409)
        self.assertIsNotNone(self.s.get(models.Project, project.id))

    def test_delete_project_never_removes_an_external_repository_path(self):
        external = Path(self.tmp.name) / "external-source"
        external.mkdir()
        marker = external / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        project = models.Project(name="external", repo_path=str(external))
        self.s.add(project)
        self.s.commit()

        result = delete_project(project.id, delete_files=True, s=self.s)

        self.assertTrue(marker.exists())
        self.assertFalse(result["files_deleted"])
        self.assertTrue(result["files_delete_skipped"])

    def test_delete_project_removes_all_project_owned_rows(self):
        project, scan = self.project_with_scan("delete-me")
        module = models.Module(scan_id=scan.id, name="m")
        self.s.add(module)
        self.s.flush()
        rule = models.BusinessRule(scan_id=scan.id, name="r")
        self.s.add(rule)
        self.s.flush()
        self.s.add_all(
            [
                models.Confirmation(
                    scan_id=scan.id,
                    target_type="rule",
                    target_id=rule.id,
                    action="confirm",
                ),
                models.RuntimeEvidence(
                    scan_id=scan.id,
                    target_type="rule",
                    target_id=rule.id,
                    level="R1",
                    evidence_type="test",
                    environment="test",
                    summary="passed",
                ),
                models.ModuleAnalysis(
                    scan_id=scan.id, module_id=module.id, module_name="m"
                ),
                models.QaRecord(project_id=project.id, question="q"),
            ]
        )
        self.s.commit()

        delete_project(project.id, delete_files=False, s=self.s)

        self.assertIsNone(self.s.get(models.Project, project.id))
        self.assertEqual(self.s.query(models.Confirmation).count(), 0)
        self.assertEqual(self.s.query(models.RuntimeEvidence).count(), 0)
        self.assertEqual(self.s.query(models.ModuleAnalysis).count(), 0)
        self.assertEqual(self.s.query(models.QaRecord).count(), 0)


class BackgroundJobExclusionTests(DatabaseTestCase):
    def test_database_allows_only_one_running_job_per_project(self):
        project, _scan = self.project_with_scan("exclusive-job")
        self.s.add(models.LabelJob(project_id=project.id, status="running"))
        self.s.commit()

        with self.Session() as competing:
            competing.add(models.LabelJob(project_id=project.id, status="running"))
            with self.assertRaises(IntegrityError):
                competing.commit()

    def test_atomic_job_claim_returns_conflict_for_existing_job(self):
        project, _scan = self.project_with_scan("exclusive-route")
        self.s.add(models.LabelJob(project_id=project.id, status="running"))
        self.s.commit()

        with self.Session() as competing, self.assertRaises(HTTPException) as cm:
            _create_project_job(competing, project.id, limit=10)

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(
            self.s.query(models.LabelJob)
            .filter(models.LabelJob.project_id == project.id)
            .count(),
            1,
        )

    def test_stale_job_is_released_with_a_retryable_error(self):
        project, _scan = self.project_with_scan("stale-job")
        other_project, _other_scan = self.project_with_scan("fresh-job")
        now = models.utcnow()
        stale = models.LabelJob(
            project_id=project.id,
            status="running",
            started_at=now - timedelta(minutes=10),
            heartbeat_at=now - timedelta(minutes=10),
        )
        fresh = models.LabelJob(
            project_id=other_project.id,
            status="running",
            started_at=now,
            heartbeat_at=now,
        )
        self.s.add_all([stale, fresh])
        self.s.commit()

        self.assertEqual(recover_stale_label_jobs(self.s, lease_seconds=60), 1)
        self.s.refresh(stale)
        self.s.refresh(fresh)
        self.assertEqual(stale.status, "failed")
        self.assertIn("重新运行", stale.error)
        self.assertIsNotNone(stale.finished_at)
        self.assertEqual(fresh.status, "running")

        self.assertEqual(recover_stale_label_jobs(self.s, force=True), 1)
        self.s.refresh(fresh)
        self.assertEqual(fresh.status, "failed")


class ExportSafetyTests(unittest.TestCase):
    def test_excel_text_is_not_interpreted_as_formula(self):
        for value in ("=1+1", "+SUM(A1:A2)", "-2+3", "@cmd"):
            with self.subTest(value=value):
                self.assertTrue(_excel_safe(value).startswith("'"))
        self.assertEqual(_excel_safe("ordinary"), "ordinary")
        self.assertIsNone(_excel_safe(None))


class ModuleBatchOutcomeTests(DatabaseTestCase):
    def test_partial_failures_are_not_reported_as_analyzed_modules(self):
        project, scan = self.project_with_scan("modules")
        cached = models.Module(scan_id=scan.id, name="cached")
        success = models.Module(scan_id=scan.id, name="success")
        failure = models.Module(scan_id=scan.id, name="failure")
        self.s.add_all([cached, success, failure])
        self.s.flush()
        self.s.add(models.ModuleAnalysis(
            scan_id=scan.id,
            module_id=cached.id,
            module_name=cached.name,
            content={},
            markdown="cached",
        ))
        self.s.commit()

        def fake_analyze(session, scan_id, module_id, _client):
            if module_id == failure.id:
                raise RuntimeError("simulated module failure")
            module = session.get(models.Module, module_id)
            session.add(models.ModuleAnalysis(
                scan_id=scan_id,
                module_id=module_id,
                module_name=module.name,
                content={},
                markdown="generated",
            ))
            session.commit()

        with patch("c2b.db.SessionLocal", self.Session), patch(
            "c2b.api.routes.SessionLocal", self.Session
        ), patch(
            "c2b.llm.LLMClient", return_value=object()
        ), patch(
            "c2b.semantics.module_analysis.analyze_module",
            side_effect=fake_analyze,
        ):
            result = analyze_all_modules(project.id)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with self.Session() as check:
                    job = check.get(models.LabelJob, result["job_id"])
                    if job.status != "running":
                        break
                time.sleep(0.01)
            else:
                self.fail("module batch job did not finish")

        with self.Session() as check:
            job = check.get(models.LabelJob, result["job_id"])
            self.assertEqual(job.status, "done")
            self.assertEqual(job.result["total"], 3)
            self.assertEqual(job.result["cached"], 1)
            self.assertEqual(job.result["analyzed"], 2)
            self.assertEqual(job.result["failed"], 1)


if __name__ == "__main__":
    unittest.main()

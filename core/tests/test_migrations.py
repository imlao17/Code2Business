import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.db import Base, _migrate_schema, make_engine
from c2b.semantics.module_analysis import materialize_analysis


class AdditiveMigrationTests(unittest.TestCase):
    def test_confirmation_scan_owner_is_added_and_backfilled(self):
        with tempfile.TemporaryDirectory() as td:
            engine = make_engine(f"sqlite:///{Path(td) / 'legacy.db'}")
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE business_rules (id INTEGER PRIMARY KEY, scan_id INTEGER)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE confirmations ("
                    "id INTEGER PRIMARY KEY, target_type VARCHAR(30), target_id INTEGER)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO business_rules (id, scan_id) VALUES (7, 42)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO confirmations (id, target_type, target_id) "
                    "VALUES (1, 'rule', 7)"
                )

            _migrate_schema(engine)

            columns = {column["name"] for column in inspect(engine).get_columns(
                "confirmations"
            )}
            self.assertIn("scan_id", columns)
            with engine.connect() as connection:
                scan_id = connection.exec_driver_sql(
                    "SELECT scan_id FROM confirmations WHERE id = 1"
                ).scalar_one()
            self.assertEqual(scan_id, 42)
            engine.dispose()

    def test_module_analysis_name_is_repaired_from_canonical_module(self):
        with tempfile.TemporaryDirectory() as td:
            engine = make_engine(f"sqlite:///{Path(td) / 'module.db'}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine, expire_on_commit=False)
            with Session() as session:
                project = models.Project(name="p", repo_path=td)
                session.add(project)
                session.flush()
                scan = models.Scan(project_id=project.id)
                session.add(scan)
                session.flush()
                module = models.Module(scan_id=scan.id, name="canonical/module")
                session.add(module)
                session.flush()
                analysis = models.ModuleAnalysis(
                    scan_id=scan.id,
                    module_id=module.id,
                    module_name="hallucinated label",
                    content={"module_name": "hallucinated label", "purpose": "p"},
                )
                session.add(analysis)
                session.commit()
                analysis_id = analysis.id

            _migrate_schema(engine)

            with Session() as session:
                repaired = session.get(models.ModuleAnalysis, analysis_id)
                self.assertEqual(repaired.module_name, "canonical/module")
                content, markdown = materialize_analysis(
                    repaired, repaired.module_name
                )
                self.assertEqual(content["module_name"], "canonical/module")
                self.assertIn("# 模块需求规格：canonical/module", markdown)
            engine.dispose()

    def test_running_job_migration_recovers_duplicates_and_enforces_one_per_project(self):
        with tempfile.TemporaryDirectory() as td:
            engine = make_engine(f"sqlite:///{Path(td) / 'jobs.db'}")
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE label_jobs ("
                    "id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, "
                    "status VARCHAR(20) NOT NULL, error TEXT, finished_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO label_jobs (id, project_id, status) VALUES "
                    "(1, 7, 'running'), (2, 7, 'running')"
                )

            _migrate_schema(engine)

            indexes = {index["name"] for index in inspect(engine).get_indexes(
                "label_jobs"
            )}
            self.assertIn("uq_label_jobs_project_running", indexes)
            with engine.connect() as connection:
                running = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM label_jobs "
                    "WHERE project_id = 7 AND status = 'running'"
                ).scalar_one()
            self.assertLessEqual(running, 1)

            with self.assertRaises(IntegrityError), engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO label_jobs (project_id, status) "
                    "VALUES (7, 'running')"
                )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

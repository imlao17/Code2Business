from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.db import Base
from c2b.scanner.graph import Scanner
from c2b.scanner.service import scan_project


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def close_session(session):
    engine = session.get_bind()
    session.close()
    engine.dispose()


class ScannerGraphTests(unittest.TestCase):
    def test_method_identity_and_call_resolution_are_stable(self) -> None:
        source = """\
package demo;

class First {
    void shared() {}

    void caller() {
        shared();
    }
}

class Second {
    void shared() {}

    void shared(String value) {}

    static void sharedStatic() {}

    void invoke() {
        Second.sharedStatic();
        this.shared();
        shared("x");
    }
}
"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Demo.java").write_text(source, encoding="utf-8")

            session = make_session()
            try:
                scanner = Scanner(session, root, "demo")
                scanner.create()
                scanner.run_parse(report={})

                methods = session.execute(
                    select(models.CodeObject)
                    .where(models.CodeObject.scan_id == scanner.scan.id)
                    .where(models.CodeObject.obj_type == models.ObjType.METHOD)
                    .order_by(models.CodeObject.start_line)
                ).scalars().all()
                fq_names = [method.fq_name for method in methods]
                self.assertIn("demo.First.shared()", fq_names)
                self.assertIn("demo.Second.shared()", fq_names)
                self.assertIn("demo.Second.shared(String value)", fq_names)
                self.assertEqual(len(fq_names), len(set(fq_names)))

                by_line = {
                    edge.line: edge
                    for edge in session.execute(
                        select(models.CallEdge)
                        .where(models.CallEdge.scan_id == scanner.scan.id)
                        .order_by(models.CallEdge.line)
                    ).scalars()
                }
                self.assertEqual(by_line[7].resolve_type, models.ResolveType.UNKNOWN)
                self.assertIsNone(by_line[7].to_obj_id)
                self.assertEqual(by_line[19].resolve_type, models.ResolveType.STATIC)
                self.assertIsNotNone(by_line[19].to_obj_id)
                self.assertEqual(by_line[20].resolve_type, models.ResolveType.UNKNOWN)
                self.assertIsNone(by_line[20].to_obj_id)
                self.assertEqual(by_line[21].resolve_type, models.ResolveType.UNKNOWN)
                self.assertIsNone(by_line[21].to_obj_id)
            finally:
                close_session(session)

    def test_sharded_parse_filters_non_java_and_marks_task_done(self) -> None:
        java_source = """\
package demo;

class Demo {
    void run() {}
}
"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = root / "module"
            mod.mkdir()
            (mod / "Demo.java").write_text(java_source, encoding="utf-8")
            (mod / "mapper.xml").write_text("<mapper />\n", encoding="utf-8")

            session = make_session()
            try:
                scanner = Scanner(session, root, "demo")
                scanner.create()
                scanner.run_parse(
                    report={"total_files": 2},
                    shards=[{"name": "shard-1", "files": ["module/Demo.java", "module/mapper.xml"]}],
                )

                task = session.execute(select(models.ScanTask)).scalar_one()
                self.assertEqual(task.status, models.TaskStatus.DONE)
                self.assertEqual(scanner.scan.meta["parsed_files"], 1)
                self.assertEqual(scanner.scan.stats["classes"], 1)
                self.assertEqual(scanner.scan.stats["methods"], 1)
            finally:
                close_session(session)

    def test_sharded_parse_marks_failed_task_on_shard_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Demo.java").write_text("class Demo {}", encoding="utf-8")

            session = make_session()
            try:
                scanner = Scanner(session, root, "demo")
                scanner.create()
                created_paths: list[Path] = []

                def tracked_temp_file(*args, **kwargs):
                    kwargs["dir"] = root
                    temp_file = NamedTemporaryFile(*args, **kwargs)
                    created_paths.append(Path(temp_file.name))
                    return temp_file

                with patch(
                    "c2b.scanner.graph.NamedTemporaryFile",
                    side_effect=tracked_temp_file,
                ), patch.object(
                    scanner,
                    "_parse_shard_files",
                    side_effect=RuntimeError("boom"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "boom"):
                        scanner.run_parse(
                            report={"total_files": 1},
                            shards=[{"name": "shard-1", "files": ["Demo.java"]}],
                        )

                task = session.execute(select(models.ScanTask)).scalar_one()
                self.assertEqual(task.status, models.TaskStatus.FAILED)
                self.assertEqual(task.error, "boom")
                self.assertEqual(len(created_paths), 1)
                self.assertFalse(created_paths[0].exists())
            finally:
                close_session(session)

    def test_sharded_parse_marks_unrun_tasks_when_max_files_stops_early(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "First.java").write_text("class First {}\n", encoding="utf-8")
            (root / "Second.java").write_text("class Second {}\n", encoding="utf-8")

            session = make_session()
            try:
                scanner = Scanner(session, root, "demo", max_files=1)
                scanner.create()
                scanner.run_parse(
                    report={"total_files": 2},
                    shards=[
                        {"name": "shard-1", "files": ["First.java"]},
                        {"name": "shard-2", "files": ["Second.java"]},
                    ],
                )

                tasks = session.execute(
                    select(models.ScanTask).order_by(models.ScanTask.id)
                ).scalars().all()
                self.assertEqual(tasks[0].status, models.TaskStatus.DONE)
                self.assertEqual(tasks[1].status, models.TaskStatus.FAILED)
                self.assertIn("max_files", tasks[1].error)
                self.assertNotIn(
                    models.TaskStatus.PENDING, {task.status for task in tasks}
                )
            finally:
                close_session(session)

    def test_single_file_parse_failure_is_visible_on_task_and_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Broken.java").write_text("class Broken {}\n", encoding="utf-8")

            session = make_session()
            try:
                scanner = Scanner(session, root, "demo")
                scanner.create()
                with patch(
                    "c2b.scanner.graph.parse_mod.parse_file",
                    side_effect=RuntimeError("parser exploded"),
                ):
                    scanner.run_parse(
                        report={"total_files": 1},
                        shards=[{"name": "shard-1", "files": ["Broken.java"]}],
                    )

                task = session.execute(select(models.ScanTask)).scalar_one()
                self.assertEqual(task.status, models.TaskStatus.FAILED)
                self.assertIn("parser exploded", task.error)
                self.assertEqual(scanner.scan.meta["parse_errors"], 1)
                self.assertEqual(
                    scanner.scan.meta["parse_error_samples"][0]["file"],
                    "Broken.java",
                )
            finally:
                close_session(session)

    def test_scan_project_smoke(self) -> None:
        service = """\
package demo.service;

class Service {
    static void ping() {}
}
"""
        controller = """\
package demo.web;

class Controller {
    void entry() {
        demo.service.Service.ping();
    }
}
"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service").mkdir()
            (root / "web").mkdir()
            (root / "service" / "Service.java").write_text(service, encoding="utf-8")
            (root / "web" / "Controller.java").write_text(controller, encoding="utf-8")
            (root / "web" / "view.jsp").write_text("<%-- ignored by Java parser --%>\n", encoding="utf-8")

            session = make_session()
            try:
                progress_events = []
                result = scan_project(
                    session,
                    root,
                    "demo",
                    progress=lambda stage, done, total: progress_events.append((stage, done, total)),
                )

                scan = session.get(models.Scan, result["scan_id"])
                self.assertEqual(scan.status, models.ScanStatus.DONE)
                self.assertEqual(result["stats"]["parsed_files"], 2)
                self.assertEqual(result["stats"]["call_edges"], 1)
                self.assertEqual(result["tier"], "S")
                self.assertEqual(progress_events[0], ("probe", 0, 0))
                self.assertEqual(progress_events[1], ("parse", 0, 2))
                self.assertEqual(progress_events[-1], ("parse", 2, 2))
            finally:
                close_session(session)

    def test_scan_project_without_progress_callback_handles_hundred_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(100):
                (root / f"Demo{index}.java").write_text(
                    f"class Demo{index} {{}}\n", encoding="utf-8"
                )

            session = make_session()
            try:
                result = scan_project(session, root, "demo")

                scan = session.get(models.Scan, result["scan_id"])
                self.assertEqual(scan.status, models.ScanStatus.DONE)
                self.assertEqual(result["stats"]["parsed_files"], 100)
            finally:
                close_session(session)


if __name__ == "__main__":
    unittest.main()

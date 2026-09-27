"""v0.6.1 文件残留治理的回归测试。

覆盖三件事：
1. 旧版“当前目录默认库”在首次使用统一库时被自动收编（含 WAL 数据）；
2. `c2b clean` 能识别并删除指定目录的孤儿库与中间产物残留；
3. Agent Native 中间产物默认落在 ~/.c2b/tmp，导入成功后自动删除。
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from c2b import models
from c2b.cli import cli
from c2b.config import _default_db_url
from c2b.db import Base


def _seed_project_db(path: Path) -> None:
    """建一个带数据的孤儿库（模拟旧版 cwd 默认库）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name VARCHAR(200))")
        conn.execute("INSERT INTO projects (name) VALUES ('legacy-project')")
        conn.commit()
    finally:
        conn.close()


class OrphanAdoptionTests(unittest.TestCase):
    def test_orphan_cwd_db_is_adopted_when_home_db_missing(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            cwd = Path(td) / "cwd"
            cwd.mkdir()
            _seed_project_db(cwd / "c2b.db")

            with patch("c2b.config.CONFIG_DIR", home), \
                    patch("c2b.config.Path.cwd", return_value=cwd):
                url = _default_db_url()

            self.assertEqual(url, f"sqlite:///{home / 'c2b.db'}")
            conn = sqlite3.connect(home / "c2b.db")
            try:
                rows = conn.execute("SELECT name FROM projects").fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [("legacy-project",)])

    def test_existing_home_db_wins_over_orphan(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            _seed_project_db(home / "c2b.db")
            cwd = Path(td) / "cwd"
            cwd.mkdir()
            _seed_project_db(cwd / "c2b.db")

            with patch("c2b.config.CONFIG_DIR", home), \
                    patch("c2b.config.Path.cwd", return_value=cwd):
                url = _default_db_url()

            self.assertEqual(url, f"sqlite:///{home / 'c2b.db'}")
            # 孤儿库原样保留，不覆盖统一库
            self.assertTrue((cwd / "c2b.db").exists())


class CleanCommandTests(unittest.TestCase):
    def test_clean_removes_orphan_db_and_intermediate_files(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("c2b.db", "c2b.db-wal", "c2b.db-shm",
                         "c2b-evidence.json", "c2b-semantic-result.json"):
                (Path(td) / name).write_text("x", encoding="utf-8")

            result = CliRunner().invoke(
                cli, ["clean", "--target", td, "--yes"])

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("已删除 5 个文件", result.output)
            for name in ("c2b.db", "c2b.db-wal", "c2b.db-shm",
                         "c2b-evidence.json", "c2b-semantic-result.json"):
                self.assertFalse((Path(td) / name).exists())

    def test_clean_refuses_to_delete_active_unified_db(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            for name in ("c2b.db", "c2b.db-wal", "c2b.db-shm",
                         "c2b-evidence.json"):
                (home / name).write_text("x", encoding="utf-8")

            with patch("c2b.cli.DB_URL", f"sqlite:///{home / 'c2b.db'}"):
                result = CliRunner().invoke(
                    cli, ["clean", "--target", str(home), "--yes"])

            self.assertEqual(result.exit_code, 0, result.output)
            # 数据库三件套受保护，不会被 --yes 删掉
            for name in ("c2b.db", "c2b.db-wal", "c2b.db-shm"):
                self.assertTrue((home / name).exists(), name)
            # 非数据库残留照常清理
            self.assertFalse((home / "c2b-evidence.json").exists())

    def test_clean_reports_nothing_when_directory_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            result = CliRunner().invoke(cli, ["clean", "--target", td, "--yes"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("没有 c2b 残留", result.output)


class TmpArtifactTests(unittest.TestCase):
    def _project(self, engine) -> int:
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as s:
            p = models.Project(name="tmp-demo", repo_path="/tmp")
            s.add(p)
            s.commit()
            scan = models.Scan(
                project_id=p.id, scan_mode="full",
                status=models.ScanStatus.DONE, tier="S",
            )
            s.add(scan)
            s.commit()
            return p.id

    def test_import_deletes_tmp_input_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td) / "tmp"
            tmp_dir.mkdir()
            evidence = tmp_dir / "c2b-semantic-result.json"

            engine = create_engine(f"sqlite:///{Path(td) / 't.db'}")
            Base.metadata.create_all(engine)
            project_id = self._project(engine)
            # 一份合法但最小的语义载荷：必填字段齐全，除域以外全部为空
            evidence.write_text(json.dumps({
                "contract_version": "1.0",
                "domains": [{
                    "name": "演示域", "description": "演示",
                    "modules": [], "confidence": 60,
                }],
                "objects": [], "rules": [], "flows": [],
                "chain": None, "questions": [],
            }, ensure_ascii=False), encoding="utf-8")

            Session = sessionmaker(bind=engine, expire_on_commit=False)
            with patch("c2b.cli.init_db"), \
                    patch("c2b.cli.SessionLocal", Session), \
                    patch("c2b.cli.TMP_DIR", tmp_dir):
                result = CliRunner().invoke(
                    cli, ["import-semantic", str(project_id),
                          "--input", str(evidence)])

            self.assertEqual(result.exit_code, 0, result.output)
            # 导入成功后临时文件被自动清理
            self.assertFalse(evidence.exists())
            engine.dispose()

    def test_import_reads_default_tmp_input_without_option(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td) / "tmp"
            evidence = tmp_dir / "c2b-semantic-result.json"

            engine = create_engine(f"sqlite:///{Path(td) / 't.db'}")
            Base.metadata.create_all(engine)
            project_id = self._project(engine)
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(json.dumps({
                "contract_version": "1.0",
                "domains": [{
                    "name": "演示域", "description": "演示",
                    "modules": [], "confidence": 60,
                }],
                "objects": [], "rules": [], "flows": [],
                "chain": None, "questions": [],
            }, ensure_ascii=False), encoding="utf-8")

            Session = sessionmaker(bind=engine, expire_on_commit=False)
            with patch("c2b.cli.init_db"), \
                    patch("c2b.cli.SessionLocal", Session), \
                    patch("c2b.cli.TMP_DIR", tmp_dir):
                # 不传 --input，应回落到默认 tmp 路径并自动清理
                result = CliRunner().invoke(
                    cli, ["import-semantic", str(project_id)])

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse(evidence.exists())
            engine.dispose()

    def test_import_missing_default_input_gives_actionable_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td) / "tmp"
            tmp_dir.mkdir()
            engine = create_engine(f"sqlite:///{Path(td) / 't.db'}")
            Base.metadata.create_all(engine)
            project_id = self._project(engine)

            Session = sessionmaker(bind=engine, expire_on_commit=False)
            with patch("c2b.cli.init_db"), \
                    patch("c2b.cli.SessionLocal", Session), \
                    patch("c2b.cli.TMP_DIR", tmp_dir):
                result = CliRunner().invoke(
                    cli, ["import-semantic", str(project_id)])

            self.assertEqual(result.exit_code, 1)
            self.assertIn("agent-schema", result.output)
            engine.dispose()

    def test_import_keeps_user_input_outside_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td) / "tmp"
            tmp_dir.mkdir()
            user_file = Path(td) / "my-semantic.json"

            engine = create_engine(f"sqlite:///{Path(td) / 't.db'}")
            Base.metadata.create_all(engine)
            project_id = self._project(engine)
            user_file.write_text(json.dumps({
                "contract_version": "1.0",
                "domains": [{
                    "name": "演示域", "description": "演示",
                    "modules": [], "confidence": 60,
                }],
                "objects": [], "rules": [], "flows": [],
                "chain": None, "questions": [],
            }, ensure_ascii=False), encoding="utf-8")

            Session = sessionmaker(bind=engine, expire_on_commit=False)
            with patch("c2b.cli.init_db"), \
                    patch("c2b.cli.SessionLocal", Session), \
                    patch("c2b.cli.TMP_DIR", tmp_dir):
                result = CliRunner().invoke(
                    cli, ["import-semantic", str(project_id),
                          "--input", str(user_file)])

            self.assertEqual(result.exit_code, 0, result.output)
            # 用户自指定路径的输入文件保持原样
            self.assertTrue(user_file.exists())
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

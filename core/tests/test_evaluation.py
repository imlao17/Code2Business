import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from c2b.db import Base
from c2b.evaluation import evaluate_scan
from c2b.scanner.graph import Scanner


class EvaluationTests(unittest.TestCase):
    def test_evaluates_known_api_table_and_sql_facts(self):
        java = """\
@RestController
@RequestMapping("/users")
class UserController {
    @GetMapping("/list")
    void list() {}
}
"""
        mapper = """\
<mapper><select id="list">SELECT * FROM sys_user</select></mapper>
"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "UserController.java").write_text(java, encoding="utf-8")
            (root / "UserMapper.xml").write_text(mapper, encoding="utf-8")
            engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine, expire_on_commit=False)
            with Session() as session:
                scanner = Scanner(session, root, "demo")
                scanner.create()
                scanner.run_parse(report={})
                scanner.finish()
                result = evaluate_scan(session, scanner.project.id, {
                    "schema_version": 1,
                    "sample": {"name": "demo"},
                    "assertions": [
                        {"id": "api", "kind": "api", "method": "GET", "path": "/users/list"},
                        {"id": "table", "kind": "table", "name": "sys_user"},
                        {"id": "sql", "kind": "sql", "table": "sys_user", "op": "select"},
                        {"id": "missing", "kind": "table", "name": "sys_role"},
                    ],
                })
            engine.dispose()

        self.assertEqual(result["summary"]["passed"], 3)
        self.assertEqual(result["summary"]["total"], 4)
        self.assertEqual(result["missing"], ["missing"])


if __name__ == "__main__":
    unittest.main()

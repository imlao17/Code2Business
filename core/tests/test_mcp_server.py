import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from c2b.db import Base
from c2b.mcp_server import (
    build_server, tool_document, tool_export_evidence, tool_import_semantic,
    tool_list_projects, tool_overview, tool_rules, tool_scan, tool_status,
)


class McpToolTests(unittest.TestCase):
    """直接调用工具实现函数（MCP 只是它们上面的薄传输层）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "OrderService.java").write_text(
            "package com.shop;\n"
            "public class OrderService {\n"
            "    /** 创建订单 */\n"
            "    public void createOrder(int amount) {\n"
            "        if (amount > 10000) {\n"
            "            throw new IllegalArgumentException(\"超限\");\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'mcp.db'}")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        self._stack = ExitStack()
        self._stack.enter_context(patch("c2b.db.SessionLocal", self.Session))
        self._stack.enter_context(patch("c2b.db.init_db", lambda: None))

    def tearDown(self):
        self._stack.close()
        self.Session.kw["bind"].dispose()
        self.tmp.cleanup()

    def _payload(self, text: str) -> dict:
        return json.loads(text)

    def test_scan_creates_graph_and_tools_read_it_back(self):
        scan = self._payload(tool_scan(str(self.repo), name="订单系统示例"))
        self.assertEqual(scan["status"], "done")
        pid = scan["project_id"]
        self.assertGreater(scan["stats"]["methods"], 0)

        projects = self._payload(tool_list_projects())
        self.assertIn(pid, [p["id"] for p in projects])

        status = self._payload(tool_status(pid))
        self.assertEqual(status["project_id"], pid)
        self.assertEqual(status["next"]["key"], "label")

        overview = self._payload(tool_overview(pid))
        self.assertEqual(overview["scale"]["total_files"], 1)

        document = self._payload(tool_document(pid))
        self.assertEqual(document["doc_type"], "combined")
        self.assertIn("客户交付总报告", document["markdown"])
        self.assertIn("执行摘要", document["markdown"])

        formal = self._payload(tool_document(pid, doc_type="combined"))
        self.assertFalse(formal["draft"])

    def test_rules_tool_returns_labeled_rules_after_import(self):
        scan = self._payload(tool_scan(str(self.repo)))
        pid = scan["project_id"]
        evidence = json.loads(tool_export_evidence(pid))
        self.assertTrue(evidence["methods"])

        # 用导出证据里的真实方法构造一条语义规则并导入
        method = evidence["methods"][0]
        payload = {
            "contract_version": evidence.get("contract_version"),
            "domains": [{"name": "订单域", "description": "订单处理",
                         "modules": [method["module"]]}],
            "objects": [],
            "rules": [{
                "name": "金额超限需审批",
                "domain": "订单域",
                "rule_type": "validation",
                "trigger": "方法调用",
                "pre_condition": "amount > 10000",
                "logic": "创建订单金额超过 10000 时需要审批",
                "status_change": "",
                "data_change": "",
                "follow_up": "",
                "exception": "超限抛出异常",
                "impact": "订单创建",
                "confidence": 90,
                "source_method": method["fq_name"],
                "evidence": {
                    "file": method["file"],
                    "lines": method["lines"],
                    "snippet": "if (amount > 10000) { throw ... }",
                },
            }],
            "flows": [],
            "chain": None,
            "questions": [],
        }
        imported = self._payload(tool_import_semantic(pid, json.dumps(payload)))
        self.assertGreaterEqual(imported.get("rules", 0), 1)

        rules = self._payload(tool_rules(pid, min_confidence=80))
        names = [r["name"] for r in rules["rules"]]
        self.assertIn("金额超限需审批", names)

        with self.assertRaises(ValueError) as bad:
            tool_rules(pid, rule_type="不存在的类型")
        self.assertIn("rule_type 无效", str(bad.exception))

    def test_errors_are_actionable_not_tracebacks(self):
        with self.assertRaises(ValueError):
            tool_scan(str(self.tmp.name) + "/nope")
        with self.assertRaises(ValueError):
            tool_status(99999)
        with self.assertRaises(ValueError):
            tool_document(99999)
        with self.assertRaises(ValueError):
            tool_document(99999, doc_type="不存在")
        with self.assertRaises(ValueError):
            tool_import_semantic(99999, "{not json")

    def test_build_server_registers_all_tools(self):
        async def collect():
            server = build_server()
            return [t.name for t in await server.list_tools()]

        names = __import__("asyncio").run(collect())
        self.assertEqual(names, [
            "list_projects", "scan", "status", "overview", "rules",
            "document", "export_evidence", "import_semantic",
        ])


if __name__ == "__main__":
    unittest.main()

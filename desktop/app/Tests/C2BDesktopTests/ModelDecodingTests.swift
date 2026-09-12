import XCTest
@testable import C2BDesktop

/// 用真实 API 响应形状的 fixture 验证解码契约（防止后端字段漂移导致原生端静默失败）。
final class ModelDecodingTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: json.data(using: .utf8)!)
    }

    func testPipelineDecoding() throws {
        let json = """
        {"project_id":1,"steps":[
          {"key":"import","label":"导入代码","page":"/overview","status":"done"},
          {"key":"scan","label":"扫描分析","page":"/overview","status":"running",
           "stage":"scan:parse","progress":100,"total":500},
          {"key":"export","label":"导出文档","page":"/documents","status":"ready"}],
         "next":{"key":"scan","label":"扫描分析","page":"/overview","status":"running",
                 "stage":"scan:parse","progress":100,"total":500}}
        """
        let pipeline = try decode(Pipeline.self, json)
        XCTAssertEqual(pipeline.projectId, 1)
        XCTAssertEqual(pipeline.steps.count, 3)
        XCTAssertEqual(pipeline.steps[1].stage, "scan:parse")
        XCTAssertEqual(pipeline.next?.key, "scan")
    }

    func testOverviewDecoding() throws {
        let json = """
        {"tier":"XL","status":"done",
         "scale":{"total_lines":100,"total_files":10,"excluded_files":2,
                  "version_files":0,"version_lines":0,
                  "encoding_samples":{"utf-8":10},"lines_by_ext":{"java":100}},
         "stats":{"modules":3,"classes":30,"methods":300,"call_edges":900,
                  "data_tables":5,"sql_operations":50,"rules":12,"questions":2,
                  "domains":3,"objects":8,"flows":4,"chain":1,"confirmed_rules":3},
         "detail":{"top_modules":[{"name":"orders","lines":50,"file_count":4}],
                   "entry_dist":{"batch":6},"rule_types":{"validation":7},
                   "confidence_buckets":{"high(>=75)":4,"mid(50-74)":5,"low(<50)":3},
                   "scan_started":"2026-09-11T01:00:00","scan_finished":"2026-09-11T01:02:00"},
         "scan_id":9}
        """
        let overview = try decode(Overview.self, json)
        XCTAssertEqual(overview.scale.totalLines, 100)
        XCTAssertEqual(overview.stats.confirmedRules, 3)
        XCTAssertEqual(overview.detail.topModules?.first?.name, "orders")
    }

    func testRuleDecoding() throws {
        let json = """
        {"id":7,"name":"金额超限审批","rule_type":"validation","module":"orders",
         "domain_id":2,"trigger":"方法调用","pre_condition":"amount > 10000",
         "logic":"创建订单金额超过 10000 时需要审批","status_change":null,"data_change":null,
         "follow_up":null,"exception":"超限抛异常","impact":"订单创建",
         "evidence_file":"orders/OrderService.java","evidence_lines":[42,60],
         "confidence":90,"state":"ai_labeled","runtime_level":"R0","model":"agent-native",
         "source_method_id":11}
        """
        let rule = try decode(Rule.self, json)
        XCTAssertEqual(rule.displayName, "金额超限审批")
        XCTAssertEqual(rule.evidenceLines, [42, 60])
        XCTAssertEqual(rule.state, "ai_labeled")

        let detailJSON = json.replacingOccurrences(of: "\"model\":\"agent-native\"",
                                                    with: "\"model\":\"agent-native\",\"evidence_snippet\":\"if (amount > 10000)\"")
        let detail = try decode(RuleDetail.self, detailJSON)
        XCTAssertEqual(detail.evidenceSnippet, "if (amount > 10000)")
        XCTAssertEqual(detail.displayName, "金额超限审批")
    }

    func testChainAndQuestionDecoding() throws {
        let chainJSON = """
        {"has_chain":true,"id":1,"mainline":["订单","审批","通知"],
         "relationships":[{"from":"订单","to":"审批","relation":"驱动","verification":"V2","evidence":"orders.java:42"}],
         "design_narrative":"先订单后审批","assumptions":["假设A"],"confidence":60,
         "runtime_level":"R0","runtime_evidence":[],"created_at":"2026-09-11"}
        """
        let chain = try decode(Chain.self, chainJSON)
        XCTAssertEqual(chain.mainline?.count, 3)
        XCTAssertEqual(chain.relationships?.first?.verification, "V2")

        let chainEmpty = try decode(Chain.self, #"{"has_chain":false,"message":"暂无业务推演"}"#)
        XCTAssertFalse(chainEmpty.hasChain)

        let question = try decode(Question.self,
            #"{"id":3,"qtype":"agent_native","desc":"调度归属不明","suggestion":"确认","status":"open","rule_id":null}"#)
        XCTAssertEqual(question.id, 3)
    }

    func testLabelJobDecoding() throws {
        let json = """
        {"job_id":5,"project_id":1,"status":"done","stage":"auto:modules","progress":3,"total":3,
         "result":{"scanned":true,"ai_ran":true,
                   "notes":["note1"],
                   "label":{"rules":10,"objects":4,"flows":2},
                   "modules":{"total":3,"analyzed":3,"failed":0}},
         "error":null}
        """
        let job = try decode(LabelJob.self, json)
        XCTAssertEqual(job.status, "done")
        XCTAssertEqual(job.result?.label?.rules, 10)
        XCTAssertEqual(job.result?.modules?.analyzed, 3)
    }

    func testFriendlyErrorMapping() {
        XCTAssertTrue(APIClient.friendly("缺少 API Key。配置方式").contains("设置"))
        XCTAssertTrue(APIClient.friendly("database is locked").contains("正忙"))
        XCTAssertTrue(APIClient.friendly("项目已有运行中的任务").contains("等它完成"))
        XCTAssertEqual(APIClient.friendly("其他未知错误"), "其他未知错误")
    }
}

/// Sidecar 的路径与环境变量组装（不真正启动进程）。
final class SidecarConfigTests: XCTestCase {
    func testDataRootOverrideAndDefaults() {
        let controller = SidecarController()
        // 默认目录：~/Library/Application Support/com.lao17.code2business
        XCTAssertEqual(controller.dataRoot.lastPathComponent, "com.lao17.code2business")
        XCTAssertEqual(controller.sourcesDir.lastPathComponent, "sources")
        XCTAssertEqual(controller.settingsDir.lastPathComponent, "settings")
        XCTAssertTrue(controller.dbURL.absoluteString.hasSuffix("c2b.db"))
    }

    func testBaseURLIsLoopback() {
        XCTAssertEqual(SidecarController.baseURL.absoluteString, "http://127.0.0.1:8123")
    }
}

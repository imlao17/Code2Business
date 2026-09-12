import Foundation

// MARK: - 项目与流程

struct Project: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let techStack: String?
    let createdAt: String?
}

struct ImportResponse: Codable {
    let ok: Bool
    let projectId: Int
    let jobId: Int?
    let importStatus: String
    let message: String?
}

struct PipelineStep: Codable, Identifiable, Hashable {
    var id: String { key }
    let key: String
    let label: String
    let page: String
    let status: String            // done / running / pending / ready / error
    let stage: String?
    let progress: Int?
    let total: Int?
    let count: Int?
    let error: String?
}

struct PipelineNext: Codable, Hashable {
    let key: String
    let label: String
    let page: String
    let status: String
    let stage: String?
    let progress: Int?
    let total: Int?
    let error: String?
}

struct Pipeline: Codable, Hashable {
    let projectId: Int
    let steps: [PipelineStep]
    let next: PipelineNext?
}

struct LabelJob: Codable {
    let jobId: Int
    let status: String            // running / done / failed
    let stage: String?
    let progress: Int?
    let total: Int?
    let result: JobResult?
    let error: String?

    struct JobResult: Codable {
        let scanned: Bool?
        let aiRan: Bool?
        let notes: [String]?
        let label: LabelSummary?
        let modules: ModulesSummary?
    }

    struct LabelSummary: Codable {
        let rules: Int?
        let objects: Int?
        let flows: Int?
    }

    struct ModulesSummary: Codable {
        let total: Int?
        let analyzed: Int?
        let failed: Int?
    }
}

// MARK: - 总览

struct Overview: Codable {
    let tier: String
    let status: String
    let scale: Scale
    let stats: Stats
    let detail: Detail
    let scanId: Int

    struct Scale: Codable {
        let totalLines: Int?
        let totalFiles: Int?
        let excludedFiles: Int?
        let versionFiles: Int?
        let versionLines: Int?
        let encodingSamples: [String: Int]?
        let linesByExt: [String: Int]?
    }

    struct Stats: Codable {
        let modules: Int?
        let classes: Int?
        let methods: Int?
        let callEdges: Int?
        let dataTables: Int?
        let sqlOperations: Int?
        let rules: Int?
        let questions: Int?
        let domains: Int?
        let objects: Int?
        let flows: Int?
        let chain: Int?
        let confirmedRules: Int?
    }

    struct Detail: Codable {
        let topModules: [TopModule]?
        let entryDist: [String: Int]?
        let ruleTypes: [String: Int]?
        let confidenceBuckets: [String: Int]?
        let scanStarted: String?
        let scanFinished: String?
    }

    struct TopModule: Codable, Identifiable {
        var id: String { name }
        let name: String
        let lines: Int
        let fileCount: Int
    }
}

// MARK: - 规则

struct Rule: Codable, Identifiable, Hashable {
    let id: Int
    let name: String?
    let ruleType: String
    let module: String?
    let domainId: Int?
    let trigger: String?
    let preCondition: String?
    let logic: String?
    let statusChange: String?
    let dataChange: String?
    let followUp: String?
    let exception: String?
    let impact: String?
    let evidenceFile: String?
    let evidenceLines: [Int]?
    let confidence: Int
    let state: String
    let runtimeLevel: String?

    var displayName: String { name ?? "(未命名)" }
}

struct RuleDetail: Codable, Identifiable {
    let id: Int
    let name: String?
    let ruleType: String
    let module: String?
    let trigger: String?
    let preCondition: String?
    let logic: String?
    let statusChange: String?
    let dataChange: String?
    let followUp: String?
    let exception: String?
    let impact: String?
    let evidenceFile: String?
    let evidenceLines: [Int]?
    let evidenceSnippet: String?
    let confidence: Int
    let state: String
    let runtimeLevel: String?

    var displayName: String { name ?? "(未命名)" }
}

struct HighConfidencePreview: Codable {
    let ok: Bool
    let dryRun: Bool
    let count: Int
}

// MARK: - 业务知识

struct Domain: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let description: String?
    let modules: [String]?
    let confidence: Int?
    let confirmed: Bool
}

struct BizObject: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let description: String?
    let sourceTables: [String]?
    let sourceClasses: [String]?
    let actions: [String]?
    let confidence: Int?
    let confirmed: Bool
}

struct BizFlow: Codable, Identifiable, Hashable {
    let id: Int
    let name: String?
    let description: String?
    let entryName: String?
    let businessSteps: [String]?
    let techSteps: [String]?
    let confidence: Int?
    let confirmed: Bool
    let runtimeLevel: String?
}

struct Chain: Codable, Hashable {
    let hasChain: Bool
    let message: String?
    let id: Int?
    let mainline: [String]?
    let relationships: [ChainRelationship]?
    let designNarrative: String?
    let assumptions: [String]?
    let confidence: Int?
    let runtimeLevel: String?
}

struct ChainRelationship: Codable, Hashable, Identifiable {
    var id: String { "\(from)-\(to)" }
    let from: String
    let to: String
    let relation: String?
    let verification: String?
    let evidence: String?
}

struct Question: Codable, Identifiable, Hashable {
    let id: Int
    let qtype: String?
    let desc: String?
    let suggestion: String?
    let status: String
    let ruleId: Int?
}

// MARK: - 数据与接口

struct DataTable: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let comment: String?
    let source: String?
}

struct SqlOperation: Codable, Identifiable, Hashable {
    let id: Int
    let table: String?
    let op: String
    let fields: [String]?
    let sql: String?
    let confidence: Int?
    let file: String?
    let line: Int?
}

struct Entry: Codable, Identifiable, Hashable {
    let id: Int
    let type: String
    let name: String
    let file: String?
    let startLine: Int?
    let isEntry: Bool?
    let entryKind: String?
    let comment: String?
}

// MARK: - 文档

struct DocType: Codable, Identifiable, Hashable {
    var id: String { key }
    let key: String
    let title: String
    let desc: String
}

// MARK: - 设置

struct LlmSettings: Codable {
    let provider: String?
    let baseURL: String?
    let model: String?
    let mode: String?
    let hasKey: Bool
    let keyMasked: String?
    let keyStorage: String?
}

struct LlmTestResult: Codable {
    let ok: Bool
    let message: String?
}

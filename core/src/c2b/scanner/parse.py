"""确定性代码分析（阶段 1）：tree-sitter-java 符号提取 + 方法调用 + 入口识别。

纯函数式：parse_file() 返回结构化 dict，由 graph.py 负责落库。
跨文件类型解析（全局符号索引）在 graph 层做，本层只做单文件提取。

对非 UTF-8（Shift-JIS 等）文件：语法树用原始 bytes 解析，字符串/注释解码失败
时以 detect_encoding 结果转码，失败则丢弃该片段（不影响语法分析）。
"""
import re
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_java

from .probe import detect_encoding, is_version_file

_LANG = Language(tree_sitter_java.language())
_PARSER = Parser(_LANG)

# 入口注解 → entry_kind
ENTRY_ANNOTATIONS = {
    "RequestMapping": "api", "GetMapping": "api", "PostMapping": "api",
    "PutMapping": "api", "DeleteMapping": "api", "PatchMapping": "api",
    "Scheduled": "schedule", "Scheduling": "schedule",
    "RabbitListener": "mq", "KafkaListener": "mq",
}
SQL_HINT = re.compile(r"\b(select|insert|update|delete|merge)\b", re.IGNORECASE)
SQL_CLAUSE = re.compile(r"\b(from|into|set|values|join|where)\b", re.IGNORECASE)
# 模板 SQL 特征：占位符/动态片段（注意：Java 字符串拼接的 + 不算模板特征）
SQL_TEMPLATE_HINT = re.compile(r"(\$\{|#\{|:param|\?\s|\\\"|concat\(|\+\s*[\"'])", re.IGNORECASE)
MAPPER_STATEMENT = re.compile(
    r"<(?P<kind>select|insert|update|delete)\b[^>]*>(?P<body>.*?)</(?P=kind)\s*>",
    re.IGNORECASE | re.DOTALL,
)
XML_TAG = re.compile(r"<[^>]+>")

CLASS_TYPES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "annotation_type_declaration": "class",
}


def _text(node) -> str | None:
    try:
        return node.text.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "shift_jis", "gb18030"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", errors="replace")


def _param_signature(params_node) -> str:
    if params_node is None:
        return "()"
    return _decode_bytes(params_node.text).strip() or "()"


def _arg_count(args_node) -> int:
    if args_node is None:
        return 0
    return len(args_node.named_children)


def _annotations(modifiers_node) -> list[dict]:
    """提取修饰符节点下的注解：[{"name","value"}]。value 取注解参数里首个字符串。"""
    out = []
    if modifiers_node is None:
        return out
    for child in modifiers_node.named_children:
        # tree-sitter distinguishes @RestController from @RequestMapping(...)
        # as marker_annotation and annotation.  Both carry routing semantics.
        if child.type not in ("annotation", "marker_annotation"):
            continue
        name_node = child.named_children[0] if child.named_children else None
        if name_node is None:
            continue
        t = _text(name_node) or ""
        name = t.split(".")[-1]
        value = None
        if child.type == "annotation":
            value = _first_annotation_string(child)
        item = {"name": name, "value": value}
        if not any(x["name"] == name for x in out):
            out.append(item)
    return out


def _first_annotation_string(node) -> str | None:
    """Return the first string argument, including ``value = {…}`` forms."""
    stack = list(reversed(node.named_children))
    while stack:
        child = stack.pop()
        if child.type == "string_literal":
            return _decode_bytes(child.text).strip("\"'")
        stack.extend(reversed(child.named_children))
    return None


def _prev_comment(node):
    """取节点的前导注释（最近的 block/line comment sibling）。"""
    prev = node.prev_named_sibling
    while prev is not None:
        if prev.type in ("block_comment", "line_comment"):
            txt = _decode_bytes(prev.text)
            return txt if len(txt) < 2000 else txt[:2000]
        prev = prev.prev_named_sibling
    return None


def parse_file(abs_path: Path, rel_path: str, module_name: str) -> dict:
    """Parse one supported source file (Java, MyBatis XML, or SQL)."""
    if abs_path.suffix.lower() in {".xml", ".sql"}:
        return _parse_sql_resource(abs_path, rel_path, module_name)

    try:
        data = abs_path.read_bytes()
    except OSError as e:
        return {"file": rel_path, "error": str(e)}

    tree = _PARSER.parse(data)
    root = tree.root_node
    package = ""
    for child in root.named_children:
        if child.type == "package_declaration":
            pkg = _text(child)
            if pkg:
                package = pkg.replace("package ", "").replace(";", "").strip()

    classes: list[dict] = []
    methods: list[dict] = []
    fields: list[dict] = []
    calls: list[dict] = []
    strings: list[dict] = []

    stem = Path(rel_path).stem
    version_file = is_version_file(stem)

    def walk(node, cls: dict | None = None, method: dict | None = None):
        # 迭代遍历（显式栈，避免深嵌套 AST 触发 Python 递归上限）
        stack = [(node, cls, method)]
        while stack:
            node, cls, method = stack.pop()
            t = node.type
            if t in CLASS_TYPES:
                cls = {
                    "name": None, "type": CLASS_TYPES[t],
                    "start": node.start_point[0] + 1, "end": node.end_point[0] + 1,
                    "annotations": [], "comment": None,
                }
                for c in node.named_children:
                    if c.type == "modifiers":
                        cls["annotations"] = _annotations(c)
                    elif c.type == "identifier":
                        cls["name"] = _text(c)
                cls["comment"] = _prev_comment(node)
                classes.append(cls)
            elif t in ("method_declaration", "constructor_declaration"):
                is_ctor = t == "constructor_declaration"
                m = {
                    "name": None, "start": node.start_point[0] + 1,
                    "end": node.end_point[0] + 1,
                    "signature": None, "annotations": [], "is_main": False,
                    "is_ctor": is_ctor, "comment": None,
                    "class_name": cls["name"] if cls else None,
                    "class_start": cls["start"] if cls else None,
                    "params": "()",
                    "param_count": 0,
                }
                for c in node.named_children:
                    if c.type == "modifiers":
                        m["annotations"] = _annotations(c)
                    elif c.type == "identifier":
                        m["name"] = _text(c)
                params_node = node.child_by_field_name("parameters")
                m["params"] = _param_signature(params_node)
                m["param_count"] = _arg_count(params_node)
                m["comment"] = _prev_comment(node)
                sig = _text(node) or ""
                m["signature"] = sig.split("{")[0].strip()[:2000]
                if m["name"] == "main":
                    m["is_main"] = bool(re.search(r"static\s+void\s+main", sig))
                methods.append(m)
                # 方法内部子树入栈
                for c in reversed(list(node.named_children)):
                    stack.append((c, cls, m))
                continue
            elif t == "field_declaration":
                for c in node.named_children:
                    if c.type == "variable_declarator":
                        name_node = c.child_by_field_name("name")
                        if name_node is not None:
                            fields.append({
                                "class_name": cls["name"] if cls else None,
                                "name": _text(name_node),
                                "start": c.start_point[0] + 1,
                            })
            elif t == "method_invocation":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    object_node = node.child_by_field_name("object")
                    args_node = node.child_by_field_name("arguments")
                    calls.append({
                        "method": _text(name_node),
                        "line": node.start_point[0] + 1,
                        "ctx": method["name"] if method else None,
                        "ctx_line": method["start"] if method else None,
                        "cls": cls["name"] if cls else None,
                        "cls_start": cls["start"] if cls else None,
                        "receiver": _text(object_node) if object_node is not None else None,
                        "receiver_kind": object_node.type if object_node is not None else None,
                        "arg_count": _arg_count(args_node),
                    })
            elif t == "string_literal":
                txt = _decode_bytes(node.text)
                inner = txt.strip("\"'")
                if len(inner) > 20 and SQL_HINT.search(inner) and SQL_CLAUSE.search(inner):
                    strings.append({
                        "text": inner[:20000], "line": node.start_point[0] + 1,
                        "ctx": method["name"] if method else None,
                        "ctx_line": method["start"] if method else None,
                        "cls": cls["name"] if cls else None,
                        "cls_start": cls["start"] if cls else None,
                        "template": bool(SQL_TEMPLATE_HINT.search(inner)),
                    })

            for c in reversed(list(node.named_children)):
                stack.append((c, cls, method))

    walk(root)

    # 入口标记：方法注解或 main 或类注解
    for m in methods:
        kind = None
        for a in m["annotations"]:
            if a["name"] in ENTRY_ANNOTATIONS:
                kind = ENTRY_ANNOTATIONS[a["name"]]
                break
        if kind is None and m["is_main"]:
            kind = "batch"
        m["is_entry"] = kind is not None
        m["entry_kind"] = kind
    for c in classes:
        kind = None
        for a in c["annotations"]:
            if a["name"] in ("Controller", "RestController"):
                kind = "api"
                break
        if kind is None and any(m["is_main"] for m in methods):
            kind = "batch"
        c["is_entry"] = kind is not None
        c["entry_kind"] = kind

    return {
        "file": rel_path,
        "module": module_name,
        "package": package,
        "lines": len(data.splitlines()),
        "version_file": version_file,
        "classes": classes, "methods": methods, "fields": fields,
        "calls": calls, "strings": strings,
    }


def _parse_sql_resource(abs_path: Path, rel_path: str, module_name: str) -> dict:
    """Extract statement bodies from mapper XML and standalone SQL files.

    Mapper XML intentionally stays at statement/table level: it yields SQL
    evidence but does not pretend to resolve a Mapper method to a Java method.
    That association needs namespace + interface analysis and remains a later
    precision enhancement.
    """
    try:
        text = _decode_bytes(abs_path.read_bytes())
    except OSError as exc:
        return {"file": rel_path, "error": str(exc)}

    strings: list[dict] = []
    suffix = abs_path.suffix.lower()
    if suffix == ".xml":
        for match in MAPPER_STATEMENT.finditer(text):
            body = match.group("body")
            # Keep dynamic MyBatis fragments visible to the SQL fallback while
            # removing XML syntax that would confuse SQLGlot.
            sql_text = XML_TAG.sub(" ", body)
            sql_text = sql_text.replace("<![CDATA[", "").replace("]]>", "")
            sql_text = " ".join(sql_text.split())
            if not sql_text:
                continue
            strings.append({
                "text": sql_text[:20000],
                "line": text.count("\n", 0, match.start()) + 1,
                "ctx": None, "ctx_line": None, "cls": None, "cls_start": None,
                "template": bool(SQL_TEMPLATE_HINT.search(sql_text)),
            })
    elif suffix == ".sql":
        sql_text = text.strip()
        if sql_text:
            strings.append({
                "text": sql_text[:20000], "line": 1,
                "ctx": None, "ctx_line": None, "cls": None, "cls_start": None,
                "template": bool(SQL_TEMPLATE_HINT.search(sql_text)),
            })

    return {
        "file": rel_path, "module": module_name, "package": "",
        "lines": len(text.splitlines()), "version_file": is_version_file(abs_path.stem),
        "classes": [], "methods": [], "fields": [], "calls": [], "strings": strings,
    }

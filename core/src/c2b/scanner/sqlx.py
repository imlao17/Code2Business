"""SQL 提取与解析：字符串 SQL → SQLGlot → 表/字段/读写。"""
import sqlglot
from sqlglot import exp

# MVP 支持方言
DIALECTS = ("mysql", "postgres")


def parse_sql(sql_text: str, dialect: str = "mysql"):
    """解析单条 SQL，返回 {table, op, fields} 列表；失败返回 None。"""
    if dialect not in DIALECTS:
        dialect = "mysql"
    try:
        statements = sqlglot.parse(sql_text, read=dialect, error_level="ignore")
    except Exception:
        return None
    out = []
    for stmt in statements:
        if stmt is None:
            continue
        op = _op_of(stmt)
        for tbl in stmt.find_all(exp.Table):
            out.append({
                "table": tbl.name,
                "op": op,
                "fields": sorted({f.name for f in stmt.find_all(exp.Column)}),
            })
    return out


def _op_of(stmt) -> str:
    t = stmt.__class__.__name__.lower()
    if "insert" in t:
        return "insert"
    if "update" in t:
        return "update"
    if "delete" in t:
        return "delete"
    if "merge" in t:
        return "merge"
    return "select"

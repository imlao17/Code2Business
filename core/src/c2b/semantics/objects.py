"""S2 业务对象识别：数据表 + 实体/Service 类 → 业务对象及动作。"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..llm import LLMClient, _find_first_list

OBJ_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "source_tables": {"type": "array", "items": {"type": "string"}},
                    "source_classes": {"type": "array", "items": {"type": "string"}},
                    "actions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "description", "source_tables",
                             "source_classes", "actions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["objects"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "你是遗留系统业务分析师。根据给定的数据表清单与相关类清单，识别其中的业务对象。"
    "约束：1) 业务对象必须是具体业务实体（如：销售订单、采购订单、生产计划、"
    "应收发票、客户档案），**不是业务域**（如'订单管理'、'采购管理'是域不是对象）；"
    "2) 粒度参考：一张主表 + 其明细/关联子表聚为一个对象；3) 每个表/类只归属一个"
    "业务对象，无法归属的归入'其他'；4) actions 列出该对象上的业务动作（如：创建、"
    "审批、入库、核销），仅依据类名/表名判断；5) 中文命名，对象名必须包含业务实体"
    "关键词（订单/合同/发票/计划/库存/客户/供应商/价格/实绩等）。"
)


def collect_tables(s: Session, scan_id: int) -> list[models.DataTable]:
    return list(s.execute(
        select(models.DataTable).where(models.DataTable.scan_id == scan_id)
        .order_by(models.DataTable.name)).scalars().all())


def collect_classes(s: Session, scan_id: int, limit: int = 300) -> list[models.CodeObject]:
    q = (select(models.CodeObject)
         .where(models.CodeObject.scan_id == scan_id,
                models.CodeObject.obj_type == models.ObjType.CLASS))
    return list(s.execute(q.limit(limit)).scalars().all())


def build_prompt(tables: list[models.DataTable],
                 classes: list[models.CodeObject]) -> str:
    lines = ["## 数据表"]
    for t in tables[:80]:
        lines.append(f"- {t.name}  {t.comment or ''}")
    lines.append("\n## 相关类（名/文件）")
    for c in classes[:150]:
        lines.append(f"- {c.name}  ({c.file_path[:80]})")
    return "\n".join(lines)


def recognize(client: LLMClient, tables: list[models.DataTable],
              classes: list[models.CodeObject]) -> list[dict]:
    """LLM 识别业务对象，返回对象列表 dict。"""
    result = client.chat_json(SYSTEM_PROMPT, build_prompt(tables, classes),
                              schema=OBJ_SCHEMA)
    objs = (result.get("objects") or result.get("business_objects")
            or result.get("object_list") or _find_first_list(result) or [])
    if not isinstance(objs, list) or not objs:
        raise ValueError(f"业务对象输出格式异常: {str(result)[:200]}")
    return objs


def run_objects(s: Session, scan_id: int, client: LLMClient) -> int:
    """执行 S2 并落库，返回对象数。"""
    tables = collect_tables(s, scan_id)
    classes = collect_classes(s, scan_id)
    if not tables and not classes:
        return 0
    objs = recognize(client, tables, classes)
    n = 0
    for o in objs:
        name = (o.get("name") or "").strip()
        if not name or name == "其他":
            continue
        s.add(models.BusinessObject(
            scan_id=scan_id, name=name, description=o.get("description"),
            source_tables=o.get("source_tables", []),
            source_classes=o.get("source_classes", []),
            actions=o.get("actions", []), confidence=65))
        n += 1
    return n

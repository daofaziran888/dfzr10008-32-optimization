"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

跨源数据关联额外维护三类状态：
- 源数据表（orders、stores 等），是增量刷新的输入；
- 关联结果，按「规则编号 + 主键值」唯一存放，重复重算只保留一份；
- 字段匹配快照，按版本追加，旧版本保留以便追溯，下游只读最新完整版本。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS, SOURCE_TABLES


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._sources: dict[str, dict[str, Any]] = {
            name: {
                "pk": meta["pk"],
                "fields": list(meta["fields"]),
                "rows": [dict(row) for row in meta["rows"]],
            }
            for name, meta in SOURCE_TABLES.items()
        }
        # 规则编号 -> {主键值: 关联结果行}，字典天然去重，保证幂等
        self._xref_results: dict[str, dict[str, dict[str, Any]]] = {}
        # 规则编号 -> [快照版本...]，只追加不覆盖
        self._xref_snapshots: dict[str, list[dict[str, Any]]] = {}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def source_names(self) -> list[str]:
        return sorted(self._sources)

    def source_table(self, name: str) -> dict[str, Any] | None:
        return self._sources.get(name)

    def xref_results(self, rule_no: str) -> dict[str, dict[str, Any]]:
        return self._xref_results.setdefault(rule_no, {})

    def xref_snapshots(self, rule_no: str) -> list[dict[str, Any]]:
        return self._xref_snapshots.setdefault(rule_no, [])

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()

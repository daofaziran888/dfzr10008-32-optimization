"""跨源数据关联业务规则：状态流转、字段校验与筛选口径都收在这里。

增量刷新约定：
- 按主键水位（水位字段 + 水位主键）选取受影响记录，只重算水位之后的数据；
- 关联结果按「规则编号 + 主键值」幂等写入，重复执行不追加同一记录；
- 字段冲突时，以已启用结果字段配置里优先级最高（数值最小）的候选为准；
- 每次刷新生成一个字段匹配快照版本，中断的刷新保留未完成快照，
  从中断水位继续；下游模型封装只读取最新完整快照，旧版本保留以便追溯；
- 主数据源、关联字段随关联时效一起保存，主数据源变化时关联字段映射
  同步重新计算，并重置水位等待全量重算。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "crossref"
DATAPACK_MODULE = "datapack"
REQUIRED_FIELDS = ["规则编号", "主数据源", "关联数据源"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已关闭"]
ACTION_RULES = {"启用关联": "草稿", "暂停关联": "已启用", "关闭关联": "已暂停"}
NEGATIVE_ACTIONS = []

WATERMARK_FORMAT = "%Y-%m-%d %H:%M"
DEFAULT_WATERMARK_FIELD = "updated_at"
TIMELINESS_WINDOWS = {
    "实时": timedelta(seconds=0),
    "小时级": timedelta(hours=1),
    "日级": timedelta(days=1),
    "周级": timedelta(days=7),
}


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or "").strip(), WATERMARK_FORMAT)
    except ValueError:
        return None


def _fmt_ts(moment: datetime) -> str:
    return moment.strftime(WATERMARK_FORMAT)


def _now() -> str:
    return _fmt_ts(datetime.now())


def resolve_field_plan(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """裁决结果字段：同一输出字段取已启用配置中优先级最高（数值最小）的候选。"""
    winners: dict[str, dict[str, Any]] = {}
    for conf in entry.get("结果字段配置") or []:
        if not conf.get("启用", True):
            continue
        out = str(conf.get("输出字段") or "").strip()
        if not out:
            continue
        current = winners.get(out)
        if current is None or int(conf.get("优先级", 99)) < int(current.get("优先级", 99)):
            winners[out] = conf
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for conf in entry.get("结果字段配置") or []:
        out = str(conf.get("输出字段") or "").strip()
        if out and out not in seen and winners.get(out) is conf:
            plan.append(conf)
            seen.add(out)
    return plan


def latest_complete_snapshot(rule_no: str) -> dict[str, Any] | None:
    """下游读取面：模型封装只读最新完整字段匹配快照。"""
    complete = [snap for snap in store.xref_snapshots(rule_no) if snap.get("完整")]
    return complete[-1] if complete else None


class CrossrefService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于跨源数据关联可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"关联规则已{action}"

    # ------------------------------------------------------------------
    # 配置保存：主数据源、关联字段随关联时效一起保存
    # ------------------------------------------------------------------
    def update_config(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        primary_changed = False
        join_changed = False
        for field in ("主数据源", "关联数据源", "时效控制"):
            new_value = str(values.get(field) or "").strip()
            if not new_value:
                continue
            if field == "时效控制" and new_value not in TIMELINESS_WINDOWS:
                return None, f"关联时效「{new_value}」不支持，可选：{'、'.join(TIMELINESS_WINDOWS)}"
            if field == "主数据源" and new_value != str(entry.get("主数据源") or ""):
                primary_changed = True
            entry[field] = new_value
        mapping = values.get("关联字段映射")
        if isinstance(mapping, dict) and mapping:
            cleaned = {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip() and str(v).strip()}
            if cleaned and cleaned != (entry.get("关联字段映射") or {}):
                entry["关联字段映射"] = cleaned
                join_changed = True
        notes: list[str] = []
        if primary_changed:
            # 主数据源变化：关联字段映射同步重新计算
            entry["关联字段映射"] = self._auto_field_mapping(entry)
            join_changed = False
            notes.append("主数据源变化，关联字段映射已重新计算")
        if primary_changed or join_changed:
            # 关联基础变化，旧结果与新口径不再可比：重置水位，等待重算
            self._reset_increment(entry)
            notes.append("水位已重置，下次刷新将重算关联结果")
        entry["关联字段"] = self._render_mapping(entry)
        message = "关联规则配置已保存（主数据源、关联字段、关联时效已一起保存）"
        if notes:
            message += "；" + "；".join(notes)
        return entry, message

    def _auto_field_mapping(self, entry: dict[str, Any]) -> dict[str, str]:
        """按主数据源与关联数据源的同名字段重算关联字段映射，优先关联表主键。"""
        primary = store.source_table(str(entry.get("主数据源") or ""))
        linked = store.source_table(str(entry.get("关联数据源") or ""))
        if primary is None or linked is None:
            return {}
        common = [
            field for field in primary["fields"]
            if field in linked["fields"] and field != DEFAULT_WATERMARK_FIELD
        ]
        linked_pk = str(linked["pk"])
        if linked_pk in common:
            return {linked_pk: linked_pk}
        if common:
            return {common[0]: common[0]}
        return {}

    def _render_mapping(self, entry: dict[str, Any]) -> str:
        mapping = entry.get("关联字段映射") or {}
        primary = str(entry.get("主数据源") or "")
        linked = str(entry.get("关联数据源") or "")
        return "、".join(f"{primary}.{p} = {linked}.{l}" for p, l in mapping.items())

    def _reset_increment(self, entry: dict[str, Any]) -> None:
        rule_no = str(entry.get("规则编号") or "")
        entry["水位"] = ""
        entry["水位主键"] = ""
        entry["刷新状态"] = {"状态": "未刷新", "已处理": 0, "候选总数": 0, "最近刷新": ""}
        store.xref_results(rule_no).clear()
        pending = [snap for snap in store.xref_snapshots(rule_no) if not snap.get("完整")]
        for snap in pending:
            store.xref_snapshots(rule_no).remove(snap)

    # ------------------------------------------------------------------
    # 增量刷新
    # ------------------------------------------------------------------
    def refresh(self, entry_id: int, options: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        if entry.get("status") != "已启用":
            return None, "关联规则未启用，请先启用关联再刷新"
        rule_no = str(entry.get("规则编号") or "")
        primary = store.source_table(str(entry.get("主数据源") or ""))
        if primary is None:
            return None, f"主数据源「{entry.get('主数据源')}」未接入源数据，无法增量刷新"
        linked = store.source_table(str(entry.get("关联数据源") or ""))
        if linked is None:
            return None, f"关联数据源「{entry.get('关联数据源')}」未接入源数据，无法增量刷新"
        plan = resolve_field_plan(entry)
        if not plan:
            return None, "结果字段配置为空，请先维护已启用的结果字段"
        mapping = entry.get("关联字段映射") or {}
        if not mapping:
            return None, "关联字段映射为空，请先保存主数据源与关联字段"

        interrupt_after = options.get("interrupt_after")
        interrupt_after = int(interrupt_after) if interrupt_after is not None else None

        before_watermark = str(entry.get("水位") or "") or "（空）"
        candidates = self._select_increment(entry, primary)
        results = store.xref_results(rule_no)
        snapshots = store.xref_snapshots(rule_no)

        if not candidates and not any(not snap.get("完整") for snap in snapshots):
            summary = self._summary(entry, before_watermark, 0, 0, len(results), None)
            return summary, "水位之后没有新记录，无需刷新；重复执行不会追加关联结果"

        # 中断后恢复：沿用未完成的快照版本；否则开启新版本
        snapshot = next((snap for snap in reversed(snapshots) if not snap.get("完整")), None)
        if snapshot is None:
            snapshot = {
                "版本": (snapshots[-1]["版本"] if snapshots else 0) + 1,
                "规则编号": rule_no,
                "生成时间": _now(),
                "完成时间": "",
                "水位": str(entry.get("水位") or ""),
                "完整": False,
                "字段": [
                    {"输出字段": f["输出字段"], "取值来源": f["来源"], "源字段": f["源字段"], "优先级": f["优先级"]}
                    for f in plan
                ],
                "记录数": 0,
            }
            snapshots.append(snapshot)

        linked_index = self._build_linked_index(linked, mapping)
        pk_field = str(entry.get("主键") or primary["pk"])
        watermark_field = str(entry.get("水位字段") or DEFAULT_WATERMARK_FIELD)

        processed = 0
        interrupted = False
        for row in candidates:
            if interrupt_after is not None and processed >= interrupt_after:
                interrupted = True
                break
            linked_row = self._find_linked(linked_index, mapping, row)
            result: dict[str, Any] = {}
            for field in plan:
                source_row = row if field["来源"] == "主数据源" else (linked_row or {})
                result[str(field["输出字段"])] = source_row.get(field["源字段"])
            result["快照版本"] = snapshot["版本"]
            pk_value = str(row.get(pk_field))
            results[pk_value] = result  # 幂等：同一主键只保留一份关联结果
            entry["水位"] = str(row.get(watermark_field) or entry.get("水位") or "")
            entry["水位主键"] = pk_value
            processed += 1

        total = len(candidates)
        if interrupted:
            entry["刷新状态"] = {"状态": "中断", "已处理": processed, "候选总数": total, "最近刷新": _now()}
            summary = self._summary(entry, before_watermark, processed, total, len(results), snapshot)
            return summary, (
                f"刷新中断：已处理 {processed}/{total} 条，水位推进到 {entry.get('水位') or before_watermark}，"
                "再次刷新将从该水位继续，不会重复追加"
            )

        snapshot["完整"] = True
        snapshot["完成时间"] = _now()
        snapshot["水位"] = str(entry.get("水位") or "")
        snapshot["记录数"] = len(results)
        entry["刷新状态"] = {"状态": "完成", "已处理": processed, "候选总数": total, "最近刷新": _now()}
        synced = self._sync_datapack(entry, snapshot)
        summary = self._summary(entry, before_watermark, processed, total, len(results), snapshot)
        message = (
            f"增量刷新完成：处理 {processed} 条受影响记录，水位 {before_watermark} → {entry.get('水位')}，"
            f"生成完整快照 v{snapshot['版本']}"
        )
        if synced:
            message += f"，已同步模型封装：{'、'.join(synced)}"
        return summary, message

    def _select_increment(self, entry: dict[str, Any], primary: dict[str, Any]) -> list[dict[str, Any]]:
        """只选水位之后的受影响记录，按（水位字段, 主键）稳定排序。"""
        pk_field = str(entry.get("主键") or primary["pk"])
        watermark_field = str(entry.get("水位字段") or DEFAULT_WATERMARK_FIELD)
        watermark_ts = _parse_ts(entry.get("水位"))
        watermark_pk = str(entry.get("水位主键") or "")

        def position(row: dict[str, Any]) -> tuple[datetime, str]:
            return (_parse_ts(row.get(watermark_field)) or datetime.min, str(row.get(pk_field)))

        rows = [row for row in primary["rows"] if watermark_ts is None or position(row) > (watermark_ts, watermark_pk)]
        rows.sort(key=position)
        return rows

    def _build_linked_index(self, linked: dict[str, Any], mapping: dict[str, str]) -> dict[tuple[str, ...], dict[str, Any]]:
        index: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in linked["rows"]:
            key = tuple(str(row.get(linked_field)) for linked_field in mapping.values())
            index[key] = row
        return index

    def _find_linked(
        self,
        index: dict[tuple[str, ...], dict[str, Any]],
        mapping: dict[str, str],
        primary_row: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = tuple(str(primary_row.get(primary_field)) for primary_field in mapping.keys())
        return index.get(key)

    def _sync_datapack(self, entry: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
        """增量恢复/刷新完成后，把最新完整快照同步给下游模型封装。"""
        source_names = {"主数据源": entry.get("主数据源"), "关联数据源": entry.get("关联数据源")}
        mapping_text = "；".join(
            f"{field['输出字段']}←{source_names.get(field['取值来源'], field['取值来源'])}.{field['源字段']}"
            for field in snapshot["字段"]
        )
        synced: list[str] = []
        for row in store.rows(DATAPACK_MODULE):
            if str(row.get("关联规则编号") or "") == str(entry.get("规则编号") or ""):
                row["字段映射"] = mapping_text
                row["快照版本"] = snapshot["版本"]
                row["最近同步"] = snapshot["完成时间"]
                synced.append(str(row.get("封装编号") or row.get("id")))
        return synced

    def _summary(
        self,
        entry: dict[str, Any],
        before_watermark: str,
        processed: int,
        total: int,
        result_count: int,
        snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "规则编号": entry.get("规则编号"),
            "上次水位": before_watermark,
            "当前水位": entry.get("水位") or "",
            "关联时效": entry.get("时效控制") or "",
            "时效到期": self._timeliness_due(entry),
            "候选记录": total,
            "本次处理": processed,
            "结果总数": result_count,
            "快照版本": snapshot["版本"] if snapshot else None,
            "快照完整": snapshot["完整"] if snapshot else None,
            "刷新状态": (entry.get("刷新状态") or {}).get("状态", ""),
        }

    def _timeliness_due(self, entry: dict[str, Any]) -> bool | None:
        """按关联时效判断当前结果是否已过期需要刷新。"""
        window = TIMELINESS_WINDOWS.get(str(entry.get("时效控制") or ""))
        watermark_ts = _parse_ts(entry.get("水位"))
        if window is None or watermark_ts is None:
            return None
        return datetime.now() > watermark_ts + window

    # ------------------------------------------------------------------
    # 结果、快照与一致性
    # ------------------------------------------------------------------
    def list_results(self, entry_id: int) -> tuple[list[dict[str, Any]] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        results = store.xref_results(str(entry.get("规则编号") or ""))
        return list(results.values()), ""

    def list_snapshots(self, entry_id: int) -> tuple[list[dict[str, Any]] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        snapshots = store.xref_snapshots(str(entry.get("规则编号") or ""))
        return list(reversed(snapshots)), ""

    def latest_snapshot(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        snapshot = latest_complete_snapshot(str(entry.get("规则编号") or ""))
        if snapshot is None:
            return None, f"关联规则 {entry.get('规则编号')} 暂无完整快照，请先完成一次增量刷新"
        return snapshot, ""

    def verify(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """校验关联结果与最新完整字段匹配快照是否一致。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"关联规则 {entry_id} 不存在或已归档"
        rule_no = str(entry.get("规则编号") or "")
        snapshot = latest_complete_snapshot(rule_no)
        if snapshot is None:
            return None, f"关联规则 {rule_no} 暂无完整快照，无法校验一致性"
        expected = [str(field["输出字段"]) for field in snapshot["字段"]]
        results = store.xref_results(rule_no)
        mismatches: list[dict[str, Any]] = []
        for pk_value, row in results.items():
            actual = [key for key in row if key != "快照版本"]
            missing = [field for field in expected if field not in actual]
            extra = [field for field in actual if field not in expected]
            if missing or extra:
                mismatches.append({"主键值": pk_value, "缺失字段": missing, "多余字段": extra})
        report = {
            "规则编号": rule_no,
            "快照版本": snapshot["版本"],
            "结果总数": len(results),
            "重复主键": 0,  # 结果按主键值字典存放，天然只保留一份
            "不一致记录": mismatches,
            "一致": not mismatches,
        }
        message = "关联结果与最新完整快照字段一致" if not mismatches else f"发现 {len(mismatches)} 条结果与快照字段不一致"
        return report, message

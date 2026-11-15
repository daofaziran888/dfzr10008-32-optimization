"""跨源数据关联接口：维护关联规则，覆盖启用关联、暂停关联、关闭关联等动作。

增量刷新相关接口：
- PUT  /{entry_id}/config    主数据源、关联字段随关联时效一起保存；主数据源变化时重算字段映射
- POST /{entry_id}/refresh   按主键水位增量刷新，中断后从上次水位继续，重复执行幂等
- GET  /{entry_id}/results   当前跨源关联结果（同一主键只保留一份）
- GET  /{entry_id}/snapshots 字段匹配快照版本列表，旧版本保留以便追溯
- GET  /{entry_id}/snapshots/latest 最新完整快照，即下游模型封装的读取面
- GET  /{entry_id}/verify    校验关联结果与最新完整快照是否一致
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.crossref import CrossrefService
from app.store import store

router = APIRouter(prefix="/api/crossref", tags=["跨源数据关联"])

service = CrossrefService()

LIST_FIELDS = ["规则编号", "主数据源", "关联数据源", "关联字段", "关联类型", "结果字段", "时效控制", "规则状态", "水位"]
STATUSES = ["草稿", "已启用", "已暂停", "已关闭"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、已启用、已暂停、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤跨源数据关联列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """列出可参与跨源关联的源数据表，供配置主数据源、关联数据源时选择。"""
    return {"sources": store.source_names()}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出跨源数据关联清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "crossref", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条关联规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"关联规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条关联规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="关联规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条关联规则执行启用关联、暂停关联、关闭关联；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}/config", response_model=ActionResult)
def update_config(entry_id: int, payload: EntryPayload) -> ActionResult:
    """主数据源、关联字段随关联时效一起保存；主数据源变化时同步重算关联字段映射。"""
    entry, message = service.update_config(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/refresh", response_model=ActionResult)
def refresh(entry_id: int, payload: EntryPayload) -> ActionResult:
    """按主键水位与关联时效增量刷新：只重算受影响记录，中断后可从上次水位继续。"""
    summary, message = service.refresh(entry_id, payload.values)
    if summary is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=summary)


@router.get("/{entry_id}/results")
def list_results(entry_id: int) -> dict[str, Any]:
    """当前跨源关联结果：按主键幂等保存，重复重算只保留一份。"""
    items, message = service.list_results(entry_id)
    if items is None:
        raise HTTPException(status_code=404, detail=message)
    return {"total": len(items), "items": items}


@router.get("/{entry_id}/snapshots")
def list_snapshots(entry_id: int) -> dict[str, Any]:
    """字段匹配快照版本列表：旧版本保留以便追溯。"""
    items, message = service.list_snapshots(entry_id)
    if items is None:
        raise HTTPException(status_code=404, detail=message)
    return {"total": len(items), "items": items}


@router.get("/{entry_id}/snapshots/latest")
def latest_snapshot(entry_id: int) -> dict[str, Any]:
    """最新完整字段匹配快照：下游模型封装的读取面。"""
    snapshot, message = service.latest_snapshot(entry_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=message)
    return snapshot


@router.get("/{entry_id}/verify")
def verify(entry_id: int) -> dict[str, Any]:
    """校验当前关联结果与最新完整快照的字段一致性。"""
    report, message = service.verify(entry_id)
    if report is None:
        raise HTTPException(status_code=404, detail=message)
    report["说明"] = message
    return report

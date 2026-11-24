"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload, ActionResult, ClaimPayload, EntryPayload, PageResult, TriggerPayload,
)
from app.services import service_for
from app.services.alert_policy import AlertPolicyService
from app.services.anomaly_analysis import SOURCE_CLAIM, SOURCE_HISTORY

router = APIRouter(prefix="/api", tags=["业务资源"])

# 归因列表额外接受的查询参数（证据维度精确过滤）
ANOMALY_FILTER_KEYS = {
    "rule_id", "duty_group", "metric_id", "metric_value_id",
    "alert_log_id", "evidence_alert_log_id", "handler",
    "触发规则", "归属值班组", "指标采样",
}


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


def _extra_params(module: str, request: Request) -> dict[str, Any]:
    """取出模块声明的额外过滤参数（精确匹配，不做模糊 LIKE）。"""
    service = require_service(module)
    allowed = set(service.EXTRA_FILTER_COLUMNS)
    if module == "anomaly_analysis":
        allowed |= ANOMALY_FILTER_KEYS
    extra: dict[str, Any] = {}
    for key, value in request.query_params.items():
        # 已由 filters/keyword 处理的字段不再重复精确过滤
        if key in service.LIST_FIELDS:
            continue
        if key in allowed and str(value).strip():
            extra[key] = str(value).strip()
    return extra


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    anchor_id: int | None = Query(default=None, ge=0),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total, anchor = service.list_entries_page(
        filters=filters, keyword=keyword, status=status,
        page=page, size=size, extra=_extra_params(module, request),
        anchor_id=anchor_id,
    )
    return PageResult(items=items, total=total, page=page, size=size, anchor_id=anchor)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


# ----------------------------------------------------------------------
# 告警历史 / 触发证据
# ----------------------------------------------------------------------
@router.get("/alert_policy/history", response_model=dict)
def alert_history(
    request: Request,
    rule_id: int | None = Query(default=None),
    duty_group: str | None = Query(default=None),
    metric_id: int | None = Query(default=None),
    metric_value_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    anchor_id: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    service = require_service("alert_policy")
    # /api/alert_policy/history 与 /api/alert_policy/{alert_id} 共用前缀；
    # 若把 "history" 当 id 解析会 422，这里只认纯数字参数。
    rule_id = rule_id or _int_param(request, "触发规则") or _int_param(request, "rule_id")
    alert_id = _int_param(request, "alert_id")
    if alert_id is not None and rule_id is None:
        rule_id = alert_id
    metric_value_id = metric_value_id or _int_param(request, "指标采样")
    metric_id = metric_id or _int_param(request, "metric_id")
    duty_group = duty_group or request.query_params.get("归属值班组") or None
    items, total, anchor = service.list_history(
        rule_id=rule_id, duty_group=duty_group, metric_id=metric_id,
        metric_value_id=metric_value_id, page=page, size=size, anchor_id=anchor_id,
    )
    return {"items": items, "total": total, "page": page, "size": size, "anchor_id": anchor}


@router.get("/alert_policy/{alert_id}/history", response_model=dict)
def alert_history_by_rule(alert_id: int, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    service = require_service("alert_policy")
    return {"items": service.alert_history(alert_id, limit)}


@router.post("/alert_policy/{alert_id}/trigger", response_model=ActionResult)
def register_trigger(alert_id: int, payload: TriggerPayload) -> ActionResult:
    service = require_service("alert_policy")
    result = service.trigger(alert_id, payload.metric_value_id, payload.triggered_at)
    entry = result.get("entry")
    view = AlertPolicyService._log_view(dict(entry)) if entry else None
    return ActionResult(ok=result["ok"], message=result["message"], entry=view)


# ----------------------------------------------------------------------
# 异常归因：证据工作台 / 按规则历史 / 认领
# ----------------------------------------------------------------------
@router.get("/anomaly_analysis/evidence", response_model=dict)
def anomaly_evidence(
    alert_log_id: int = Query(...),
    duty_group: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    anchor_id: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    service = require_service("anomaly_analysis")
    return service.evidence_workspace(
        alert_log_id=alert_log_id, duty_group=duty_group,
        page=page, size=size, anchor_id=anchor_id,
    )


@router.get("/anomaly_analysis/rule/{rule_id}/history", response_model=dict)
def anomaly_rule_history(
    rule_id: int,
    duty_group: str = Query(default=""),
    metric_value_id: int | None = Query(default=None),
    alert_log_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    anchor_id: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    service = require_service("anomaly_analysis")
    return service.by_rule_history(
        rule_id, duty_group=duty_group, metric_value_id=metric_value_id,
        alert_log_id=alert_log_id, page=page, size=size, anchor_id=anchor_id,
    )


@router.post("/anomaly_analysis/from-evidence", response_model=ActionResult)
def create_from_evidence(payload: ClaimPayload) -> ActionResult:
    service = require_service("anomaly_analysis")
    entry, message = service.create_from_evidence(
        alert_log_id=payload.alert_log_id,
        duty_group=payload.duty_group,
        handler=payload.handler,
        source=payload.source or SOURCE_HISTORY,
        dimensions=payload.dimensions,
        remark=payload.remark,
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message,
                        entry=service._analysis_view(entry))


@router.post("/anomaly_analysis/{entry_id}/claim", response_model=ActionResult)
def claim_evidence(entry_id: int, payload: ClaimPayload) -> ActionResult:
    service = require_service("anomaly_analysis")
    result = service.claim_from_evidence(
        alert_log_id=payload.alert_log_id,
        analysis_id=entry_id,
        handler=payload.handler,
        duty_group=payload.duty_group,
        source=payload.source or SOURCE_CLAIM,
        rule_id=payload.rule_id,
    )
    return ActionResult(
        ok=result["ok"], message=result["message"], entry=result.get("entry"))


# ----------------------------------------------------------------------
# 通用条目
# ----------------------------------------------------------------------
@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry_view(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    view = service._row_view(entry) if entry is not None else None
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=view)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(
        entry_id, payload.action.strip(), payload.values or None)
    view = service._row_view(entry) if entry is not None else None
    return ActionResult(ok=entry is not None, message=message, entry=view)


def _int_param(request: Request, name: str) -> int | None:
    raw = request.query_params.get(name)
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None

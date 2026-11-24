"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20
    # 首次查询时结果集的 id 上界。翻页时原样回传，后插入的记录不会挤动已看过的页。
    anchor_id: int | None = None


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class ClaimPayload(BaseModel):
    """按证据认领：只记录本次办理人和来源，不覆盖原归因记录。"""

    alert_log_id: int
    rule_id: int | None = None
    duty_group: str = ""
    handler: str = ""
    source: str = ""
    dimensions: str = ""
    remark: str | None = None


class TriggerPayload(BaseModel):
    """登记一次告警触发证据，绑定到精确的指标采样（metric_value_id）。"""

    metric_value_id: int
    triggered_at: str | None = None
    # 兼容把 alert_id 放在 body 的调用方；优先使用路径参数。
    alert_id: int | None = None

"""实时数据分析与可视化应用平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8019
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import db
from app.pipeline import _resolve_metric, seed_real_data
from app.routers import ROUTERS, PIPELINE_ROUTER
from app.store import store

app = FastAPI(title="实时数据分析与可视化应用平台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_seed() -> None:
    """应用启动时初始化种子数据。"""
    try:
        existing = db.count("metrics")
        if existing == 0:
            counts = seed_real_data()
            print(f"[seed] 数据集初始化完成: {counts}")
        else:
            print(f"[seed] 数据集已存在，跳过初始化 (metrics={existing})")
        backfill_alert_evidence()
    except Exception as e:
        print(f"[seed] 初始化失败: {e}")


def backfill_alert_evidence() -> None:
    """为旧库里的告警规则/触发日志补齐 metric_id、duty_group 和指标采样绑定。

    只处理缺失字段，不动已有绑定，避免把后来插入的采样塞给历史证据。
    """
    rules = db.fetchall("SELECT * FROM alerts WHERE metric_id IS NULL OR duty_group=''")
    for rule in rules:
        metric = _resolve_metric(None, rule.get("monitor_metric", ""))
        db.update("alerts", {
            "metric_id": rule.get("metric_id") or (metric["id"] if metric else None),
            "duty_group": rule.get("duty_group") or _default_duty_group(rule),
        }, "id=?", [rule["id"]])

    # 旧日志没有 metric_value_id：按触发时间就近绑定同一指标的历史采样，
    # 找不到就留空并显式标记为不可作证据，绝不用"最新值"顶替。
    logs = db.fetchall("SELECT * FROM alert_log WHERE metric_value_id IS NULL")
    for log in logs:
        rule = db.fetchone("SELECT * FROM alerts WHERE id=?", [log["alert_id"]])
        if rule is None:
            continue
        metric_id = rule.get("metric_id")
        if not metric_id:
            metric = _resolve_metric(None, rule.get("monitor_metric", ""))
            metric_id = metric["id"] if metric else None
        if not metric_id:
            continue
        sample = db.fetchone(
            "SELECT * FROM metric_values WHERE metric_id=? AND snapshot_time <= ? "
            "ORDER BY snapshot_time DESC, id DESC LIMIT 1",
            [metric_id, log.get("triggered_at", "")],
        )
        if sample is None:
            sample = db.fetchone(
                "SELECT * FROM metric_values WHERE metric_id=? "
                "ORDER BY snapshot_time ASC, id ASC LIMIT 1",
                [metric_id],
            )
        if sample is None:
            continue
        db.update("alert_log", {
            "rule_id": rule["id"],
            "metric_value_id": sample["id"],
            "metric_id": metric_id,
            "metric_name": rule.get("monitor_metric", ""),
            "snapshot_time": sample["snapshot_time"],
            "metric_value": log.get("metric_value") if log.get("metric_value") is not None
            else sample["snapshot_value"],
            "rule_name": rule.get("rule_name", ""),
            "duty_group": log.get("duty_group") or rule.get("duty_group", ""),
        }, "id=?", [log["id"]])


def _default_duty_group(rule: dict) -> str:
    name = str(rule.get("rule_name", ""))
    if "转化" in name or "订单" in name:
        return "收入值班"
    return "渠道值班"


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "app": settings.app_name, "tables": len(db.table_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    return store.overview()


# 管道路由：数据分析核心入口
app.include_router(PIPELINE_ROUTER)

# 资源路由含 /api/{module}，必须放在 health / overview 之后注册
for module in ROUTERS:
    app.include_router(module.router)
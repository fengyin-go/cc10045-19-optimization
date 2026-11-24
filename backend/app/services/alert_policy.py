"""分析告警策略领域服务：定义指标的异常波动和阈值告警策略。

真实数据流：定义告警规则（监控指标/告警条件/连续触发次数/通知方式/归属值班组）
→ pipeline.check_alert 从 metric_values 读取最新值 → 对比告警条件
→ 达到连续阈值时触发告警日志 → 记录到 alert_log（带触发时的指标采样）
→ 下游异常归因按证据检索和办理。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.pipeline import _resolve_metric, check_alert, record_alert_trigger
from app.services.base import BaseModuleService


class AlertPolicyService(BaseModuleService):
    MODULE = "alert_policy"
    ENTITY = "告警规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "监控指标", "告警条件", "通知方式", "连续触发次数"]
    REQUIRED_FIELDS = ["规则名称", "监控指标", "告警条件"]
    STATUS_ORDER = ["待生效", "已生效", "已关闭"]
    ACTION_RULES = {"启用告警": "已生效", "关闭告警": "已关闭"}
    NEGATIVE_ACTIONS = ["关闭告警"]
    EXTRA_FILTER_COLUMNS = {
        "监控指标": "monitor_metric",
        "metric_id": "metric_id",
        "归属值班组": "duty_group",
        "duty_group": "duty_group",
        "rule_id": "id",
    }

    # ------------------------------------------------------------------
    # 规则维护
    # ------------------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记告警规则时把监控指标解析成 metric_id 并记录归属值班组。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        metric_name = str(values.get("监控指标") or entry.get("monitor_metric") or "").strip()
        metric_id = values.get("metric_id")
        if not metric_id and metric_name:
            metric = _resolve_metric(None, metric_name)
            metric_id = metric["id"] if metric else None
        duty_group = str(values.get("归属值班组") or values.get("duty_group") or "").strip()
        if metric_id or duty_group:
            db.update("alerts", {
                "metric_id": metric_id,
                "duty_group": duty_group,
            }, "id=?", [entry["id"]])
            entry = db.fetchone("SELECT * FROM alerts WHERE id=?", [entry["id"]])
        return entry, []

    # ------------------------------------------------------------------
    # 告警检查
    # ------------------------------------------------------------------
    def check_all(self) -> dict[str, Any]:
        """检查所有已生效告警规则。"""
        alerts = db.fetchall("SELECT * FROM alerts WHERE status='已生效'")
        results = []
        triggered_count = 0

        for alert in alerts:
            result = check_alert(dict(alert))
            if result:
                results.append({
                    "rule_name": alert["rule_name"],
                    "triggered": result.get("triggered", False),
                    "message": result.get("message", ""),
                })
                if result.get("triggered"):
                    triggered_count += 1

        return {
            "checked": len(alerts),
            "triggered": triggered_count,
            "details": results,
        }

    def check_single(self, alert_id: int) -> dict[str, Any]:
        """检查单个告警规则。"""
        alert = db.fetchone("SELECT * FROM alerts WHERE id=?", [alert_id])
        if alert is None:
            return {"ok": False, "message": f"告警规则 {alert_id} 不存在"}

        result = check_alert(dict(alert))
        return {
            "ok": True,
            "rule_name": alert["rule_name"],
            "check_result": result,
        }

    # ------------------------------------------------------------------
    # 告警历史与证据
    # ------------------------------------------------------------------
    def alert_history(
        self, alert_id: int, limit: int = 50, *, duty_group: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取指定规则的告警触发历史，每条都带触发时的指标采样。"""
        where = "l.alert_id=?"
        params: list[Any] = [alert_id]
        if duty_group:
            where += " AND l.duty_group=?"
            params.append(duty_group)
        rows = db.fetchall(
            f"SELECT l.*, a.monitor_metric, a.rule_name AS current_rule_name, "
            f"a.duty_group AS current_duty_group "
            f"FROM alert_log l LEFT JOIN alerts a ON a.id = l.alert_id "
            f"WHERE {where} ORDER BY l.triggered_at DESC, l.id DESC LIMIT ?",
            params + [limit],
        )
        return [self._log_view(dict(row)) for row in rows]

    def list_history(
        self,
        *,
        rule_id: int | None = None,
        duty_group: str | None = None,
        metric_id: int | None = None,
        metric_value_id: int | None = None,
        page: int = 1,
        size: int = 20,
        anchor_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """按规则/值班组/指标采样检索告警历史（证据池）。

        过滤按精确的 rule_id、metric_id、metric_value_id，不做名称模糊匹配，
        因此同名指标或同一分钟的两个采样不会互相串位。
        """
        where = ["1=1"]
        params: list[Any] = []
        if rule_id is not None:
            where.append("l.rule_id=?")
            params.append(rule_id)
        if duty_group:
            where.append("l.duty_group=?")
            params.append(duty_group)
        if metric_id is not None:
            where.append("l.metric_id=?")
            params.append(metric_id)
        if metric_value_id is not None:
            where.append("l.metric_value_id=?")
            params.append(metric_value_id)
        base = " AND ".join(where)

        if anchor_id is None:
            row = db.fetchone(
                f"SELECT COALESCE(MAX(l.id), 0) AS anchor FROM alert_log l WHERE {base}",
                params,
            )
            anchor_id = int(row["anchor"]) if row else 0

        page_where = f"{base} AND l.id <= ?"
        page_params = params + [anchor_id]
        total = db.count("alert_log l", page_where, page_params)
        rows = db.fetchall(
            f"SELECT l.*, a.monitor_metric, a.alert_condition "
            f"FROM alert_log l LEFT JOIN alerts a ON a.id = l.alert_id "
            f"WHERE {page_where} ORDER BY l.id DESC LIMIT ? OFFSET ?",
            page_params + [size, (page - 1) * size],
        )
        return [self._log_view(dict(row)) for row in rows], total, anchor_id

    # ------------------------------------------------------------------
    # 人工登记触发证据（演示/补录触发）
    # ------------------------------------------------------------------
    def trigger(self, alert_id: int, metric_value_id: int, triggered_at: str | None = None) -> dict[str, Any]:
        """按指定的指标采样登记一次告警触发。

        必须显式给出 metric_value_id，服务端不会用"同名指标的最新值"来替换它，
        保证调用方看到什么证据，落库的就是哪一条证据。
        """
        alert = db.fetchone("SELECT * FROM alerts WHERE id=?", [alert_id])
        if alert is None:
            return {"ok": False, "message": f"告警规则 {alert_id} 不存在"}
        metric_value = db.fetchone("SELECT * FROM metric_values WHERE id=?", [metric_value_id])
        if metric_value is None:
            return {"ok": False, "message": f"指标采样 {metric_value_id} 不存在"}
        if alert.get("metric_id") and alert["metric_id"] != metric_value["metric_id"]:
            return {"ok": False, "message": "该指标采样不属于此告警规则监控的指标"}

        metric = db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_value["metric_id"]])
        metric_value = dict(metric_value)
        metric_value["metric_name"] = metric["metric_name"] if metric else ""
        log_id = record_alert_trigger(dict(alert), metric_value, triggered_at)
        log = db.fetchone("SELECT * FROM alert_log WHERE id=?", [log_id])
        return {"ok": True, "message": "告警触发证据已登记", "entry": self._log_view(dict(log)) if log else None}

    @staticmethod
    def _log_view(row: dict[str, Any]) -> dict[str, Any]:
        for chinese, column in (
            ("规则名称", "rule_name"), ("监控指标", "metric_name"),
            ("归属值班组", "duty_group"), ("指标采样", "metric_value_id"),
            ("采样时间", "snapshot_time"),
        ):
            row.setdefault(chinese, row.get(column, ""))
        if not row.get("rule_name"):
            row["rule_name"] = row.get("current_rule_name", "")
        if not row.get("duty_group"):
            row["duty_group"] = row.get("current_duty_group", "")
        return row

    def resolve_alert(self, log_id: int) -> dict[str, Any]:
        """标记告警为已解决。"""
        db.update("alert_log", {"resolved": 1}, "id=?", [log_id])
        return {"ok": True, "message": "告警已标记为已解决"}

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_rules"] = db.count("alerts")
        snapshot["active_rules"] = db.count("alerts", "status='已生效'")
        snapshot["today_triggers"] = db.count("alert_log")
        return snapshot

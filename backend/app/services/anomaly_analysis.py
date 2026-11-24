"""异常归因领域服务：对指标异常波动自动执行多维度归因和贡献度分析。

真实数据流：告警历史给出触发时的指标采样（metric_values.id）、触发规则和归属值班组
→ 异常归因以该证据（alert_log.id + metric_value_id）过滤/创建归因记录
→ pipeline.detect_anomaly 执行 z-score 检测并按维度解构贡献度
→ 每条归因记录保存它实际取用的指标采样，办理时记录办理人与来源。

两条可靠性约束：
1. 证据只按 ``metric_value_id`` / ``alert_log.id`` / ``rule_id`` 精确绑定，
   不用指标名称或时间字符串匹配，因此同名指标、同一分钟的两个采样不会串位；
2. 办理动作只写本条归因记录的办理人与来源，绝不改写其它值班组已经创建的记录。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import detect_anomaly
from app.services.base import BaseModuleService

# 前端筛选告警证据时允许的中文参数 -> DB 列
EVIDENCE_FILTERS = {
    "触发规则": "rule_id",
    "规则名称": "rule_name",
    "归属值班组": "duty_group",
    "指标采样": "metric_value_id",
    "监控指标": "metric_name",
    "指标ID": "metric_id",
}

SOURCE_HISTORY = "告警历史"
SOURCE_DIRECT = "直接登记"
SOURCE_CLAIM = "认领证据"


class AnomalyAnalysisService(BaseModuleService):
    MODULE = "anomaly_analysis"
    ENTITY = "归因分析器"
    OPERATION_TERM = "分析"
    LIST_FIELDS = ["分析对象", "异常时间点", "归因维度", "贡献度排序", "分析结论"]
    REQUIRED_FIELDS = ["分析对象", "异常时间点", "归因维度"]
    STATUS_ORDER = ["待分析", "分析中", "已完成", "分析失败"]
    ACTION_RULES = {"启动分析": "已完成", "取消分析": "分析失败"}
    NEGATIVE_ACTIONS = []
    EXTRA_FILTER_COLUMNS = {
        # 证据维度：全部精确匹配
        "rule_id": "rule_id",
        "触发规则": "rule_id",
        "duty_group": "duty_group",
        "归属值班组": "duty_group",
        "metric_id": "metric_id",
        "metric_value_id": "metric_value_id",
        "指标采样": "metric_value_id",
        "alert_log_id": "alert_log_id",
        "evidence_alert_log_id": "alert_log_id",
        "分析对象": "target_name",
        "handler": "handler",
    }

    # ================================================================
    # 列表：以证据过滤归因记录
    # ================================================================
    def list_entries_page(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, Any] | None = None,
        anchor_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """带证据过滤与稳定锚点的归因列表。

        ``metric_value_id`` 按"同一个指标采样"精确过滤，同名指标、同分钟的另一采样
        都不会命中；``alert_log_id`` 定位某一次规则触发；``rule_id`` 同时匹配记录
        自身规则与它引用的触发证据规则，保证"按触发规则查询"能命中。
        """
        extra = _normalize_extra(extra)
        where = ["1=1"]
        params: list[Any] = []

        alert_log_id = extra.get("alert_log_id") or extra.get("evidence_alert_log_id")
        metric_value_id = extra.get("metric_value_id")
        rule_id = extra.get("rule_id")

        # 证据过滤一律走精确 id：
        # - metric_value_id 是"同一个指标采样"的唯一身份，同名指标、同分钟的另一采样都不会命中；
        # - alert_log_id 定位某一次规则触发；
        # - EXISTS 只用于判断关联，不产生重复行，锚点统计也不受影响。
        if metric_value_id:
            where.append(
                "(a.metric_value_id = ? OR EXISTS ("
                "SELECT 1 FROM anomaly_evidence e "
                "WHERE e.analysis_id = a.id AND e.metric_value_id = ?))"
            )
            params.extend([_as_int(metric_value_id), _as_int(metric_value_id)])

        if alert_log_id:
            where.append(
                "EXISTS (SELECT 1 FROM anomaly_evidence e "
                "WHERE e.analysis_id = a.id AND e.alert_log_id = ?)"
            )
            params.append(_as_int(alert_log_id))

        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                where.append(f"a.{_column(field)} LIKE ?")
                params.append(f"%{expected}%")

        if keyword:
            where.append("a.target_name LIKE ?")
            params.append(f"%{keyword}%")

        if status:
            where.append("a.status = ?")
            params.append(status)

        if rule_id:
            rule_value = _as_int(rule_id)
            where.append(
                "(a.rule_id = ? OR EXISTS ("
                "SELECT 1 FROM anomaly_evidence e "
                "WHERE e.analysis_id = a.id AND e.rule_id = ?))"
            )
            params.extend([rule_value, rule_value])

        for key, value in extra.items():
            if key in ("alert_log_id", "evidence_alert_log_id", "metric_value_id", "rule_id"):
                continue
            if value is None or str(value).strip() == "":
                continue
            column = self.EXTRA_FILTER_COLUMNS.get(key)
            if not column:
                continue
            where.append(f"a.{column} = ?")
            params.append(_coerce(value))

        base = " AND ".join(where)

        if anchor_id is None:
            row = db.fetchone(
                f"SELECT COALESCE(MAX(a.id), 0) AS anchor FROM anomaly_analyses a WHERE {base}",
                params,
            )
            anchor_id = int(row["anchor"]) if row else 0

        page_where = f"{base} AND a.id <= ?"
        page_params = params + [anchor_id]
        total = db.count("anomaly_analyses a", page_where, page_params)
        rows = db.fetchall(
            f"SELECT a.* FROM anomaly_analyses a "
            f"WHERE {page_where} ORDER BY a.id DESC LIMIT ? OFFSET ?",
            page_params + [size, (page - 1) * size],
        )
        return [self._analysis_view(row) for row in rows], total, anchor_id

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        items, total, _ = self.list_entries_page(**kwargs)
        return items, total

    def get_entry_view(self, entry_id: int) -> dict[str, Any] | None:
        entry = self.get_entry(entry_id)
        return self._analysis_view(entry) if entry else None

    # ================================================================
    # 工作台：告警历史 → 证据 → 是否已有人办理
    # ================================================================
    def evidence_workspace(
        self,
        *,
        alert_log_id: int,
        duty_group: str = "",
        page: int = 1,
        size: int = 20,
        anchor_id: int | None = None,
    ) -> dict[str, Any]:
        """从一条告警历史（触发证据）出发，检索同一指标采样上的归因记录。

        "相同证据"以 metric_value_id 为身份：两条规则同时触发同一个采样时，
        任何一条规则历史都能看到另一组已经做过的分析。

        返回:
          evidence              触发时的指标采样、规则、归属值班组
          items / total         该指标采样上已有的归因记录（含办理人、来源）
          existing_analysis     别组已完成的、可被认领的分析（无则为 None）
          claimed_by_others     是否已有别的值班组办理过
          can_claim             是否能认领相同证据（有现成分析即可）
          needs_own_rule        是否必须按自己的规则新建归因（尚无任何分析）
        """
        log = db.fetchone(
            "SELECT l.*, a.monitor_metric, a.metric_id AS rule_metric_id "
            "FROM alert_log l LEFT JOIN alerts a ON a.id = l.alert_id WHERE l.id=?",
            [alert_log_id],
        )
        if log is None:
            return {"ok": False, "message": f"告警历史 {alert_log_id} 不存在"}
        log = dict(log)
        if not log.get("metric_value_id"):
            return {"ok": False, "message": "该告警历史没有绑定指标采样，无法作为归因证据"}

        metric_value = db.fetchone("SELECT * FROM metric_values WHERE id=?", [log["metric_value_id"]])
        if metric_value is None:
            return {"ok": False, "message": "告警历史引用的指标采样已不存在"}
        metric = db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_value["metric_id"]])

        evidence = {
            "alert_log_id": log["id"],
            "rule_id": log.get("rule_id") or log.get("alert_id"),
            "rule_name": log.get("rule_name", ""),
            "duty_group": log.get("duty_group", ""),
            "metric_value_id": metric_value["id"],
            "metric_id": metric_value["metric_id"],
            "metric_name": metric["metric_name"] if metric else log.get("metric_name", ""),
            "snapshot_time": metric_value["snapshot_time"],
            "snapshot_value": metric_value["snapshot_value"],
            "triggered_at": log.get("triggered_at", ""),
        }

        items, total, anchor = self.list_entries_page(
            extra={"metric_value_id": metric_value["id"]}, page=page, size=size,
            anchor_id=anchor_id,
        )

        current = (duty_group or log.get("duty_group") or "").strip()
        all_on_sample = self._analyses_on_sample(metric_value["id"])
        other_analyses = [
            item for item in all_on_sample
            if item.get("duty_group") != current
        ]
        own_analyses = [
            item for item in all_on_sample
            if item.get("duty_group") == current
        ]
        claimed_by_others = any(item.get("handler") for item in other_analyses)
        existing_analysis = next(
            (item for item in other_analyses if item.get("handler")), None)
        own_rule = self._rule_for_group(metric_value["metric_id"], current)
        already_own = bool(own_analyses)
        can_claim = existing_analysis is not None and not already_own
        needs_own_rule = not all_on_sample
        if already_own:
            next_step = "本组已就该指标采样做过归因，可直接查看记录"
        elif can_claim:
            next_step = "可认领相同证据（复用该指标采样，另存本组归因记录）"
        elif own_rule:
            next_step = "该采样尚无归因，可按本组规则发起归因"
        else:
            next_step = "该采样尚无归因且本组无对应规则，请按自己的规则新建归因"
        return {
            "ok": True,
            "evidence": evidence,
            "items": items,
            "total": total,
            "anchor_id": anchor,
            "current_duty_group": current,
            "existing_analysis": existing_analysis,
            "own_analyses": own_analyses,
            "already_own": already_own,
            "claimed_by_others": claimed_by_others,
            "other_handlers": sorted(
                {item.get("handler", "") for item in other_analyses} - {""}),
            "own_rule": own_rule,
            "can_claim": can_claim,
            "needs_own_rule": needs_own_rule,
            "next_step": next_step,
        }

    def _analyses_on_sample(self, metric_value_id: int) -> list[dict[str, Any]]:
        rows = db.fetchall(
            "SELECT * FROM anomaly_analyses WHERE metric_value_id=? ORDER BY id DESC",
            [metric_value_id],
        )
        return [self._analysis_view(row) for row in rows]

    def by_rule_history(
        self,
        rule_id: int,
        *,
        duty_group: str = "",
        metric_value_id: int | None = None,
        alert_log_id: int | None = None,
        page: int = 1,
        size: int = 20,
        anchor_id: int | None = None,
    ) -> dict[str, Any]:
        """值班员从自己的规则历史打开筛选结果。

        先按 rule_id 找该规则触发的告警历史（证据池），再对选中的证据
        检索是否已经做过归因，给出可认领/需自建的结论。
        """
        rule = db.fetchone("SELECT * FROM alerts WHERE id=?", [rule_id])
        if rule is None:
            return {"ok": False, "message": f"告警规则 {rule_id} 不存在"}
        rule = dict(rule)

        if alert_log_id:
            workspace = self.evidence_workspace(
                alert_log_id=alert_log_id, duty_group=duty_group or rule.get("duty_group", ""),
                page=page, size=size, anchor_id=anchor_id,
            )
            workspace["rule"] = _rule_view(rule)
            workspace["history"] = self._rule_history(rule_id)
            return workspace

        history_where = "l.rule_id=?"
        history_params: list[Any] = [rule_id]
        if metric_value_id is not None:
            history_where += " AND l.metric_value_id=?"
            history_params.append(metric_value_id)
        history = db.fetchall(
            f"SELECT l.*, a.monitor_metric FROM alert_log l "
            f"LEFT JOIN alerts a ON a.id=l.alert_id WHERE {history_where} "
            f"ORDER BY l.id DESC",
            history_params,
        )
        items: list[dict[str, Any]] = []
        for log in history:
            log = dict(log)
            workspace = self.evidence_workspace(
                alert_log_id=log["id"], duty_group=duty_group or rule.get("duty_group", ""),
                page=1, size=size,
            )
            analyses = workspace.get("items", [])
            has_own = any(
                item.get("rule_id") == rule_id and item.get("duty_group")
                == (duty_group or rule.get("duty_group", ""))
                for item in analyses
            )
            items.append({
                "alert_log_id": log["id"],
                "metric_value_id": log.get("metric_value_id"),
                "snapshot_time": log.get("snapshot_time", ""),
                "metric_name": log.get("metric_name", ""),
                "triggered_at": log.get("triggered_at", ""),
                "duty_group": log.get("duty_group", ""),
                "analyses": analyses,
                "analysis_total": len(analyses),
                "has_analysis": bool(analyses),
                "has_own_analysis": has_own,
                "can_claim": workspace.get("can_claim", False),
                "needs_own_rule": workspace.get("needs_own_rule", True),
                "existing_analysis": workspace.get("existing_analysis"),
                "next_step": workspace.get("next_step", ""),
            })
        return {
            "ok": True,
            "rule": _rule_view(rule),
            "current_duty_group": duty_group or rule.get("duty_group", ""),
            "history": items,
            "total": len(items),
        }

    def _rule_history(self, rule_id: int) -> list[dict[str, Any]]:
        return db.fetchall(
            "SELECT id, alert_id, rule_id, rule_name, duty_group, metric_value_id, "
            "metric_id, metric_name, snapshot_time, metric_value, triggered_at "
            "FROM alert_log WHERE rule_id=? ORDER BY id DESC",
            [rule_id],
        )

    def _rule_for_group(self, metric_id: int, duty_group: str) -> dict[str, Any] | None:
        """找到当前值班组监控同一指标、且已生效的规则。"""
        if not duty_group:
            return None
        rule = db.fetchone(
            "SELECT * FROM alerts WHERE duty_group=? AND metric_id=? "
            "ORDER BY CASE status WHEN '已生效' THEN 0 ELSE 1 END, id LIMIT 1",
            [duty_group, metric_id],
        )
        return _rule_view(dict(rule)) if rule else None

    # ================================================================
    # 从证据创建归因记录
    # ================================================================
    def create_from_evidence(
        self,
        *,
        alert_log_id: int,
        duty_group: str = "",
        handler: str = "",
        source: str = SOURCE_HISTORY,
        dimensions: str = "",
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """以告警历史的指标采样为证据登记一条归因记录。

        记录中保存的是 alert_log 绑定的 metric_value_id，而不是"同名指标最新值"，
        因此同分钟内的另一条采样插进来也不会改变这条记录实际取用的证据。
        """
        log = db.fetchone(
            "SELECT l.*, a.monitor_metric, a.metric_id AS rule_metric_id, a.alert_condition "
            "FROM alert_log l LEFT JOIN alerts a ON a.id=l.alert_id WHERE l.id=?",
            [alert_log_id],
        )
        if log is None:
            return None, f"告警历史 {alert_log_id} 不存在"
        log = dict(log)
        if not log.get("metric_value_id"):
            return None, "该告警历史没有绑定指标采样，无法作为归因证据"
        metric_value = db.fetchone("SELECT * FROM metric_values WHERE id=?", [log["metric_value_id"]])
        if metric_value is None:
            return None, "告警历史引用的指标采样已不存在"
        metric = db.fetchone("SELECT * FROM metrics WHERE id=?", [metric_value["metric_id"]])
        metric_name = metric["metric_name"] if metric else log.get("metric_name", "")

        rule_id = log.get("rule_id") or log.get("alert_id")
        rule = db.fetchone("SELECT * FROM alerts WHERE id=?", [rule_id]) if rule_id else None
        group = (duty_group or log.get("duty_group") or (rule["duty_group"] if rule else "")).strip()

        dims = dimensions
        if not dims:
            dims = json.dumps(["指标", "时间", "规则"], ensure_ascii=False)

        # 同一值班组对同一告警证据的重复登记保持幂等，避免重复点击产生多条记录
        existing = db.fetchone(
            "SELECT a.* FROM anomaly_analyses a "
            "JOIN anomaly_evidence e ON e.analysis_id = a.id "
            "WHERE e.alert_log_id=? AND a.duty_group=? ORDER BY a.id DESC LIMIT 1",
            [log["id"], group],
        )
        if existing is not None:
            return dict(existing), f"该证据已登记过归因记录（#{existing['id']}）"

        analysis_id = db.insert("anomaly_analyses", {
            "target_name": metric_name,
            "anomaly_time": metric_value["snapshot_time"],
            "attribution_dimensions": dims,
            "status": self.STATUS_ORDER[0],
            "metric_id": metric_value["metric_id"],
            "metric_name": metric_name,
            "metric_value_id": metric_value["id"],
            "snapshot_time": metric_value["snapshot_time"],
            "snapshot_value": metric_value["snapshot_value"],
            "rule_id": rule_id,
            "rule_name": log.get("rule_name", "") or (rule["rule_name"] if rule else ""),
            "duty_group": group,
            "evidence_source": source or SOURCE_HISTORY,
            "handler": handler,
        })
        db.insert("anomaly_evidence", {
            "analysis_id": analysis_id,
            "alert_log_id": log["id"],
            "rule_id": rule_id,
            "rule_name": log.get("rule_name", "") or (rule["rule_name"] if rule else ""),
            "duty_group": log.get("duty_group", ""),
            "metric_value_id": metric_value["id"],
            "metric_id": metric_value["metric_id"],
            "metric_name": metric_name,
            "snapshot_time": metric_value["snapshot_time"],
            "snapshot_value": metric_value["snapshot_value"],
            "source": source or SOURCE_HISTORY,
        })
        entry = db.fetchone("SELECT * FROM anomaly_analyses WHERE id=?", [analysis_id])
        return entry, f"已按指标采样 {metric_value['id']} 登记归因记录"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """支持带证据登记；无证据时退回通用登记行为。"""
        alert_log_id = values.get("alert_log_id")
        if alert_log_id:
            entry, message = self.create_from_evidence(
                alert_log_id=int(alert_log_id),
                duty_group=str(values.get("归属值班组") or values.get("duty_group") or ""),
                handler=str(values.get("handler") or values.get("办理人") or ""),
                source=str(values.get("source") or SOURCE_HISTORY),
                dimensions=str(values.get("归因维度") or ""),
                remark=values.get("remark"),
            )
            return entry, [] if entry else [message]

        entry, missing = super().create_entry(values)
        if entry is not None:
            db.update("anomaly_analyses", {
                "metric_name": values.get("分析对象", ""),
                "handler": values.get("handler", ""),
                "duty_group": values.get("归属值班组", ""),
                "evidence_source": values.get("source", SOURCE_DIRECT),
            }, "id=?", [entry["id"]])
            entry = db.fetchone("SELECT * FROM anomaly_analyses WHERE id=?", [entry["id"]])
        return entry, missing

    # ================================================================
    # 办理：认领相同证据 / 按自己的规则归因
    # ================================================================
    def claim(
        self,
        analysis_id: int,
        *,
        handler: str,
        duty_group: str = "",
        source: str = SOURCE_CLAIM,
        alert_log_id: int | None = None,
        rule_id: int | None = None,
    ) -> dict[str, Any]:
        """认领已有证据：复用源记录绑定的 metric_value_id，另存一条本组归因记录。

        源记录（例如渠道值班已办理的那条）的办理人和来源保持原样，不被覆盖；
        认领产生的新记录保存本次办理人、本组规则和本次使用来源。
        """
        source_entry = db.fetchone(
            "SELECT * FROM anomaly_analyses WHERE id=?", [analysis_id])
        if source_entry is None:
            return {"ok": False, "message": f"归因记录 {analysis_id} 不存在"}
        source_entry = dict(source_entry)
        if not source_entry.get("metric_value_id"):
            return {"ok": False, "message": "该归因记录未绑定指标采样，不能按证据认领"}
        if not handler.strip():
            return {"ok": False, "message": "认领需要填写办理人"}

        metric_value = db.fetchone(
            "SELECT * FROM metric_values WHERE id=?", [source_entry["metric_value_id"]])
        if metric_value is None:
            return {"ok": False, "message": "源归因记录引用的指标采样已不存在"}

        log = None
        if alert_log_id:
            log = db.fetchone("SELECT * FROM alert_log WHERE id=?", [alert_log_id])
            if log is None:
                return {"ok": False, "message": f"告警历史 {alert_log_id} 不存在"}
            if log.get("metric_value_id") != source_entry["metric_value_id"]:
                return {
                    "ok": False,
                    "message": "认领的告警证据与源归因记录使用的指标采样不一致",
                }

        group = (duty_group or source_entry.get("duty_group") or "").strip()
        claim_rule = rule_id or (log.get("rule_id") if log else None) or source_entry.get("rule_id")

        # 同一值班组对同一指标采样只保留一条认领记录，重复点击幂等返回。
        existing = db.fetchone(
            "SELECT * FROM anomaly_analyses WHERE metric_value_id=? AND duty_group=? "
            "AND claimed_from_id=? ORDER BY id DESC LIMIT 1",
            [source_entry["metric_value_id"], group, analysis_id],
        )
        if existing is not None:
            return {
                "ok": True,
                "message": f"本组已认领过该证据（归因记录 {existing['id']}）",
                "entry": self._analysis_view(existing),
                "evidence_used": source_entry["metric_value_id"],
                "source": existing.get("evidence_source", source),
                "claimed_from_id": analysis_id,
                "reused": True,
            }

        new_id = db.insert("anomaly_analyses", {
            "target_name": source_entry.get("target_name", ""),
            "anomaly_time": source_entry.get("anomaly_time", ""),
            "attribution_dimensions": source_entry.get("attribution_dimensions", "[]"),
            "status": self.STATUS_ORDER[0],
            "metric_id": source_entry.get("metric_id"),
            "metric_name": source_entry.get("metric_name", ""),
            "metric_value_id": source_entry["metric_value_id"],
            "snapshot_time": source_entry.get("snapshot_time", ""),
            "snapshot_value": source_entry.get("snapshot_value"),
            "rule_id": claim_rule,
            "rule_name": (log.get("rule_name") if log else "") or source_entry.get("rule_name", ""),
            "duty_group": group,
            "evidence_source": source or SOURCE_CLAIM,
            "handler": handler,
            "claimed_from_id": analysis_id,
        })
        # 新记录同时绑定本组告警证据；源记录的证据绑定不动。
        if log is not None:
            db.insert("anomaly_evidence", {
                "analysis_id": new_id,
                "alert_log_id": log["id"],
                "rule_id": claim_rule,
                "rule_name": log.get("rule_name", "") or source_entry.get("rule_name", ""),
                "duty_group": log.get("duty_group", ""),
                "metric_value_id": source_entry["metric_value_id"],
                "metric_id": source_entry.get("metric_id"),
                "metric_name": source_entry.get("metric_name", ""),
                "snapshot_time": source_entry.get("snapshot_time", ""),
                "snapshot_value": source_entry.get("snapshot_value"),
                "source": source or SOURCE_CLAIM,
            })
        updated = db.fetchone("SELECT * FROM anomaly_analyses WHERE id=?", [new_id])
        return {
            "ok": True,
            "message": f"已认领同一证据（指标采样 {source_entry['metric_value_id']}），另存本组归因记录 {new_id}",
            "entry": self._analysis_view(updated) if updated else None,
            "evidence_used": source_entry["metric_value_id"],
            "source": source or SOURCE_CLAIM,
            "claimed_from_id": analysis_id,
        }

    def claim_from_evidence(
        self,
        *,
        alert_log_id: int,
        analysis_id: int,
        handler: str,
        duty_group: str = "",
        source: str = SOURCE_CLAIM,
        rule_id: int | None = None,
    ) -> dict[str, Any]:
        """按证据认领并显式校验证据一致，避免认领到别的采样。"""
        analysis = db.fetchone("SELECT * FROM anomaly_analyses WHERE id=?", [analysis_id])
        if analysis is None:
            return {"ok": False, "message": f"归因记录 {analysis_id} 不存在"}
        log = db.fetchone("SELECT * FROM alert_log WHERE id=?", [alert_log_id])
        if log is None:
            return {"ok": False, "message": f"告警历史 {alert_log_id} 不存在"}
        if analysis["metric_value_id"] != log["metric_value_id"]:
            return {"ok": False, "message": "归因记录使用的指标采样与告警证据不一致，不能认领"}
        result = self.claim(analysis_id, handler=handler, duty_group=duty_group,
                            source=source, alert_log_id=alert_log_id, rule_id=rule_id)
        if result.get("ok"):
            result["alert_log_id"] = alert_log_id
        return result

    def run_action(
        self,
        entry_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """启动归因时按记录自身绑定的 metric_value_id 取证据，绝不取最新值。"""
        if action == "启动分析":
            result = self.run_analysis(entry_id)
            if not result.get("ok"):
                return None, result.get("message", "启动分析失败")
            entry = db.fetchone("SELECT * FROM anomaly_analyses WHERE id=?", [entry_id])
            return entry, result.get("message", "归因分析已完成")
        return super().run_action(entry_id, action, payload)

    # ================================================================
    # 分析执行
    # ================================================================
    def run_analysis(self, entry_id: int) -> dict[str, Any]:
        """执行异常归因分析。先加载记录，再调用管线。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return {"ok": False, "message": f"分析记录 {entry_id} 不存在"}

        # 标记为分析中
        db.update("anomaly_analyses", {"status": "分析中"}, "id=?", [entry_id])

        analysis_data = {
            "分析对象": entry.get("target_name") or entry.get("分析对象"),
            "异常时间点": entry.get("anomaly_time") or entry.get("异常时间点"),
            "归因维度": entry.get("attribution_dimensions") or entry.get("归因维度"),
            # 有证据时分析固定用这条采样，不被同名指标的其他样本替换。
            "metric_value_id": entry.get("metric_value_id"),
            "metric_id": entry.get("metric_id"),
        }
        result = detect_anomaly(analysis_data)

        # 回写结论
        conclusion = result.get("conclusion", "分析完成")
        contributions = result.get("contributions", {})
        ranking_json = json.dumps(
            sorted(contributions.items(), key=lambda x: -x[1])[:5],
            ensure_ascii=False,
        )

        db.update("anomaly_analyses", {
            "status": "已完成" if result.get("status") != "分析失败" else "分析失败",
            "contribution_ranking": ranking_json,
            "conclusion": conclusion,
        }, "id=?", [entry_id])

        return {
            "ok": True,
            "message": "归因分析已完成",
            "target": analysis_data["分析对象"],
            "metric_value_id": entry.get("metric_value_id"),
            "is_anomaly": result.get("is_anomaly", False),
            "z_score": result.get("z_score", 0),
            "conclusion": conclusion,
            "contributions": contributions,
        }

    def recent_anomalies(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的分析结果。"""
        return db.fetchall(
            "SELECT * FROM anomaly_analyses WHERE status='已完成' ORDER BY id DESC LIMIT ?",
            [limit],
        )

    def evidence_for(self, analysis_id: int) -> list[dict[str, Any]]:
        return db.fetchall(
            "SELECT * FROM anomaly_evidence WHERE analysis_id=? ORDER BY id", [analysis_id])

    def _analysis_view(self, row: dict[str, Any]) -> dict[str, Any]:
        view = dict(row)
        for field, column in (
            ("分析对象", "target_name"),
            ("异常时间点", "anomaly_time"),
            ("归因维度", "attribution_dimensions"),
            ("贡献度排序", "contribution_ranking"),
            ("分析结论", "conclusion"),
            ("归属值班组", "duty_group"),
            ("触发规则", "rule_id"),
            ("规则名称", "rule_name"),
            ("指标采样", "metric_value_id"),
            ("办理人", "handler"),
            ("来源", "evidence_source"),
        ):
            view.setdefault(field, view.get(column, ""))
        view["pending"] = view.get("status") not in ("已完成", "分析失败")
        view["abnormal"] = view.get("status") == "分析失败"
        try:
            view["evidence_count"] = db.count(
                "anomaly_evidence", "analysis_id=?", [row["id"]])
        except Exception:
            view["evidence_count"] = 0
        return view

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_analyses"] = db.count("anomaly_analyses")
        snapshot["completed_analyses"] = db.count("anomaly_analyses", "status='已完成'")
        snapshot["evidence_links"] = db.count("anomaly_evidence")
        return snapshot


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _column(field: str) -> str:
    from app.services.base import _to_column_name
    return _to_column_name(field)


def _as_int(value: Any) -> int:
    return int(str(value).strip())


_CHINESE_FILTER_ALIASES = {
    "触发规则": "rule_id",
    "规则ID": "rule_id",
    "rule_id": "rule_id",
    "归属值班组": "duty_group",
    "duty_group": "duty_group",
    "指标采样": "metric_value_id",
    "metric_value_id": "metric_value_id",
    "告警历史": "alert_log_id",
    "alert_log_id": "alert_log_id",
    "监控指标": "metric_id",
    "metric_id": "metric_id",
}


def _normalize_extra(extra: dict[str, Any] | None) -> dict[str, Any]:
    """把中文/英文过滤参数统一成内部键，避免调用方各写一套别名。"""
    normalized: dict[str, Any] = {}
    for key, value in (extra or {}).items():
        normalized[_CHINESE_FILTER_ALIASES.get(key, key)] = value
    return normalized


def _coerce(value: Any) -> Any:
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def _rule_view(rule: dict[str, Any]) -> dict[str, Any]:
    view = dict(rule)
    view.setdefault("规则名称", view.get("rule_name", ""))
    view.setdefault("监控指标", view.get("monitor_metric", ""))
    view.setdefault("归属值班组", view.get("duty_group", ""))
    return view

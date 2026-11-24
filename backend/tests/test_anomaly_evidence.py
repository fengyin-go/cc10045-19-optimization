"""异常归因证据筛选的回归测试。

直接调用领域服务，使用临时 SQLite 目录，不依赖 HTTP 服务。

覆盖：
1. 同名指标/同一分钟两个采样不串位（按 metric_value_id 绑定）；
2. 翻页/重新筛选时后插入的样本不挤掉当时可办理的记录（anchor_id）；
3. 两条规则指向同一采样，一条已由渠道值班归因，收入值班能检索到；
4. 认领相同证据另存本组记录，渠道原记录的办理人与来源不被抹掉；
5. 详情/分析始终使用记录实际取用的指标采样。

运行：``PYTHONPATH=backend backend/.venv/bin/python backend/tests/test_anomaly_evidence.py``
"""
from __future__ import annotations

import os
import tempfile

os.environ["CC10045_DB_DIR"] = tempfile.mkdtemp(prefix="anomaly-test-")

from app.database import db  # noqa: E402
from app.pipeline import seed_real_data  # noqa: E402
from app.services.alert_policy import AlertPolicyService  # noqa: E402
from app.services.anomaly_analysis import AnomalyAnalysisService  # noqa: E402

RESET_TABLES = ("alerts", "alert_log", "metric_values", "anomaly_analyses",
                "anomaly_evidence", "metrics")


class Case:
    """一次独立场景：两条规则指向同一指标采样 17，另有同分钟的采样 18。"""

    __test__ = False

    def __init__(self) -> None:
        for table in RESET_TABLES:
            db.delete(table, "1=1")
        seed_real_data()
        self.alerts = AlertPolicyService()
        self.anomaly = AnomalyAnalysisService()

        self.metric_id = db.insert("metrics", {
            "metric_name": "渠道支付额", "metric_type": "counter",
            "source_module": "event_tracker",
        })
        for i in range(16):
            db.insert("metric_values", {
                "metric_id": self.metric_id, "snapshot_value": 100 + i,
                "snapshot_time": f"2026-11-24T10:{i:02d}:00",
                "comparison_status": "已保存",
            })
        self.sample17 = db.insert("metric_values", {
            "metric_id": self.metric_id, "snapshot_value": 999.0,
            "snapshot_time": "2026-11-24T11:00:00",
        })
        self.sample18 = db.insert("metric_values", {
            "metric_id": self.metric_id, "snapshot_value": 555.0,
            "snapshot_time": "2026-11-24T11:00:00",
        })
        assert self.sample17 != self.sample18

        self.rule_channel = db.insert("alerts", {
            "rule_name": "渠道支付额骤降", "monitor_metric": "渠道支付额",
            "metric_id": self.metric_id, "duty_group": "渠道值班",
            "alert_condition": "> 0", "status": "已生效",
        })
        self.rule_revenue = db.insert("alerts", {
            "rule_name": "收入支付额骤降", "monitor_metric": "渠道支付额",
            "metric_id": self.metric_id, "duty_group": "收入值班",
            "alert_condition": "> 0", "status": "已生效",
        })
        self.log_channel = self.alerts.trigger(
            self.rule_channel, self.sample17)["entry"]["id"]
        self.log_revenue = self.alerts.trigger(
            self.rule_revenue, self.sample17)["entry"]["id"]

    def channel_analysis(self) -> dict:
        entry, message = self.anomaly.create_from_evidence(
            alert_log_id=self.log_channel, duty_group="渠道值班",
            handler="渠道-张三", source="告警历史",
        )
        assert entry is not None, message
        return entry


def test_evidence_is_bound_to_exact_sample_not_same_minute():
    case = Case()
    entry = case.channel_analysis()
    assert entry["metric_value_id"] == case.sample17
    assert entry["snapshot_value"] == 999.0

    # 同分钟、编号更大的样本后到，不能替换已登记证据
    case.alerts.trigger(case.rule_revenue, case.sample18)
    detail = case.anomaly.get_entry_view(entry["id"])
    assert detail["metric_value_id"] == case.sample17
    assert detail["snapshot_value"] == 999.0

    # 按采样 id 过滤时 18 不出现
    items_17, total_17, _ = case.anomaly.list_entries_page(
        extra={"metric_value_id": case.sample17})
    assert total_17 == 1 and items_17[0]["metric_value_id"] == case.sample17
    items_18, total_18, _ = case.anomaly.list_entries_page(
        extra={"metric_value_id": case.sample18})
    assert total_18 == 0 and items_18 == []


def test_anchor_keeps_page_stable_when_new_sample_inserted():
    case = Case()
    case.channel_analysis()

    first, total, anchor = case.anomaly.list_entries_page(
        extra={"metric_value_id": case.sample17}, page=1, size=1)
    assert total == 1 and first[0]["id"] == anchor

    # 翻页前插入更新的记录（属于另一条触发），全表顺序变了
    case.anomaly.create_from_evidence(
        alert_log_id=case.log_revenue, duty_group="收入值班", handler="收入-王五")

    page2, total2, anchor2 = case.anomaly.list_entries_page(
        extra={"metric_value_id": case.sample17}, page=1, size=1, anchor_id=anchor)
    assert anchor2 == anchor
    assert page2[0]["id"] == first[0]["id"]
    assert total2 == total

    # 不带锚点时会看到新记录，证明插入确实发生了
    fresh, fresh_total, _ = case.anomaly.list_entries_page(
        extra={"metric_value_id": case.sample17}, page=1, size=1)
    assert fresh_total == 2 and fresh[0]["id"] != first[0]["id"]


def test_alert_history_pages_by_rule_with_anchor():
    case = Case()
    case.alerts.trigger(case.rule_revenue, case.sample18)

    items, total, anchor = case.alerts.list_history(
        rule_id=case.rule_revenue, page=1, size=1)
    assert total == 2 and items[0]["metric_value_id"] == case.sample18

    # 后来再插入一条该规则的触发，不影响已锚定的第二页
    case.alerts.trigger(case.rule_revenue, case.sample17)
    page2, total2, anchor2 = case.alerts.list_history(
        rule_id=case.rule_revenue, page=2, size=1, anchor_id=anchor)
    assert anchor2 == anchor and total2 == total
    assert page2[0]["metric_value_id"] == case.sample17


def test_revenue_can_find_and_claim_same_evidence():
    case = Case()
    channel = case.channel_analysis()

    history = case.anomaly.by_rule_history(
        case.rule_revenue, duty_group="收入值班")
    assert history["ok"]
    row = next(h for h in history["history"]
               if h["metric_value_id"] == case.sample17)
    assert row["can_claim"] is True
    assert row["needs_own_rule"] is False
    assert row["existing_analysis"]["id"] == channel["id"]

    workspace = case.anomaly.evidence_workspace(
        alert_log_id=case.log_revenue, duty_group="收入值班")
    assert workspace["evidence"]["metric_value_id"] == case.sample17
    assert workspace["claimed_by_others"] is True
    assert workspace["can_claim"] is True
    assert workspace["needs_own_rule"] is False

    result = case.anomaly.claim_from_evidence(
        alert_log_id=case.log_revenue, analysis_id=channel["id"],
        handler="收入-李四", duty_group="收入值班", source="认领证据",
        rule_id=case.rule_revenue,
    )
    assert result["ok"], result
    assert result["evidence_used"] == case.sample17
    claimed = result["entry"]
    assert claimed["id"] != channel["id"]
    assert claimed["handler"] == "收入-李四"
    assert claimed["evidence_source"] == "认领证据"
    assert claimed["rule_id"] == case.rule_revenue
    assert claimed["claimed_from_id"] == channel["id"]


def test_channel_record_not_overwritten_by_revenue_claim():
    case = Case()
    channel = case.channel_analysis()
    case.anomaly.claim_from_evidence(
        alert_log_id=case.log_revenue, analysis_id=channel["id"],
        handler="收入-李四", duty_group="收入值班", source="认领证据",
        rule_id=case.rule_revenue,
    )
    original = db.fetchone(
        "SELECT * FROM anomaly_analyses WHERE id=?", [channel["id"]])
    assert original["handler"] == "渠道-张三"
    assert original["evidence_source"] == "告警历史"
    assert original["duty_group"] == "渠道值班"
    assert original["metric_value_id"] == case.sample17


def test_own_rule_analysis_uses_bound_sample():
    case = Case()
    entry = case.channel_analysis()
    # 让采样 18 成为该指标"最新值"；按证据分析仍必须用 17
    case.alerts.trigger(case.rule_revenue, case.sample18)

    result = case.anomaly.run_analysis(entry["id"])
    assert result["ok"]
    updated = case.anomaly.get_entry_view(entry["id"])
    assert updated["metric_value_id"] == case.sample17
    assert updated["snapshot_value"] == 999.0


if __name__ == "__main__":
    test_evidence_is_bound_to_exact_sample_not_same_minute()
    test_anchor_keeps_page_stable_when_new_sample_inserted()
    test_alert_history_pages_by_rule_with_anchor()
    test_revenue_can_find_and_claim_same_evidence()
    test_channel_record_not_overwritten_by_revenue_claim()
    test_own_rule_analysis_uses_bound_sample()
    print("all anomaly evidence tests passed")

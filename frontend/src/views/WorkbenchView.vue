<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <!-- 异常归因：按告警证据筛选 -->
    <form v-if="isAnomaly" class="filter-bar evidence-bar" data-testid="evidence-filters" @submit.prevent="submitEvidenceFilters">
      <label class="filter-item">
        <span>触发规则</span>
        <select v-model="evidenceRuleId" data-testid="evidence-rule">
          <option value="">全部规则</option>
          <option v-for="rule in rules" :key="rule.id" :value="String(rule.id)">
            #{{ rule.id }} {{ rule.rule_name }}（{{ rule.duty_group }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>归属值班组</span>
        <input v-model="dutyGroup" data-testid="evidence-duty-group" placeholder="如：收入值班" />
      </label>
      <label class="filter-item">
        <span>指标采样编号</span>
        <input v-model="evidenceMetricValueId" data-testid="evidence-metric-value" placeholder="metric_value_id" />
      </label>
      <button class="btn primary" data-testid="evidence-query" type="submit">按证据筛选</button>
      <button class="btn ghost" data-testid="evidence-reset" type="button" @click="resetEvidenceFilter">清除证据</button>
    </form>

    <form class="filter-bar" @submit.prevent="submitFilters">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 告警历史：按触发规则查询，翻页时保持锚点，后插入的样本不挤掉当时可办理的记录 -->
    <section v-if="isAnomaly && alertHistory.length" class="evidence-panel" data-testid="alert-history">
      <header class="evidence-head">
        <div>
          <h3>告警历史（触发证据）</h3>
          <p class="page-desc">每条历史都带触发时的指标采样、规则与归属值班组，点击定位归因记录。</p>
        </div>
        <span class="page-desc" data-testid="history-page-info">第 {{ historyPage }} 页 · 锚点 {{ historyAnchor ?? '—' }}</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>告警历史</th><th>触发规则</th><th>指标采样</th><th>采样时间</th><th>取值</th><th>归属值班组</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="item in alertHistory"
            :key="item.id"
            :data-testid="`history-${item.id}`"
            :class="{ selected: pendingAlertLogId === item.id }"
          >
            <td>#{{ item.id }}</td>
            <td>#{{ item.rule_id }} {{ item.rule_name }}</td>
            <td data-testid="history-metric-value">{{ item.metric_value_id }}</td>
            <td>{{ item.snapshot_time }}</td>
            <td>{{ item.metric_value }}</td>
            <td>{{ item.duty_group }}</td>
            <td>
              <button class="link" type="button" :data-testid="`open-history-${item.id}`" @click="openWorkspace(item.id)">
                查看归因
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <button class="btn" data-testid="history-prev" type="button" :disabled="historyPage <= 1" @click="goHistoryPage(historyPage - 1)">
          上一页
        </button>
        <button
          class="btn"
          data-testid="history-next"
          type="button"
          :disabled="historyPage * historyPageSize >= historyTotal"
          @click="goHistoryPage(historyPage + 1)"
        >
          下一页
        </button>
      </div>
    </section>

    <!-- 异常归因工作台：证据 → 是否已有分析 → 认领或自建 -->
    <section v-if="isAnomaly && workspace" class="evidence-panel" data-testid="evidence-panel">
      <header class="evidence-head">
        <div>
          <h3>归因证据：指标采样 {{ workspace.evidence.metric_value_id }}</h3>
          <p class="page-desc">
            {{ workspace.evidence.metric_name }} ·
            {{ workspace.evidence.snapshot_time }} · 取值 {{ workspace.evidence.snapshot_value }}
          </p>
          <p class="page-desc">
            触发规则 #{{ workspace.evidence.rule_id }} {{ workspace.evidence.rule_name }} ·
            归属 {{ workspace.evidence.duty_group }}
          </p>
        </div>
        <div class="evidence-verdict" :data-can-claim="workspace.can_claim">
          <strong>{{ workspace.next_step }}</strong>
          <span v-if="workspace.already_own" data-testid="already-own">本组已归因该证据</span>
          <span v-else-if="workspace.can_claim" data-testid="can-claim">
            可认领相同证据
            <template v-if="workspace.claimed_by_others">
              （已被 {{ workspace.other_handlers.join('、') }} 办理，不会覆盖原记录）
            </template>
          </span>
          <span v-else-if="workspace.claimed_by_others" data-testid="claimed-by-others">
            已被其它值班组办理：{{ workspace.other_handlers.join('、') }}
          </span>
          <span v-else data-testid="needs-own-rule">需要自己的规则归因</span>
        </div>
      </header>

      <div class="evidence-actions">
        <label class="filter-item">
          <span>办理人</span>
          <input v-model="handler" data-testid="claim-handler" placeholder="本次办理人" />
        </label>
        <label class="filter-item">
          <span>本组规则</span>
          <select v-model="claimRuleId" data-testid="claim-rule">
            <option value="">使用证据所属规则</option>
            <option v-for="rule in rulesForGroup" :key="rule.id" :value="String(rule.id)">
              #{{ rule.id }} {{ rule.rule_name }}
            </option>
          </select>
        </label>
        <button
          v-if="workspace.can_claim && workspace.existing_analysis"
          class="btn primary"
          data-testid="claim-evidence"
          type="button"
          @click="claimExisting"
        >
          认领相同证据
        </button>
        <button
          v-else
          class="btn primary"
          data-testid="create-own"
          type="button"
          @click="createOwn"
        >
          按本组规则发起归因
        </button>
      </div>

      <table class="data-table" data-testid="evidence-table">
        <thead>
          <tr>
            <th>归因记录</th><th>指标采样</th><th>采样时间</th><th>规则</th>
            <th>值班组</th><th>办理人</th><th>来源</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in workspace.items" :key="String(item.id)" :data-testid="`analysis-${item.id}`">
            <td>#{{ item.id }}</td>
            <td>{{ item.metric_value_id }}</td>
            <td>{{ item.snapshot_time }}</td>
            <td>#{{ item.rule_id }} {{ item.rule_name }}</td>
            <td>{{ item.duty_group || '—' }}</td>
            <td>{{ item.handler || '—' }}</td>
            <td>{{ item.evidence_source || '—' }}</td>
            <td>{{ item.status }}</td>
          </tr>
          <tr v-if="!workspace.items.length">
            <td colspan="8" class="empty-state">该指标采样还没有归因记录</td>
          </tr>
        </tbody>
      </table>
      <p v-if="workspace.evidence" class="page-desc" data-testid="evidence-used">
        本次实际取用证据：指标采样 {{ workspace.evidence.metric_value_id }}（{{ workspace.evidence.snapshot_time }}，取值 {{ workspace.evidence.snapshot_value }}）
      </p>
    </section>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in tableColumns" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :data-metric-value-id="row.metric_value_id ?? ''"
        >
          <td v-for="column in tableColumns" :key="column">{{ displayCell(row, column) }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="isAnomaly && row.metric_value_id"
              class="link"
              type="button"
              :data-testid="`detail-${row.id}`"
              @click="openDetail(row)"
            >
              查看证据
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="tableColumns.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span class="pager">
        <button class="btn" data-testid="prev-page" type="button" :disabled="page <= 1" @click="goPage(page - 1)">
          上一页
        </button>
        <span data-testid="page-info">第 {{ page }} 页 · 锚点 {{ anchorId ?? '—' }}</span>
        <button
          class="btn"
          data-testid="next-page"
          type="button"
          :disabled="page * pageSize >= total"
          @click="goPage(page + 1)"
        >
          下一页
        </button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="noticeMessage" class="notice-text" data-testid="notice">{{ noticeMessage }}</span>
    </footer>

    <div v-if="detail" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal">
        <h3>归因记录 #{{ detail.id }} 的证据</h3>
        <dl class="dag-detail">
          <dt>指标采样</dt><dd data-testid="detail-metric-value">{{ detail.metric_value_id }}</dd>
          <dt>指标名称</dt><dd>{{ detail.metric_name }}</dd>
          <dt>采样时间</dt><dd>{{ detail.snapshot_time }}</dd>
          <dt>采样取值</dt><dd data-testid="detail-snapshot-value">{{ detail.snapshot_value }}</dd>
          <dt>触发规则</dt><dd>#{{ detail.rule_id }} {{ detail.rule_name }}</dd>
          <dt>归属值班组</dt><dd>{{ detail.duty_group }}</dd>
          <dt>办理人</dt><dd>{{ detail.handler || '—' }}</dd>
          <dt>来源</dt><dd>{{ detail.evidence_source || '—' }}</dd>
        </dl>
        <div class="page-actions">
          <button class="btn" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimEvidence,
  createAttributionFromEvidence,
  createEntry,
  downloadEntries,
  getEntry,
  listAlertHistory,
  listEntries,
  loadEvidenceWorkspace,
  listRuleHistory,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type {
  AlertHistoryItem, AnalysisRow, AnomalyWorkspace, EntryRow, RuleHistoryResult,
} from '@/data/types'

type RuleOption = { id: number; rule_name: string; duty_group: string; monitor_metric: string }

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const isAnomaly = meta.key === 'anomaly_analysis'
const rows = ref<EntryRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(isAnomaly ? 5 : 20)
// 首屏查询的 id 上界：翻页时原样回传，后插入的样本不会挤掉当时可办理的记录
const anchorId = ref<number | null>(null)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const detail = ref<AnalysisRow | null>(null)

// 异常归因证据筛选
const evidenceRuleId = ref('')
const evidenceMetricValueId = ref('')
const dutyGroup = ref(isAnomaly ? '收入值班' : '')
const rules = ref<RuleOption[]>([])
const workspace = ref<AnomalyWorkspace | null>(null)
const ruleHistory = ref<RuleHistoryResult | null>(null)
const handler = ref('')
const claimRuleId = ref('')
const pendingAlertLogId = ref<number | null>(null)
// 告警历史列表（证据池）
const alertHistory = ref<AlertHistoryItem[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyPageSize = ref(5)
const historyAnchor = ref<number | null>(null)

// 归因列表额外展示证据身份，避免同名指标/同分钟样本串位
const evidenceColumns = ['指标采样', '归属值班组', '办理人', '来源']
const tableColumns = computed(() =>
  isAnomaly ? [...new Set([...meta.fields, ...evidenceColumns])] : meta.fields,
)

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)
const rulesForGroup = computed(() => {
  const group = (dutyGroup.value || workspace.value?.current_duty_group || '').trim()
  if (!group) return rules.value
  const matched = rules.value.filter((rule) => rule.duty_group === group)
  return matched.length ? matched : rules.value
})

function displayCell(row: EntryRow, column: string): string | number {
  const value = row[column]
  if (value === null || value === undefined || value === '') return '—'
  return value as string | number
}

function evidenceExtra(): Record<string, string | number> {
  const extra: Record<string, string | number> = {}
  if (!isAnomaly) return extra
  if (evidenceRuleId.value) extra.rule_id = evidenceRuleId.value
  if (dutyGroup.value.trim()) extra.duty_group = dutyGroup.value.trim()
  if (evidenceMetricValueId.value.trim()) extra.metric_value_id = evidenceMetricValueId.value.trim()
  return extra
}

function resetFilters() {
  filters.value = {}
  page.value = 1
  anchorId.value = null
  void reload()
}

function resetEvidenceFilter() {
  evidenceRuleId.value = ''
  evidenceMetricValueId.value = ''
  workspace.value = null
  pendingAlertLogId.value = null
  alertHistory.value = []
  historyTotal.value = 0
  historyPage.value = 1
  historyAnchor.value = null
  page.value = 1
  anchorId.value = null
  void reload()
}

async function applyEvidenceFilter() {
  errorMessage.value = ''
  noticeMessage.value = ''
  page.value = 1
  anchorId.value = null
  historyPage.value = 1
  historyAnchor.value = null
  if (evidenceRuleId.value) {
    await loadAlertHistory(true)
    await openRuleHistory(Number(evidenceRuleId.value))
  } else {
    alertHistory.value = []
    historyTotal.value = 0
  }
  await reload()
}

async function loadAlertHistory(resetAnchor = false) {
  if (!isAnomaly || !evidenceRuleId.value) return
  try {
    const payload = await listAlertHistory({
      ruleId: Number(evidenceRuleId.value),
      dutyGroup: dutyGroup.value.trim(),
      page: historyPage.value,
      size: historyPageSize.value,
      anchorId: resetAnchor ? null : historyAnchor.value,
    })
    alertHistory.value = payload.items
    historyTotal.value = payload.total
    historyAnchor.value = payload.anchor_id ?? historyAnchor.value
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警历史读取失败'
  }
}

async function goHistoryPage(next: number) {
  if (next < 1) return
  historyPage.value = next
  // 翻页时沿用首次查询的锚点，后插入的样本不会挤掉当时可办理的记录
  await loadAlertHistory(false)
}

async function openRuleHistory(ruleId: number) {
  try {
    const result = await listRuleHistory(ruleId, dutyGroup.value.trim())
    ruleHistory.value = result
    if (!result.ok) throw new Error(result.message || '规则历史读取失败')
    // 默认选中已有人办理、且当前组尚未归因的那条证据
    const candidate = result.history.find((item) => item.can_claim && !item.has_own_analysis)
      ?? result.history[0]
    if (candidate) {
      await openWorkspace(candidate.alert_log_id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则历史读取失败'
  }
}

async function openWorkspace(alertLogId: number) {
  pendingAlertLogId.value = alertLogId
  workspace.value = await loadEvidenceWorkspace({
    alertLogId,
    dutyGroup: dutyGroup.value.trim(),
  })
  claimRuleId.value = evidenceRuleId.value
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
    if (workspace.value && pendingAlertLogId.value) {
      await openWorkspace(pendingAlertLogId.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function claimExisting() {
  if (!workspace.value?.existing_analysis || !pendingAlertLogId.value) return
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await claimEvidence({
      analysisId: workspace.value.existing_analysis.id,
      alertLogId: pendingAlertLogId.value,
      ruleId: claimRuleId.value ? Number(claimRuleId.value) : null,
      dutyGroup: dutyGroup.value.trim(),
      handler: handler.value.trim() || '当前值班员',
      source: '认领证据',
    })
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await openWorkspace(pendingAlertLogId.value)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '认领失败'
  }
}

async function createOwn() {
  if (!pendingAlertLogId.value) return
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const result = await createAttributionFromEvidence({
      alertLogId: pendingAlertLogId.value,
      dutyGroup: dutyGroup.value.trim(),
      handler: handler.value.trim() || '当前值班员',
      source: '告警历史',
    })
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    await openWorkspace(pendingAlertLogId.value)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记归因失败'
  }
}

async function openDetail(row: EntryRow) {
  errorMessage.value = ''
  try {
    const entry = await getEntry(meta.key, Number(row.id))
    detail.value = entry as AnalysisRow
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '详情读取失败'
  }
}

async function goPage(next: number) {
  if (next < 1) return
  page.value = next
  await reload(false)
}

async function reload(resetAnchor = true) {
  errorMessage.value = ''
  if (resetAnchor) {
    page.value = 1
    anchorId.value = null
  }
  try {
    const payload = await listEntries(meta.key, {
      page: page.value,
      size: pageSize.value,
      filters: filters.value,
      extra: evidenceExtra(),
      anchorId: anchorId.value,
    })
    rows.value = payload.items
    total.value = payload.total
    anchorId.value = payload.anchor_id ?? anchorId.value
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

function submitFilters() {
  void reload()
}

function submitEvidenceFilters() {
  void applyEvidenceFilter()
}

async function loadRules() {
  if (!isAnomaly) return
  try {
    const payload = await listEntries('alert_policy', { size: 100 })
    rules.value = payload.items.map((item) => ({
      id: Number(item.id),
      rule_name: String(item.rule_name ?? item['规则名称'] ?? ''),
      duty_group: String(item.duty_group ?? item['归属值班组'] ?? ''),
      monitor_metric: String(item.monitor_metric ?? item['监控指标'] ?? ''),
    }))
  } catch {
    rules.value = []
  }
}

onMounted(async () => {
  await loadRules()
  await reload()
})
</script>

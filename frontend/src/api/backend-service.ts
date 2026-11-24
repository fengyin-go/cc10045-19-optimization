import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult, AlertHistoryItem, AnomalyWorkspace, EntryRow,
  OverviewResult, PageResult, RuleHistoryResult,
} from '@/data/types'

export { moduleMeta }

export type ListParams = {
  page?: number
  size?: number
  anchorId?: number | null
  filters?: Record<string, string>
  extra?: Record<string, string | number>
}

export async function listEntries(
  key: string,
  params: ListParams = {},
): Promise<PageResult> {
  const query = new URLSearchParams()
  for (const [field, value] of Object.entries(params.filters ?? {})) {
    if (String(value ?? '').trim()) query.set(field, String(value))
  }
  for (const [field, value] of Object.entries(params.extra ?? {})) {
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      query.set(field, String(value))
    }
  }
  if (params.page) query.set('page', String(params.page))
  if (params.size) query.set('size', String(params.size))
  // 翻页时回传首屏锚点，后插入的记录不会挤动已看过的页
  if (params.anchorId !== null && params.anchorId !== undefined) {
    query.set('anchor_id', String(params.anchorId))
  }
  const response = await request(`/api/${key}?${query.toString()}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
  values: Record<string, string> = {},
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action, values }),
  })
  return await response.json()
}

export async function getEntry(key: string, id: number): Promise<EntryRow> {
  const response = await request(`/api/${key}/${id}`)
  if (!response.ok) throw new Error('记录详情读取失败')
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ----------------------------------------------------------------------
// 告警历史（触发证据）
// ----------------------------------------------------------------------
export async function listAlertHistory(params: {
  ruleId?: number | null
  dutyGroup?: string
  metricValueId?: number | null
  page?: number
  size?: number
  anchorId?: number | null
}): Promise<PageResult<AlertHistoryItem>> {
  const query = new URLSearchParams()
  if (params.ruleId) query.set('rule_id', String(params.ruleId))
  if (params.dutyGroup) query.set('duty_group', params.dutyGroup)
  if (params.metricValueId) query.set('metric_value_id', String(params.metricValueId))
  query.set('page', String(params.page ?? 1))
  query.set('size', String(params.size ?? 20))
  if (params.anchorId !== null && params.anchorId !== undefined) {
    query.set('anchor_id', String(params.anchorId))
  }
  const response = await request(`/api/alert_policy/history?${query.toString()}`)
  if (!response.ok) throw new Error('告警历史读取失败')
  return await response.json()
}

export async function listRuleHistory(
  ruleId: number,
  dutyGroup: string,
): Promise<RuleHistoryResult> {
  const query = new URLSearchParams({ duty_group: dutyGroup })
  const response = await request(`/api/anomaly_analysis/rule/${ruleId}/history?${query.toString()}`)
  if (!response.ok) throw new Error('规则历史读取失败')
  return await response.json()
}

export async function loadEvidenceWorkspace(params: {
  alertLogId: number
  dutyGroup: string
  page?: number
  size?: number
  anchorId?: number | null
}): Promise<AnomalyWorkspace> {
  const query = new URLSearchParams({
    alert_log_id: String(params.alertLogId),
    duty_group: params.dutyGroup,
    page: String(params.page ?? 1),
    size: String(params.size ?? 20),
  })
  if (params.anchorId !== null && params.anchorId !== undefined) {
    query.set('anchor_id', String(params.anchorId))
  }
  const response = await request(`/api/anomaly_analysis/evidence?${query.toString()}`)
  if (!response.ok) throw new Error('归因证据读取失败')
  return await response.json()
}

export async function createAttributionFromEvidence(payload: {
  alertLogId: number
  dutyGroup: string
  handler: string
  source: string
  dimensions?: string
}): Promise<ActionResult> {
  const response = await request('/api/anomaly_analysis/from-evidence', {
    method: 'POST',
    body: JSON.stringify({
      alert_log_id: payload.alertLogId,
      duty_group: payload.dutyGroup,
      handler: payload.handler,
      source: payload.source,
      dimensions: payload.dimensions ?? '',
    }),
  })
  return await response.json()
}

export async function claimEvidence(payload: {
  analysisId: number
  alertLogId: number
  ruleId?: number | null
  dutyGroup: string
  handler: string
  source: string
}): Promise<ActionResult> {
  const response = await request(`/api/anomaly_analysis/${payload.analysisId}/claim`, {
    method: 'POST',
    body: JSON.stringify({
      alert_log_id: payload.alertLogId,
      rule_id: payload.ruleId ?? null,
      duty_group: payload.dutyGroup,
      handler: payload.handler,
      source: payload.source,
    }),
  })
  return await response.json()
}

/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  metric_value_id?: number | null
  duty_group?: string
  handler?: string
  evidence_source?: string
  [field: string]: string | number | boolean | null | undefined
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult<T = EntryRow> = {
  items: T[]
  total: number
  page: number
  size: number
  anchor_id?: number | null
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow | null
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type AlertHistoryItem = {
  id: number
  alert_id: number
  rule_id?: number | null
  rule_name?: string
  duty_group?: string
  metric_value_id?: number | null
  metric_id?: number | null
  metric_name?: string
  snapshot_time?: string
  metric_value?: number
  triggered_at?: string
  '规则名称'?: string
  '监控指标'?: string
  '归属值班组'?: string
  '指标采样'?: number
  '采样时间'?: string
}

export type Evidence = {
  alert_log_id: number
  rule_id?: number | null
  rule_name: string
  duty_group: string
  metric_value_id: number
  metric_id: number
  metric_name: string
  snapshot_time: string
  snapshot_value: number
  triggered_at: string
}

export type AnalysisRow = EntryRow & {
  metric_value_id: number
  metric_name: string
  snapshot_time: string
  snapshot_value: number
  rule_id?: number | null
  rule_name: string
  duty_group: string
  handler: string
  evidence_source: string
  claimed_from_id?: number | null
  '归属值班组'?: string
  '指标采样'?: number
  '办理人'?: string
  '来源'?: string
}

export type AnomalyWorkspace = {
  ok: boolean
  message?: string
  evidence: Evidence
  items: AnalysisRow[]
  total: number
  anchor_id?: number | null
  current_duty_group: string
  existing_analysis: AnalysisRow | null
  own_analyses: AnalysisRow[]
  already_own: boolean
  claimed_by_others: boolean
  other_handlers: string[]
  own_rule: Record<string, unknown> | null
  can_claim: boolean
  needs_own_rule: boolean
  next_step: string
}

export type RuleHistoryEntry = {
  alert_log_id: number
  metric_value_id: number
  snapshot_time: string
  metric_name: string
  triggered_at: string
  duty_group: string
  analyses: AnalysisRow[]
  analysis_total: number
  has_analysis: boolean
  has_own_analysis: boolean
  can_claim: boolean
  needs_own_rule: boolean
  existing_analysis: AnalysisRow | null
  next_step: string
}

export type RuleHistoryResult = {
  ok: boolean
  message?: string
  rule: Record<string, unknown>
  current_duty_group: string
  history: RuleHistoryEntry[]
  total: number
}

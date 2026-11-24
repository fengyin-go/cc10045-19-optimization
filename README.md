# 实时数据分析与可视化应用平台

支撑实时数据聚合、多维度分析、可视化图表生成和数据应用发布的分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8019/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5199/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8019`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时聚合器 | `realtime_agg` | 聚合窗口 | 窗口名称、数据源、聚合维度 |
| 事件追踪器 | `event_tracker` | 事件定义 | 事件名称、事件属性、触发条件 |
| 仪表盘构建器 | `dashboard_builder` | 仪表盘 | 仪表盘名称、包含图表、刷新间隔 |
| 查询缓存层 | `query_cache` | 缓存配置 | 配置名称、关联数据集、缓存时长秒数 |
| 分群引擎 | `segment_engine` | 分群定义 | 分群名称、分群条件、所属人口特征 |
| 指标存储 | `metric_store` | 指标快照 | 指标名称、快照值、快照时间 |
| 下钻路径 | `drill_path` | 下钻规则 | 规则名称、起始指标、下钻维度 |
| 分析告警策略 | `alert_policy` | 告警规则 | 规则名称、监控指标、告警条件 |
| 数据报告 | `data_report` | 报告生成器 | 报告名称、包含指标集、报告周期 |
| 异常归因 | `anomaly_analysis` | 归因分析器 | 分析对象、异常时间点、归因维度 |
| 预测模型 | `forecast_model` | 预测任务 | 任务名称、预测指标、预测周期 |
| 分群对比 | `cohort_compare` | 对比报告 | 对比名称、分群A、分群B |
| 分析结果导出 | `data_export` | 导出任务 | 导出名称、查询条件、导出格式 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询历史 | `query_history` | 查询记录 | 查询时间、执行人、涉及数据集 |
| 报告快照 | `report_snapshot` | 快照记录 | 快照名称、所属报告、快照时间 |
| 数据API | `data_api` | 数据接口 | 接口名称、关联指标、请求方式 |
| 留存分析 | `retention_analysis` | 留存任务 | 任务名称、行为事件、留存天数 |
| 漏斗分析器 | `funnel_analyzer` | 漏斗定义 | 漏斗名称、步骤序列、转化窗口小时 |
| 多维数据集构建 | `cube_builder` | Cube定义 | Cube名称、事实表、维度组合 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size, anchor_id }`。`anchor_id` 是首屏
  结果集的 id 上界，翻页时原样回传，期间新插入的记录不会挤掉已看过的页。
- 动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 异常归因的证据筛选

告警历史是归因的唯一证据来源：每条 `alert_log` 在触发时绑定 `metric_value_id`
（指标采样）、`rule_id`、`duty_group`（归属值班组）。相关接口：

| 接口 | 用途 |
| --- | --- |
| `GET /api/alert_policy/history?rule_id=&metric_value_id=` | 按触发规则检索告警历史，支持 `anchor_id` 翻页 |
| `POST /api/alert_policy/{alert_id}/trigger` | 按指定 `metric_value_id` 登记一次触发证据 |
| `GET /api/anomaly_analysis/evidence?alert_log_id=&duty_group=` | 工作台：证据 + 已有归因 + 可认领 / 需自建 |
| `GET /api/anomaly_analysis/rule/{rule_id}/history` | 从自己的规则历史打开筛选结果 |
| `POST /api/anomaly_analysis/from-evidence` | 以证据登记归因记录 |
| `POST /api/anomaly_analysis/{id}/claim` | 认领相同证据，另存本组记录 |
| `GET /api/anomaly_analysis?rule_id=&metric_value_id=&alert_log_id=` | 归因列表按证据精确过滤 |

可靠性规则：

- 证据只按精确 id（`metric_value_id` / `alert_log_id` / `rule_id`）绑定和过滤，
  不按指标名称或时间字符串匹配，同名指标、同一分钟的两个采样不会串位。
- 每条归因记录都保存它实际取用的 `metric_value_id`；`启动分析` 固定使用该采样，
  不会换成同名指标的最新值。
- 认领相同证据时另存一条本组记录，源记录（如渠道值班已办理的那条）的办理人和
  来源保持不变；同一组重复认领幂等。
- 翻页使用 `anchor_id`，后插入的样本不会挤掉当时可办理的记录。

回归测试：`PYTHONPATH=backend backend/.venv/bin/python backend/tests/test_anomaly_evidence.py`

领域：数据工程
子方向：数据分析与数据应用

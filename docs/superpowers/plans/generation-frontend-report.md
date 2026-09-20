# 生成任务与资产库前端交付记录

日期：2026-09-20。范围为实施计划 Task 4；本次仅修改 frontend 与本报告。

## 已实现

- 侧栏顺序：项目管理、素材库、任务管理、资产库、AI 配置。
- `/tasks/text|image|video`：三个独立创建表单与 Axios 方法，类型匹配的配置选择，状态/配置/来源/时间筛选，服务端分页，URL 恢复筛选和详情。
- 任务详情：文本正文、部分结果、媒体资产入口、输入参数、调用记录、安全错误，以及由 `can_cancel/can_retry/can_resume` 决定的确认操作。活跃任务每 4 秒查询；终态及 unknown 停止自动查询；切换类型/筛选、关闭详情时取消过时 GET。
- 创建与重新生成采用独立幂等键。网络失败保持原键；成功或修改请求后换键。sessionStorage 仅保存 SHA-256 摘要和随机键，不保存创作内容或凭据；浏览器禁用存储时退回内存。
- `/media-library/image|video`：名称/来源/时间筛选、分页、图片预览、视频按需播放、详情签名 URL 刷新、名称乐观版本更新、真实目标采用确认。
- 采用需明确填写真实服务端目标 ID、当前媒体 ID（没有当前媒体才为 null）并勾选确认；共享素材另有影响确认。不会使用本地演示项目/分镜 ID，不会在轮询完成后自动采用。
- UI 沿用现有 graphite/amber 配色、Ant Design token 和侧栏。手机上收起筛选，任务表保持摘要、状态和详情操作可见。

## 修改路径

- `frontend/src/api/types/generations.ts`
- `frontend/src/api/modules/generations.ts`
- `frontend/src/api/modules/media-library.ts`
- `frontend/src/api/http.ts`（补充生成字段的安全校验提示）
- `frontend/src/features/generations/{attempt,ConfigSelect,CreateGeneration,presentation,TaskDetail,useRemotePage}.ts[x]`
- `frontend/src/features/media-library/AssetDetail.tsx`
- `frontend/src/pages/tasks/TasksPage.tsx`
- `frontend/src/pages/media-library/MediaLibraryPage.tsx`
- `frontend/src/components/layout/Sidebar.tsx`
- `frontend/src/components/ui/Icon.tsx`
- `frontend/src/app/{App.tsx,generations.css}`
- `frontend/tests/generation-attempt.test.mjs`
- `frontend/output/playwright/generation-{setup,check,final-check}.js` 与四张 desktop/mobile 截图。

## 验证

- `npm.cmd run typecheck`：通过。
- 最终 `npm.cmd run build`：通过，1587 modules。Vite 仍提示 Ant Design chunk 超过 500 kB（约 835 kB / gzip 262 kB），没有构建错误。
- `node --test tests/generation-attempt.test.mjs`：3/3 通过，覆盖同请求失败重试复用键、不把正文存入浏览器存储、修改/成功后新键、64 位字符串 ID 边界。
- Playwright 实际浏览器 + 本地 Vite 5176 + 完整 `/api/v1/**` fixture 拦截：三个创建表单均提交正确类型入口；文本首次 503 后输入保留且重试相同幂等键；任务文本正文可见；unknown 仅展示允许的恢复操作；资产改名发送字符串版本；采用只在确认后发起且目标 ID/expected_media_id=null 正确；409 改名保留草稿且要求重新加载；本地演示 ID 被拒；资产列表 503 保留旧结果并可恢复；390px 手机无横向页面溢出且详情操作在屏内。
- 截图：`generation-tasks-desktop.png`、`generation-tasks-mobile.png`、`generation-assets-desktop.png`、`generation-assets-mobile.png`，位于 `frontend/output/playwright/`。

## 验证边界

此子任务的浏览器验证使用模拟 API 合约，未调用真实模型、RabbitMQ、MinIO 或业务数据库；真实后端集成由主任务单独验证。视频播放使用真实浏览器控件但未加载供应商视频。取消/重新生成/恢复的按钮、确认及请求已实现，未在浏览器 fixture 中覆盖所有后台状态转移或收费模型结果。

Task detail 的媒体摘要没有 URL 时显示媒体入口；打开资产详情后调用 GET 资产详情获取签名 URL。当前项目编辑页仍是本地演示，采用通过显式真实服务端目标 ID 完成。

视觉检查在当前子任务内完成（按主任务要求不再派生子代理），沿用 `studio.css`、`web.css`、`main.tsx` 既有 token；未重写现有设计体系。

## 后端只读合约复核

创建、来源、retry 空对象/可选 config_id、cancel/resume 无请求体、rename 字符串 row_version、apply 必填 nullable expected_media_id 均已对齐。

发现并报告主任务的具体问题：`media_asset_service.py:102-105,163` 将供应商 resolved_parameters 的秒单位 duration 当成业务毫秒，可能把 6000ms 视频采用为 6ms。应采用用户明确填写的毫秒或真实媒体 duration_ms，不直接使用供应商原始 duration。此子任务未编辑后端。

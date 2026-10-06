# 无限画布整站清理与访问故障修复

记录日期：2026-10-06。本次只处理用户报告的“画布误报归档/撤权”和“返回画布库进入 BeefTV 整站”两项问题，不将本次完成视为完整画布迁移验收。

## 1. 故障原因与修复

用户从 `http://127.0.0.1:8081/canvas-app/canvas/{source_key}` 打开画布。业务库只读核对确认项目和画布未归档，可信账号读取 resolve、my-document 成功；自动保存 viewport 返回 403。

本机 API 的 `PUBLIC_ORIGIN` 原为 `http://127.0.0.1:8080`，与实际工作台端口不一致。身份中间件仍按来源和 CSRF 校验写请求，返回 `csrf_failed`。旧画布请求适配器把任意 403/404 都判成整张画布撤权，因而隐藏编辑器并显示误导性的归档提示。

- [访问判断](../../frontend/canvas/src/services/host-canvas-access.ts)现在只认当前 Python 权限边界明确的 `404 not_found`；CSRF、邮箱校验、写入规则失败仍返回原错误，保留局部保存反馈，不冻结可读取的整图。
- [请求适配](../../frontend/canvas/src/services/api/request.ts)继续捕获可信账号、检查会话 epoch、使用宿主 HTTP 客户端；不解除项目权限、Origin 或 CSRF 检查。历史条目和媒体对象缺失不能冻结整图，初次复制的来源缺失也不能冒充目标撤权。
- 本机已有配置仅调整 `PUBLIC_ORIGIN` 为 `http://127.0.0.1:8081`，未覆盖其他配置或加密主密钥；API 和调度器重新加载配置。换端口时必须同步该值，不能关闭 CSRF 绕过问题。
- 旧标签页已冻结的内存状态需要重新加载清除；未清理用户本机草稿、重建画布或覆盖服务器作品。

## 2. 实际删除范围

从 `frontend/canvas/` 物理删除 77 个固定源文件。完整路径、原哈希和原因保存在 [来源适配账本](../../frontend/canvas/source-adaptations.json)，原 [来源清单](../../frontend/canvas/source-manifest.json)仍固定在 `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。

| 删除项 | 文件数 |
| --- | ---: |
| 首页 `src/pages/home/` | 4 |
| 整站项目列表、详情、章节、工作流管理 `src/pages/projects/` | 22 |
| 源画布库 `src/pages/canvas/index.tsx` | 1 |
| 独立创作页面 `src/pages/create/` | 11 |
| 模型设置页面 `src/pages/settings/` | 6 |
| 独立资产管理页面 `src/pages/assets/` | 4 |
| 外部 Agent 管理页面 `src/pages/agents/` | 3 |
| 独立任务管理页面 `src/pages/tasks/` | 8 |
| 整站顶部导航、侧栏、命令面板、更新/变更记录等 layout | 11 |
| 整站导航 helper | 3 |
| BeefTV 品牌组件和三张品牌静态图 | 4 |

启动 HTML、加载状态和浏览器标题改为无限画布与宿主图标。未复制或修改 `D:\code\BeefTV` 源项目，也未停止其 8080 服务。

[画布路由](../../frontend/canvas/src/router.tsx)只有 `/canvas/:id` 渲染业务页面。旧 `/settings` 转宿主 `/ai_config`，旧 `/assets/*`、`/tasks/*` 分别转宿主入口，其余旧整站深链转 `/projects`。没有隐藏的 BeefTV 首页或管理路由。

[画布顶栏](../../frontend/canvas/src/pages/canvas/canvas-project-top-bar.tsx)移除“回到主页 / 全部项目 / 创建新项目”的源库链接，提供“返回工作台 / 模型配置”。错误页也提供“返回工作台”，不再显示“返回画布库”。正常退出先等个人偏好和作品远端回执；失败或 409 留页保稿。已撤权、未加载或只读画布退出只完成本机持久化，不强行写服务器。删除最后一张画布成功后直接回宿主项目列表，不重新保存已删除作品。

等待保存期间仍允许用户操作，离开前再次检查节点/连线/对话/视口的当前引用、模型偏好脏状态和既有未确认编辑队列；有新输入时完成本机持久化并留页提示重试，不让较早一次保存回执带走后续输入。生产浏览器使用等待期间重命名节点的真实操作覆盖此路径。

退出使用 `/projects`：当前宿主 `/projects/{id}` 对无限画布项目会自动启动主画布，直接返回该路径会再次打开画布。项目详情管理需要另行接入宿主明确的详情分支。

## 3. 必须保留的共享功能与依据

**没有必须保留 BeefTV 首页或整站管理页面的技术原因。** 以下保留的是编辑器依赖，不是整站页面。

| 保留模块 | 实际调用与不能直接删除的原因 |
| --- | --- |
| [AppProviders](../../frontend/canvas/src/components/layout/app-providers.tsx)、ClientRootInit、[WorkspaceBootstrapHydrator](../../frontend/canvas/src/components/workspace/workspace-bootstrap-hydrator.tsx) | 编辑器入口依赖 React/AntD 上下文、主题、QueryClient、可信账号、按账号恢复持久化及生成消费者；删除会影响弹窗、颜色、身份隔离和草稿恢复 |
| [UserLayout](../../frontend/canvas/src/layouts/user-layout.tsx)与源主题/编辑器 CSS | 仅保留具体画布原有尺寸、滚动边界和 body 浮层标记；整站 Shell、侧栏和菜单已移除，不能为了删整站破坏画布几何 |
| [WorkspaceState](../../frontend/canvas/src/components/layout/workspace-state.tsx) | 节点搜索、绘图/导演编辑弹窗和启动失败状态实际引用；仅加载/空/错误反馈，不渲染首页 |
| [workspace-page.tsx](../../frontend/canvas/src/components/layout/workspace-page.tsx)的 PaginationBar | 文件已裁剪为分页组件；[资产选择弹窗](../../frontend/canvas/src/components/assets/asset-library-picker-modal.tsx)引用它，不含原页面壳和标题区 |
| `components/canvas/`、`pages/canvas/` 编辑器、资产选择/托盘、生成记录、助手、绘图与媒体工具 | 节点创建、资源引用、生成参数与回填、助手和图内操作依赖；这些是用户要求保留的无限画布功能 |
| [local-workspace-repository](../../frontend/canvas/src/services/local-workspace-repository.ts)、[canvas-sync-drafts](../../frontend/canvas/src/services/canvas-sync-drafts.ts)、私人模型偏好与资源/任务 API helper | 串行自动保存、CAS、409 保稿、任务恢复、稳定资源定位和账号隔离依赖；删除同名服务不能替代删除整站 UI |

一次额外的 44 个辅助文件批量清理被自动审批拒绝，理由是其中含画布、回收站、Agent 和任务相关服务，静态入口可达分析不足以证明不影响画布。该可选整理未执行，未阻止上述 77 个整站文件实际删除；不为进一步整理保留可访问的整站页面。

## 4. 来源检查和验证范围

[source-check](../../frontend/canvas/scripts/check-source.mjs)支持显式 `action: removed`，要求原因和原来源哈希匹配，且文件实际不存在。未登记的缺失继续失败，已删除文件被重新放回也会失败。禁止重新运行 importer 覆盖宿主适配。

本次实际执行结果已同步至[实施记录](2026-10-05-beeftv-implementation-log.md)。浏览器验证区分两种边界：

- `e2e/host-canvas.test.mjs` 使用 API 替身，验证实际编辑器拖拽、保存、撤权、退出保存等待、409 保稿及模型偏好冲突。
- `scripts/verify-python.mjs` 与 `test_canvas_browser.py` 使用真实登录、Python API 和随机隔离 MySQL，不拦截 API、不调用模型；宿主目标是明确的 HTML 导航边界，仅验证地址和未加载源整站模块，不等于宿主业务页面渲染验收。

| 实际执行 | 结果 |
| --- | --- |
| 画布完整 Node 测试 | 218 通过，无跳过 |
| 统一生产 preview 的 `host-canvas.test.mjs` | 2 通过；包含偏好/作品冲突、退出等待、新输入保留及真正撤权 |
| 显式启用的 Python/随机 MySQL 浏览器测试 | 1 通过，无跳过；真实 CSRF 403 不冻结、恢复保存、拖拽刷新、跨窗外观 409、退出与 14 个旧深链 |
| `source:check` | 固定 832 个来源、168 条适配、77 条删除记录通过 |
| 统一两包生产构建 | 宿主与画布类型检查和构建通过；现有大分块/静动引用提示保留 |
| 独立与合并画布产物 SHA-256 | 874 个文件全部一致，旧 BeefTV 品牌静态文件不存在 |
| Python 新改测试 Ruff check/format | 通过 |
| 文档本地链接与 `git diff --check` | 通过 |

失败和修正也保留在忽略的 `.runtime/` 日志：首次浏览器命令未指定本机 Chrome，缺少 Playwright 下载的浏览器；切换已安装 Chrome 后通过。真实 harness 最初全局改写 Origin 的错误没有 status，原异常未记录完整，不能据此认定原因；后来保持合法 Origin、仅使用合成无效测试 CSRF cookie/header，取得真实 `403 csrf_failed` 和恢复成功证据。开发浏览器一次 `page.goto` 的 load 超时，最终采用独立生产 preview 并等待编辑器节点/状态，全部生产回归通过；第二次拖拽未产生位移的测试动作改为可观察的节点重命名，没有将未发生的编辑算作验收。

两次随机测试库均已记录成功 CREATE/DROP，撤销临时权限后清理；业务库仅用于故障只读核对。验证期间暂时停止的 Worker 已按原角色恢复，统一 dev 仍使用 8081/8082，源 BeefTV 8080 保留。当前代码未新增提交或推送，原完整迁移 goal 保持此前的暂停状态。

依赖原整站库、资产、文件夹、回收站的旧浏览器 harness 不再代表当前产品验收。已在 [子包说明](../../frontend/canvas/README.md)列出；应将画布级导入/恢复等操作接入宿主或图内入口，再恢复这些用户流程的验收。不得为旧测试重新引入 BeefTV 整站。

本次未调整 ORM/表结构，未执行业务数据库 DDL，未调用真实付费模型。标准模式实现未改；完整画布助手、媒体工具、剩余协议、导入导出和一比一视觉验收仍按接续文档推进。

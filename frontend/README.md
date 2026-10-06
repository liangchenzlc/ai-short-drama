# 前端

React 19 + TypeScript + Vite + Ant Design 网页工作台。项目、正文、素材、分镜、生成任务和采用结果通过 Axios 连接后端；模型选择保留为浏览器偏好。无需 Electron。

## 运行与检查

两套前端统一运行推荐 Node.js 22.18+ 的 22.x 或 24.x LTS，在本目录执行：

```powershell
npm run install:all
npm run dev
```

开发地址 `http://127.0.0.1:8080`。`dev` 在一个 Node 进程内创建标准工作台和画布两台独立 Vite 服务器，画布默认监听 8082，通过宿主 `/canvas-app/` 同源访问；退出时关闭这两台服务器。可用 `CANVAS_DEV_PORT` 调整画布端口；`dev` 接受 `--port`、`--host`、`--mode`、`--open`、`--force`、`--strictPort`。API 默认代理至 `http://127.0.0.1:8000`。修改代理时使用 `.env.local`，参照 [.env.example](.env.example)，禁止在 VITE 变量放凭据。只运行一个包时使用 `npm run dev:standard` 或 `npm run dev:canvas`。

```powershell
npm test
npm run typecheck
npm run build
npm run test:frontends
npm run preview
```

`install:all` 分别使用宿主和画布子包的 `package-lock.json` 执行 `npm ci`；两包继续保留各自的依赖目录和 HTML 入口。共同声明但版本不同的直接依赖使用宿主版本，画布独有依赖继续单独锁定，详见[画布说明](canvas/README.md)。

`build` 依次完成两包类型检查和生产构建，再将独立画布产物复制到 `dist/canvas-app/`；画布自己的 `canvas/dist/` 仍可单独构建。`preview` 只启动宿主 Vite preview，使用统一 `dist/`，对画布页面回退到 `/canvas-app/index.html`，对标准页面回退到 `/index.html`，API 沿用代理配置，不需要额外画布 preview 进程。静态文件本身不含 API 代理能力，正式部署仍需配置反向代理和两套 HTML 回退。

`npm test` 和 `npm run test:e2e` 保持标准工作台的测试范围；画布检查可从本目录运行 `npm run test:canvas`、`npm run test:e2e:canvas`、`npm run source:check`，也可在 `canvas/` 独立执行。`test:frontends` 需要先完成统一构建，使用临时端口验证双服务启动/退出、端口占用时清理、独立 HTML 与深链接、资源和 API 代理。Node 测试覆盖保存队列、导航、版本冲突、模型选择、生成契约和部分 UI 源码约定。浏览器 API 夹具测试不调用真实模型。

```powershell
npx playwright install chromium
npm run test:e2e
# 已有本地 Chromium 时可通过 PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH 指定路径
```

浏览器测试启动独立 Vite 端口 4175，覆盖桌面与 390px 窄屏、TXT 导入、抽屉、参考图持久化、懒加载、历史采用与任务提示词。失败截图与 trace 保存在忽略的 `.runtime/browser-results/`。

## 路由

| 路径 | 页面 |
| --- | --- |
| `/projects` | 项目搜索、分页和继续创作 |
| `/projects/:projectId` | 项目信息、分集、项目资源库 |
| `/projects/:projectId/episodes/:episodeId/:stage?` | 四步分集制作；stage 为 source/assets/storyboard/assembly，旧 script 链接打开定稿标签 |
| `/assets/:kind` | character/scene/prop 三类个人素材 |
| `/ai` | 文本、图片、视频模型配置 |
| `/tasks/:kind` | text/image/video生成任务 |
| `/media-library/:kind` | image/video生成媒体资产 |
| `/login`、`/register`、`/verify-email`、`/reset-password` | 登录、注册、邮箱验证和密码找回 |
| `/invite/:token` | 定向邀请预览、指定账号登录及本次邮箱验证 |

旧video和无效步骤链接按当前待处理阶段规范化；旧快照算出的video阶段映射到storyboard。BrowserRouter使用真实路径；生产部署要配置API代理、SPA fallback及正确的Vite资源base，详见[部署限制](../docs/development.md#部署与维护)。

## 模块职责

- `src/app`：应用入口、路由、主题和样式。
- `src/pages`：项目、分集、素材、任务、资产和配置页面。
- `src/features/projects`：正文保存会话、导航保护、素材提取、分镜候选与图片采用；成片的帧编辑模型、双视频播放器、单轨时间轴、会话内撤销重做和串行自动保存。
- `src/features/assets`：三层素材库共用UI和请求状态。
- `src/features/auth`：账号会话、过期时保留编辑、账号草稿隔离与个人模型偏好。
- `src/features/generations`：通用生成、任务详情、轮询与幂等请求标识。
- `src/features/media-library`：媒体详情与图片选择器。
- `src/api`：DTO、请求封装和统一错误；见[前端API约定](src/api/README.md)。
- `canvas/`：独立无限画布子包，保留 BeefTV 页面、交互、Provider、样式、字体、媒体工具及来源记录，通过 `@host` 引用宿主 HTTP 客户端；独立依赖、HTML 和测试仍由本包管理。

`episode-workflow.ts`仍承担旧浏览器快照读取/校验与模型偏好兼容。保留的旧媒体、宫格、video类型不是当前页面能力；对应演示生成组件已经移除。不要绕过服务端重新启用本地生成结果。

## 保存与交互

小说与剧本停顿1秒自动保存，串行队列共享服务器版本，旧响应不覆盖新输入。切步骤/离开页面先等待保存，失败则保留草稿并要求明确处理；刷新关闭用浏览器离开提示。冲突可下载草稿，再载入服务端版本手动核对。

AI 设置栏与正文并列，小说/定稿合并为标签页；分镜每批 20 条按需加载，历史候选放在弹窗。素材和分镜的参考图通过专用接口持久化，独立于生成候选及当前采用图。AI结果先预览后采用。素材上传不自动确认，分镜生成不直接覆盖镜头，任务页成功不等于项目已采用。详情页与任务列表刷新使用当前服务器状态；临时媒体URL失效时重新读取。

账号模式只读取本人命名空间下的浏览器草稿。未归属的旧版正文/素材仅在受控兼容模式提供显式导入或 JSON 下载，不自动上传或覆盖任何账号数据。保留兼容读取不代表浏览器存储仍是业务数据源。设计与部署见[共同创作方案](../docs/plans/2026-10-02-project-collaboration.md)和[迁移说明](../docs/collaboration-deployment.md)。

产品边界见[PRODUCT.md](PRODUCT.md)，界面约定见[DESIGN.md](DESIGN.md)，后端契约见[接口说明](../docs/api/README.md)。

时间轴验收与运行条件见[实施验收记录](../docs/reviews/2026-09-29-assembly-timeline.md)，依赖与上游参考见[时间轴依赖](docs/timeline-dependencies.md)。`scripts/timeline-*.mjs` 为 Playwright CLI 的浏览器验收函数，使用隔离 API 测试替身和真实本地视频，不是 `test:e2e` 自动收集的测试。旧 `scripts/assembly-acceptance.mjs` 对应已替换的列表式剪辑界面，停止用于当前版本验收。

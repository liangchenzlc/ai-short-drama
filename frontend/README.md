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
npm test -- --feature standard.assets
npm run typecheck
npm run build
npm run test:frontends
npm run preview
```

`install:all` 分别使用宿主和画布子包的 `package-lock.json` 执行 `npm ci`；两包继续保留各自的依赖目录和 HTML 入口。共同声明但版本不同的直接依赖使用宿主版本，画布独有依赖继续单独锁定，详见[画布说明](canvas/README.md)。

`build` 依次完成两包类型检查和生产构建，再将独立画布产物复制到 `dist/canvas-app/`；画布自己的 `canvas/dist/` 仍可单独构建。`preview` 只启动宿主 Vite preview，使用统一 `dist/`，对画布页面回退到 `/canvas-app/index.html`，对标准页面回退到 `/index.html`，API 沿用代理配置，不需要额外画布 preview 进程。静态文件本身不含 API 代理能力，正式部署仍需配置反向代理和两套 HTML 回退。

`npm test` 和 `npm run test:e2e` 保持标准工作台的测试范围，必须追加功能或文件参数；画布检查可从本目录运行 `npm run test:canvas -- --feature canvas.models`、`npm run test:e2e:canvas -- --feature canvas.characters`、`npm run source:check`，也可在 `canvas/` 独立执行。目录、功能编号及选测规则见[测试规范](../docs/instruction/按功能执行测试.md)。`test:frontends` 需要先完成统一构建，使用临时端口验证双服务启动/退出、端口占用时清理、独立 HTML 与深链接、资源和 API 代理。Node 测试覆盖保存队列、导航、版本冲突、模型选择、生成契约和部分 UI 源码约定。浏览器 API 夹具测试不调用真实模型。

```powershell
npx playwright install chromium
npm run test:e2e -- --feature standard.assets
# 已有本地 Chromium 时可通过 PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH 指定路径
```

浏览器测试启动独立 Vite 端口 4175，覆盖 1280px 及以上桌面、参考图持久化、懒加载、候选采用、自动保存、冲突与助手交互。失败截图与 trace 保存在忽略的 `.runtime/browser-results/`。

新建只填写名称与模式；名称最多 120 字符，允许重名。创作工作台顶部提供名称原位改名、阶段导航、保存状态、助手和返回；没有项目梗概、画幅、风格或归档设置。

## 路由

| 路径 | 页面 |
| --- | --- |
| `/projects` | 项目搜索、分页和继续创作 |
| `/projects/:projectId` | 创作项目转入 source 阶段，画布项目进入原生画布 |
| `/projects/:projectId/:stage` | 四步创作，stage 为 source/assets/storyboard/assembly |
| 旧分集、素材库和邀请链接 | 显示已停用，不能恢复退役入口 |
| `/ai` | 文本、图片、视频模型配置 |
| `/tasks/*` | 已退役，兼容跳转项目列表 |
| `/media-library/:kind` | all/image/video/audio，本人生成资产的工作台分类、名称搜索与纯预览 |
| `/login`、`/register`、`/verify-email`、`/reset-password` | 登录、注册、邮箱验证和密码找回 |

旧video和无效步骤链接按当前待处理阶段规范化；旧快照算出的video阶段映射到storyboard。BrowserRouter使用真实路径；生产部署要配置API代理、SPA fallback及正确的Vite资源base，详见[部署限制](../docs/instruction/开发运行与验证.md#部署与维护)。

## 模块职责

页面在 src/pages 以目录和 index.tsx 组织，页面样式独立；业务进入 src/features 的业务／功能目录。请求放 src/api，具名类型放 src/types；通用界面与工具放 src/shared。应用壳在 src/app，画布宿主桥在 src/integrations/canvas。完整目录与旧文件去向见[目录与职责维护](../docs/instruction/目录与职责维护.md)。

## 保存与交互

剧本、故事设定、素材、分镜、声音和成片各自使用串行编辑会话，旧响应不覆盖新输入，409 保留本机稿；未确认保存回执继续按原请求核对。普通应用内切换释放订阅，只有仅在内存的输入才保护浏览器退出。账号与稳定对象共同隔离恢复稿，损坏记录保留原文供下载。

剧本提供故事设定／正文双标签和选区 AI 改写，小说／大纲及专属 API 已退役。结构化候选仍须明确采用，素材与分镜参考图独立保存；生成成功不等于候选已经采用。临时媒体 URL 失效时重读。

产品边界见[PRODUCT.md](PRODUCT.md)，界面约定见[DESIGN.md](DESIGN.md)，后端契约见[接口说明](../docs/api/README.md)。

时间轴验收与运行条件见[实施验收记录](../docs/reviews/2026-09-29-成片时间轴验收.md)，依赖与上游参考见[时间轴依赖](docs/timeline-dependencies.md)。`scripts/timeline-*.mjs` 为 Playwright CLI 的浏览器验收函数，使用隔离 API 测试替身和真实本地视频，不是 `test:e2e` 自动收集的测试。旧 `scripts/assembly-acceptance.mjs` 对应已替换的列表式剪辑界面，停止用于当前版本验收。

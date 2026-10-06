# 无限画布独立前端

本包从 BeefTV 固定 commit `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c` 迁入，现位于 `frontend/canvas/`。独立 React root、依赖锁、HTML、Provider、CSS、字体与媒体构建保留源编辑器结构，通过 `/canvas-app/` 与标准工作台同源部署。按用户后续调整，共同声明但版本不同的直接依赖采用宿主版本，源独有依赖继续独立锁定；来源清单仍保留原版版本信息。完整迁移仍在进行，阶段状态见[实施记录](../../docs/plans/2026-10-05-beeftv-implementation-log.md)。

## 产品边界

本包仅提供 `/canvas-app/canvas/:source_key` 无限画布编辑器。2026-10-06 按用户要求物理删除源首页、项目库及详情、资产管理、设置、外部 Agent、独立创作页、任务管理页和整站侧栏、搜索面板、品牌组件；这些页面不再作为源代码或构建入口存在。项目、资产、模型配置和任务管理使用当前工作台。

| 旧画布子包路径 | 当前入口 |
| --- | --- |
| `/canvas-app/canvas/:source_key` | 无限画布编辑器 |
| `/canvas-app/settings` | 宿主 `/ai_config`，仅携带安全的画布返回地址 |
| `/canvas-app/assets/*` | 宿主 `/assets` |
| `/canvas-app/tasks/*` | 宿主 `/tasks` |
| `/canvas-app/`、`/home`、`/canvas`、`/project`、`/projects/*`、`/create`、`/agents` 及其他旧路径 | 宿主 `/projects` |

编辑器“返回工作台”和“模型配置”入口先等待个人偏好与作品远端保存；失败或版本冲突留在原页并保留草稿。共享 Provider、画布 CSS/字体、媒体与资源选择弹窗、生成记录、助手及保存/权限模块继续保留：它们是编辑器依赖，不提供 BeefTV 整站页面。`workspace-page.tsx` 仅保留资源选择器所需的分页组件；`workspace-state.tsx` 为节点搜索、编辑弹窗和工作区恢复提供加载/空/错误状态。独立 HTML 和原编辑器容器尺寸继续保留，首屏、标签页和图标使用当前工作台画布标识。

## 本机运行

推荐 Node 22.18+ 的 22.x 或 24.x LTS；画布测试使用 Node 类型剥离，nanoid 6 不支持 Node 20。通常在父目录 `frontend/` 执行 `npm run install:all`、`npm run dev`：分别按两包锁文件安装，再在一个 Node 进程中启动独立的标准工作台 8080 和画布 8082；退出时关闭本次创建的两台服务器。也可从父目录运行 `npm run dev:canvas`，或在本目录独立运行 `npm ci`、`npm run dev`。

宿主 Vite 把 `/canvas-app` 代理到画布端口，API 仍走当前 Python 服务及宿主配置的 `API_PROXY_TARGET`；可用 `CANVAS_DEV_PORT` 调整统一启动时的画布端口。浏览器通过宿主项目入口进入画布，API 的 `PUBLIC_ORIGIN` 必须与浏览器访问的宿主 origin 一致。不要替换已运行的 BeefTV 3000/8080 进程。

父目录 `npm run build` 分别进行两包类型检查和生产构建，将本包 `dist/` 复制至宿主 `dist/canvas-app/`。父目录 `npm run preview` 用这一统一产物提供标准页面和画布独立 HTML 回退，只需一个预览进程。独立 `npm run build`、`npm run preview` 仍可在本目录使用；正式部署要分别配置 `/canvas-app/*` 和标准页面的 HTML 回退及 `/api/` 代理。

页面先通过 `/auth/me` 确认账号，再导入带持久化状态的编辑器模块。画布 JSON 和 Blob 请求复用 `frontend/src/api/http.ts` 的 Axios 实例、CSRF 和错误处理。源接口由 `services/host-canvas-contract.ts` 翻译到 Python 契约，未适配业务使用专用 `/canvas-runtime` 命名空间，避免与标准模式的同名项目、素材接口混淆。

## 依赖兼容

两包仍各自维护 `package.json`、`package-lock.json` 和 `node_modules/`。共同直接依赖的声明范围与宿主相同，2026-10-06 实际锁定版本逐项一致：

| 依赖 | 实际版本 |
| --- | --- |
| `@ant-design/v5-patch-for-react-19` | 1.0.3 |
| `antd` | 5.29.3 |
| `axios` | 1.20.0 |
| `react`、`react-dom` | 19.3.0 |
| `@types/node` | 20.19.43 |
| `@types/react`、`@types/react-dom` | 19.3.0 |
| `@vitejs/plugin-react` | 5.2.0 |
| `typescript` | 5.9.3 |
| `vite` | 7.3.6 |

画布独有的 `react-router@8.2.0` 继续保留；宿主使用自己的 `react-router-dom@7.18.4`，两包各自运行 React root。以下调整由宿主版本的真实 peer dependency 约束决定，标准前端未为画布升级依赖：

- `@ant-design/x@1.6.1` 是支持 Ant Design 5 的最高稳定版本，配合 `@ant-design/icons@5.6.1`；原版 X 2.x 要求 Ant Design 6。提示词助手把 `role`/`suffix` 等价映射为 `roles`/`actions`，保留原滚动容器、发送、优化方式菜单与采用操作。
- 原 `@lobehub/icons@5.16.0` 的完整包要求 Ant Design 6/LobeHub UI 5，而兼容 Ant Design 5 的旧版本会减少 53 个 Logo。现在仅使用 [vendor/lobehub-icons](vendor/lobehub-icons/README.md) 中原始字节未改的 321 个 Mono SVG、目录及必要模块，保留 MIT 许可证和逐文件 SHA-256；继续按需加载，并显式锁定其纯辅助依赖 `es-toolkit@1.52.0`。
- `@react-three/fiber` 从 9.6.1 调整到支持 React 19.3 的最小稳定版本 9.8.0；`@react-three/drei@10.7.7` 保留，其 `^9.0.0` peer 范围兼容该版本。
- Excalidraw 0.18.1 内部固定的 Radix Tabs 1.0.2 仅声明 React 16–18，通过限定在 Excalidraw 内的 override 使用支持 React 19 的 `@radix-ui/react-tabs@1.1.13`，不改变绘图业务代码。

最终锁文件已通过正常 `npm ci` 和 `npm ls --all`，没有使用 `--force` 或 `--legacy-peer-deps`。`tests/model-logo-integrity.test.mjs` 核对 321 项、649 个原始文件与许可；`e2e/dependency-compat.test.mjs` 使用真实组件和浏览器验证长对话滚动、Sender 输入/两种发送、页脚菜单/采用回填、全部 321 项 Logo 列表及 Nano Banana 按需加载。可分别执行 `node --test tests/model-logo-integrity.test.mjs`、`node --test e2e/dependency-compat.test.mjs`；后者可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`。这些验证使用受控优化供应商替身，不代表真实模型或完整原版视觉验收。

## 验证与来源

- `npm test`：覆盖版本、路径、首次请求重试、HTTP 适配、启动顺序、个人视口/外观串行与冲突、撤权会话隔离、上传范围捕获及幂等身份合同；实际数量以当前执行结果为准。
- `npm run build`：完整画布类型检查与生产构建，包括 wasm/Worker。
- `npm run test:e2e`：真实浏览器与 API 夹具验证打开、拖拽、保存、视口/外观刷新恢复与撤权阻断，以及依赖兼容组件。可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 使用本机 Chromium；`CANVAS_BROWSER_BASE_URL` 可让原编辑器测试复用已启动的统一生产 preview，未设置时仍启动独立开发服务。该测试不代表 Python、媒体或供应商验收。
- Python 联调：在 `backend/` 设置隔离的 `TEST_DATABASE_URL`（库名以 `_test` 结尾，有临时建库权限）和 `RUN_CANVAS_BROWSER_INTEGRATION=1`，运行 `uv run pytest tests/integration/test_canvas_browser.py -q`。测试随机建库、真实登录后调用 `scripts/verify-python.mjs`，临时前端端口 4186，API 端口由系统分配，结束后清理。不拦截 HTTP；覆盖拖拽落库、刷新、外观恢复和跨窗口冲突。报告在 `.runtime/python-browser/report.json`，明确记录尚未迁移模块的真实 404；不代表媒体/供应商/助手验收。
- `npm run test:parity`：读取固定、干净的 BeefTV 源仓库，在已运行的原版开发服务与本包之间执行相同操作、截图和几何比较。默认源路径 `D:/code/BeefTV`、源 URL `http://127.0.0.1:3000`，可由 `BEEFTV_ROOT`/`BEEFTV_BASE_URL` 覆盖；目标临时端口 4184，退出后关闭。所有业务请求被测试夹具拦截，不写源工作区。截图、差异图与报告写入忽略目录 `.runtime/parity`。当前覆盖 17 个场景，包括基础画布、深浅/自定义外观、素材托盘、窄屏、历史预览和绘图；捕获前等待几何与控件颜色/阴影过渡稳定。报告差异不等于完整验收，真实媒体、生成和助手须另验。
- `npm run source:check`：核对固定源文件哈希与 `source-adaptations.json` 的显式适配记录；物理删除文件使用 `action: removed`，保留删除原因和上游哈希，检查已删除文件没有被重新引入。

资源 API、MySQL、MinIO 和真实 ffprobe/FFmpeg 样本测试继续使用隔离测试数据库与随机 `canvas-test-*` buckets，结束后清理。旧 `scripts/verify-resource-python.mjs` 包含已删除的源资产分类页与源项目库操作，整段浏览器 harness 已移出当前验收范围；过去选图、上传、登记、刷新下载及分类操作的报告只记录当时实现，不能证明当前宿主管理流程通过。GLB 字节存取不代表导演台模型验收，故障注入不代表真实断网实测。

以下旧浏览器 harness 依赖源 `/canvas-app/canvas` 项目库或 `/canvas-app/assets`，不再作为当前版本验收命令：`verify-creation-python.mjs`、`verify-creation-recovery-python.mjs`、`verify-creation-session-python.mjs`、`verify-resource-python.mjs`、`verify-recycle-python.mjs`、`verify-delete-recovery-python.mjs`、`verify-folders-python.mjs`、`verify-archive-python.mjs`、`verify-drawing-creation-python.mjs`，以及调用绘图创建旧流程的 `verify-drawing-python.mjs` 和 `compare-recycle-source.mjs`。脚本保留作历史测试资料，未改成跳过后宣称通过；对应项目/资产/回收/文件夹浏览器场景需要围绕宿主入口重新编写。独立编辑器的现行浏览器回归为 `verify-python.mjs` 和 `e2e/host-canvas.test.mjs`；媒体、生成和助手专项须核对自身入口后分别执行，不能用旧整站报告代替当前验收。

固定源版本的历史已知问题：`styles/workspace-product.css` 的减少动画规则把所有后代的 transition-duration 设为 1ms，会干扰 AntD 菜单同步测量。过去在 Chrome 154、1440×900、`reducedMotion=reduce` 下，源资产管理菜单实际定位到屏幕外。源资产页现已删除，该报告不属于当前宿主资产管理验收；共享画布 CSS 仍保留，上述画布对照范围也未覆盖这一旧整站菜单。

画布生成已接专用 Python `/canvas-runtime/tasks`：来源、目标和连线先真实保存，准入按本人模型与固定操作键登记任务，原版结果消费者直接绑定作品。刷新和未知回执查找同一任务，Config 批次来源独立收尾，稀疏产物保留原槽。供应商实际文本增量保存到 MySQL，通过 SSE 游标续读；不会用浏览器取消观察来取消收费任务，也不以 unknown 自动重发。媒体先检验并归档 MinIO、交付真实私人素材，原 bind 后才发布作品。完整合同及尚未支持的参数/协议见[画布 API](../../docs/api/canvases.md#画布生成任务与文本流)。

视频已增加原 OpenAI Videos multipart、NewAPI Channel 2 JSON 与官方 BeefAPI Seedance 预上传三条 Python 合同。参考资源使用永久身份和服务端真实元信息；Channel 2 本地参考保持原 HTTPS 替换弹窗，受理前失败可沿用原操作键，刷新后绑定同一真实任务。源全局 Ark 上传默认项在这些非 Ark 协议中不启动上传；未知收费提交显示 `submission_unknown`，原节点不提供重试。规范参数、原 ID 查询、30 秒轮询和有界下载恢复见[视频合同](../../docs/api/canvases.md#画布视频协议)。完整 M2 与收费供应商验收仍须继续。

生成真实联调测试位于 `backend/tests/integration/test_canvas_generation_runtime.py`、`test_canvas_generation_media_runtime.py`、`test_canvas_text_stream.py`、`test_canvas_generation_browser.py`。在隔离 MySQL 环境显式设置 `RUN_CANVAS_RESOURCE_MINIO=1`、`RUN_CANVAS_BROWSER_INTEGRATION=1` 后运行对应 pytest；原 UI 浏览器脚本 `scripts/verify-generation-python.mjs` 临时使用 4188。测试采用真实执行器、存储、媒体工具及浏览器，但模型供应商为受控替身；不能作为付费供应商、真实消息队列或完整一比一验收。008 增量需要部署同版 API/Worker/scheduler 才能提供实时草稿，应用不自动建表。

视频专项为 `test_canvas_video_protocols.py`、`test_canvas_video_policy_runtime.py` 和 `test_canvas_video_options_runtime.py`，使用同一隔离 MySQL/MinIO 开关；原视频按钮的实际 UI 脚本为 `scripts/verify-video-protocols-python.mjs`，临时端口 4194。测试按原播放按钮激活播放器，核对真实 MP4 解码、刷新与全新缓存恢复，unknown 与 HTTPS 决策保留原入口和断言。此专项使用直接执行器与受控供应商，真实消息队列证据由独立 broker 测试提供，不能合并声称正式供应商验收。

文字节点的已保存图片引用先接 Chat Completions，使用本人保存的模型图片能力与当前项目资源；
Worker 按源顺序签发执行期 MinIO URL 并生成 `image_url` 消息，标准文本输入保持原合同。
保存能力未声明图片时不自动开启。普通画布仍没有新增 thinking UI；其他文字协议与工具另行实施。
普通文字节点按源保持加载态至终态后回填，没有启用 `onTextDelta/useTextEvents`；
私人增量与 SSE 接口的恢复验证不能当作该节点的逐字展示验收。
升级前保存的绑定模型在新任务准入时刷新派生执行缓存，已提交任务保持原冻结版本。
完整范围与阶段限制见[文字引用合同](../../docs/api/canvases.md#画布文字图片引用)。

图片裁切原 UI 专项位于 `backend/tests/integration/test_canvas_image_tools_browser.py`，
对应 `scripts/verify-image-tools-python.mjs`，临时端口 4199；需同时设置上述 MinIO 与浏览器开关。
该片已完成原上传/inline 裁切、取消、派生图、刷新、两处 503 草稿恢复、真实字节与权限验收；
裁切默认选区的奇数图像素导出沿源 ceil 算法，不能用 UI round 标签替代真实输出尺寸。
切格和标注各自验收，不用裁切通过代表整个图片工具完成。

模型管理统一进入宿主 `/ai_config`；`/canvas-app/settings` 兼容深链直接转宿主，不再渲染 BeefTV 设置页。画布模型列表只读本人宿主配置及完整运行 profile，旧 `channel::model` 别名恢复原稳定 ID，明确失效的选择提示不可用。画布偏好 PUT 只提交个人默认与生成参数，任何带 channels 的旧写入明确拒绝。进入配置前等待偏好 ACK 和作品远端保存，失败或 409 保留草稿并阻止离开；宿主返回只接受具体同源画布路径。

宿主“测试模型”复用独立 `/canvas-runtime/model-tests`，不依赖画布或虚构节点，固定操作键重放未知回执，只有实际任务结果才成功。结果媒体保持本人权限；普通停用配置不能借测试标记执行。旧渠道绑定保留作选择别名；新增宿主运行 profile、加密扩展凭据及绑定迁移标记，升级见[统一模型迁移](../../docs/数据库模型/migrations/2026-10-06-host-model-runtime/README.md)。当前完整 SQL 为 93 表，部署必须同步 API/Worker/scheduler，旧 Worker 不能解析新的标准 `model_auth_*` 或画布凭据信封。完整接口见[画布 API](../../docs/api/canvases.md#独立模型测试)。

BeefAPI 连接、状态和钱包入口统一在宿主提供。托管目录的地址/profile/模型身份由服务端维护，名称、启停及本人 headers 可保存并脱敏恢复；普通 CRUD 不能伪造托管凭据。授权由 scheduler 后台继续，关闭页面不会取消。真实企业登录与收费供应商仍需单独验收。本轮仅完成模型管理统一，项目、资产和其余整站入口的收敛仍是后续切片。

资源持久化固定使用 Python API；`isLocalWorkspaceMode` 仍保留源 UI 功能分支，IndexedDB 只用于草稿和缓存。不能切回浏览器本地资源存储来绕过缺失服务，否则选图可能仅在本机显示而未写入服务器。

`scripts/import-source.mjs` 用于固定基线首次迁入，不要在已经适配的目录强制重导覆盖；它明确拒绝重新写入已登记物理删除的文件。每次确需修改源文件，要核对实际差异并通过 `scripts/check-source.mjs --record <文件> <原因>` 更新记录；明确删除后使用 `--record-removal <文件> <原因>`，不得删掉固定来源清单来掩盖变化。哈希通过只证明差异可追踪，不证明交互与视觉一致。新增适配文件、测试与独立路由不属于上游文件哈希清单。

统一静态产物的完整生产部署验收、完整资源服务、模型执行、助手和原版逐项视觉/操作对照仍需完成。共同依赖调整后的视觉和行为要重新对照，不能沿用调整前的截图或构建结果。最终一比一判定以[迁移方案](../../docs/plans/2026-10-05-beeftv-infinite-canvas-migration.md)的验收门禁为准。

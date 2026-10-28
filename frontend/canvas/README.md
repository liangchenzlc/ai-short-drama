# 无限画布独立前端

本包从 BeefTV 固定 commit `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c` 迁入，现位于 `frontend/canvas/`。独立 React root、依赖锁、HTML、Provider、CSS、字体与媒体构建保留源编辑器结构，通过 `/canvas-app/` 与标准工作台同源部署。按用户后续调整，共同声明但版本不同的直接依赖采用宿主版本，源独有依赖继续独立锁定；来源清单仍保留原版版本信息。完整迁移仍在进行，阶段状态见[实施记录](../../docs/plans/2026-10/2026-10-05-BeefTV画布迁移实施记录.md)。

根 `.gitattributes` 将本包文本固定为 LF，防止 Windows `core.autocrlf=true` 签出改变原始来源摘要；二进制保持原字节。`vendor/lobehub-icons/` 使用 `-text` 例外，保留 npm 包原始字节（包括 CRLF），由独立的 649 项第三方图标清单核对，不改原清单摘要。已有签出若仅有 CRLF 差异，先核对规范字节与登记摘要，再规范换行；不要通过批量重登记源码来隐藏真正内容差异。2026-10-08 的环境与接续证据见[剩余工作台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)。

## 产品边界

Skill 节点正文和模板快照只在本人文档中恢复，普通 text 作品仍按项目权限共享。后端也遮蔽旧共享 Skill 正文；归属未确认的旧记录保存/恢复返回 `canvas_legacy_skill_body_private` 并保留原数据，历史恢复透传作者确认说明，保存409仍保留草稿并暂停自动提交。2026-10-09 两宽度原 UI 拖动、关闭旧窗口仅cookies恢复与成员移动后的作者模板恢复已在独立 Python/MySQL 环境验证；已有快照由 API 预置，源创建入口继续隐藏，没有供应商调用。该隔离回归不代表 Skill 管理入口、旧数据缓存清理或全部引用生成已经验收，详见[接口说明](../../docs/api/canvases.md)和[剩余台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)。

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

最终锁文件已通过正常 `npm ci` 和 `npm ls --all`，没有使用 `--force` 或 `--legacy-peer-deps`。`tests/models/model-logo-integrity.test.mjs` 核对 321 项、649 个原始文件与许可；`e2e/compat/dependency-compat.test.mjs` 使用真实组件和浏览器验证长对话滚动、Sender 输入/两种发送、页脚菜单/采用回填、全部 321 项 Logo 列表及 Nano Banana 按需加载。可分别执行 `node --test tests/models/model-logo-integrity.test.mjs`、`node --test e2e/compat/dependency-compat.test.mjs`；后者可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`。这些验证使用受控优化供应商替身，不代表真实模型或完整原版视觉验收。

## 提示词优化与审美工具

提示词优化已通过宿主工具任务relay执行，携带当前画布、节点与稳定操作键，使用本人已保存文字模型；canonical只允许固定源合同与永久图片resource定位。工具结果及reasoning仅本人可见，不改变节点loading或普通任务候选，不物化/绑定作品；显式“采用优化后的提示词”才更新当前输入。普通任务恢复与绑定会排除编辑器工具。

个人工具状态通过宿主API保存在本人偏好JSON，默认关闭；模型偏好保存保留它，画布按remote存储读取。宿主只显示可达AI审美开关。固定源CSS隐藏提示词标题栏，唯一优化/放大入口也隐藏，保持源状态，不能将relay合同或依赖兼容测试当成可达产品UI。

审美持久creation-run已接通45秒租约、15秒heartbeat、epoch CAS及九阶段准备/批准/原子任务关联；运行、阶段、报告和reasoning仅本人可见，直接`/tasks`仍409拒绝。保存来源后才准备阶段，受理回执重放使用原任务，unknown不能重提交供应商；旧助手与独立创作路由不恢复。服务端先显式部署011迁移，本机业务库已完成两表及作者索引DDL。

工具/状态/模型偏好44项和原画布开关/仅cookies恢复1项已有证据；持久租约/四协议九阶段及迁移native-b27项通过，受控HTTP读取原MinIO字节。卡片服务端有效开关3项红转绿，画布Node251、统一build-b与source832/186/91通过。原审美UI art-ui-g/h/i各1项通过，最终i为67.50s、概览与详情截图已实际核验，覆盖五阶段受理后取消观察、释放租约、仅cookies恢复原run/任务、九阶段终态、再次fresh报告及原SVG定位/复制提示词；零重复供应商调用、零作品、原图不变及三方权限后置断言均执行。对应`backend/tests/integration/canvas/tools/test_canvas_art_critique_browser.py`调用`scripts/verify-art-critique-python.mjs`，需统一生产产物、隔离MySQL与`RUN_CANVAS_RESOURCE_MINIO=1`、`RUN_CANVAS_BROWSER_INTEGRATION=1`，临时端口4199。完整动态资源政策/配额保留R22；收费供应商按用户要求暂缓，不将受控供应商或构建等同完整工具迁移。

## 图片本地工具

宫格、标注和本地插值放大沿用固定源算法、布局与直接回填；上传或保存失败保留本人的完整
草稿，明确保存时复用同一节点、连线与已上传媒体。409 保留冲突草稿并等待明确加载最新版本；
离开画布、切换账号或删除来源后，不将迟到的放大结果接入图。

固定源桌面“图片工具”只提供裁切、标注与宫格；宽度小于640px时，紧凑“工具”菜单仍能进入
“调整尺寸”。该工具是本地插值，读取37×19源图后默认1K、高质量插值，输出1024×526；
保留双线性、最近邻、2K/4K选择及已达4K的禁用状态，不新增AI超分服务或桌面入口。

现行专项为 `backend/tests/integration/canvas/image/test_canvas_image_upscale_browser.py`，复用
`scripts/verify-image-grid-recovery-python.mjs` 的原上传、菜单和六类异常操作。除隔离MySQL、
`RUN_CANVAS_RESOURCE_MINIO=1`、`RUN_CANVAS_BROWSER_INTEGRATION=1` 与Chrome外，
设置 `BEEFTV_ROOT` 为只读源仓库；测试通过 `git show` 读取固定commit的插值代码，核对清单
SHA-256，再在同一Chrome生成参考图，逐像素比较真实MinIO产物。源当前HEAD无需切换。
正常流程覆盖三种算法、1K/2K/4K、取消和仅cookies新窗口恢复，异常覆盖upload_failure、commit_failure、
conflict、navigation、account_switch、source_deleted；临时端口4199，随机库/桶最终清理。
这组验证不包含收费模型、其他图片编辑工具或完整源界面视觉对照。

## 图片局部重绘

原蒙版UI专项为 `backend/tests/integration/canvas/image/test_canvas_image_mask_browser.py` 与
`scripts/verify-image-mask-python.mjs`，临时画布端口4199。除上述MySQL、MinIO和浏览器开关外，
设置 `RUN_CANVAS_BROKER_INTEGRATION=1`、`SNOWFLAKE_WORKER_ID=1022`；独立Worker使用1023。
测试复用随机namespace的真实Publisher/RabbitMQ/Celery，图片响应由受控本机HTTP门禁延迟，
用于验证关闭旧窗口后仅cookies恢复原任务。原画笔选择、持久蒙版及上游multipart逐像素/
原字节核对，任务和蒙版保持本人隔离，直接回填后的图片按项目权限共享。
局部重绘结果复用成功任务消费者绑定Worker已归档的图片，不在旧窗口重复上传产物。
原UI校验覆盖空要求、空蒙版、画笔/擦除/重置/取消及已保存模型的蒙版能力、原独立份数；
四个场景为正常1440px、关闭旧窗口后的600px恢复、不支持蒙版模型及三份独立批次。
恢复窗口先使用原“适合屏幕”，等视口稳定后再校验图片解码和全部节点入屏，适配源视口
延迟加载；不改变产品加载或批次规则。四场景与七项蒙版/三项AMQP相邻回归同版14项已通过，
Python冻结/字节/权限后置断言均执行，产品修复后画布e2e5项通过。
固定源CSS隐藏全局Toast，能力拒绝专项只证明提示已挂载和零提交，不宣称可见反馈。运行结果与
尚未完成的异常/完整视觉范围见[当前台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)。

## 图片多角度

多角度专项使用 `backend/tests/integration/canvas/image/test_canvas_image_angle_browser.py` 与
`scripts/verify-image-angle-python.mjs`，环境开关、4199端口和Worker节点与上述蒙版专项相同。
桌面从原“视角调整”菜单、600px从“工具”菜单进入。默认值/六预设、键盘、两模式拖拽、
取消及个人份数为3时强制单份均已核对；旧窗口和仅cookies恢复均绑定任务原永久产物，
不再次上传生成图片。两场景与四项蒙版/三项AMQP同版9项及画布e2e5项通过。完整异常、
成对视觉和供应商效果仍按[台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)后续核验。

## 图片打光

打光专项使用 `backend/tests/integration/canvas/image/test_canvas_image_lighting_browser.py` 与
`scripts/verify-image-lighting-python.mjs`，环境开关、4199端口和独立Worker与蒙版专项相同。
保留六光位、八风格、球面拖拽、亮度边界、光色、轮廓光、智能文字、模板现场修改和真实剪贴板。
“生成打光效果”只创建单份idle提示词节点，关闭旧窗口后可恢复；之后原“生成”按钮才提交任务。
正常1440与恢复600两场景的trace-d **2 passed / 0 skipped，85.24s**，全部Python后置断言执行，
单次提交/原图唯一上传、multipart与MinIO原字节、任务永久产物、三方权限及待执行/终态恢复通过。
final-c曾出现一次视口PUT 409，未降低失败请求断言或修改产品CAS；根因仍保留R21。
本片只调整专注/常规模式的测试入口并记录脱敏视口时序，不关闭完整视觉或收费供应商验收。

## 图片表情

表情专项使用 `backend/tests/integration/canvas/image/test_canvas_image_emotion_browser.py` 与
`scripts/verify-image-emotion-python.mjs`，环境开关、4199端口和独立Worker与蒙版专项相同。
实际加载MediaPipe JS/WASM、检测模型与3D人头，图案图片无脸时使用原手工框选；覆盖25个
情绪预设、键盘/二维拖拽、取消重开和两个人物切换，个人份数3仍只提交一份。

正常及恢复共用源局部羽化合成，以已保存的永久原图为底，上传独立派生图片并保存本人素材，
再由当前 `document.commit` 确认图与效果标记；派生上传键按账号、任务和产物槽固定。
供应商原图和两份裁切参考继续私人可见，合成作品按项目权限共享。
final-f **3 passed/0 skipped，135.24s**，正常1440px、运行中关闭窗口后600px恢复及无蒙版
能力流程全部执行Python后置断言：精确multipart/MinIO字节、320×180合成、保守人脸框外
全像素不变、来源冻结、单任务/记录/绑定与三方权限。支持蒙版时uploads=5，否则4。
两宽度默认/选定和最终恢复截图已查看；11项隔离消费者回归覆盖保存失败、来源删除、
未永久保存、任务/输入变化和账号切换，不代表真实断网或真人检测验收。
完整异常、固定源成对视觉及供应商效果继续见[台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)。

## 图片肤质

肤质专项为 `backend/tests/integration/canvas/image/test_canvas_image_portrait_texture_browser.py` 与
`scripts/verify-image-portrait-texture-python.mjs`，开关、4199端口和独立Worker同上述蒙版专项。
固定源桌面“人像调整”只显示表情，600px“工具”菜单仍能创建质感节点；创建动作只添加
空图片与一条原图引用。创作标题栏的源CSS隐藏质感参数入口，保持隐藏，不强制点击或放开。
默认值为深度融合、自然匹配、自然肤质、自然纹理、标准清晰；15选项没有作为可见UI验收。

之后原“生成”按钮才提交，沿普通图片份数规则，3张产生3个独立任务及批次子节点。
ui-d **3 passed/0 skipped，134.72s**：桌面恢复、600px仅cookies中断恢复、3份批次恢复均
执行全部Python后置断言；每场景原图上传1次，供应商POST分别1/1/3，准确提示词、multipart/
MinIO字节、原图不变及私人任务/共享产物权限通过。桌面创建先经原窄屏入口，再恢复1440px。
ui-e只补菜单截图的入场等待，正常场景 **1 passed/2 deselected，42.21s**；菜单、两宽度
默认画布和全部终态恢复截图已查看。没有产品/接口/DDL/依赖变化；完整异常、参数可见性
的源限制、成对视觉和供应商效果仍按[台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)记录。

## 共用 AI 创作助手

标准分集与画布统一使用 `frontend/src/features/assistant/panel/CreativeAssistantPanel.tsx`，直接抽取原画布助手的面板、消息气泡、Markdown 和输入区样式。画布的 `creative-assistant-sidebar.tsx` 仅管理停靠、拖宽和窄屏抽屉；不再保留另一套助手 UI 或 `/canvas-runtime/assistant/*` 请求链路。

对话按本人和项目归属，切换同项目画布不会拆分对话。自动作品上下文可移除，节点通过 `@` 显式引用；发送前必须完成模型偏好和画布远端保存，使用服务端确认的十进制版本。409 或准备期间变更会保留消息与作品草稿。附件、资产引用和个人 Skill 复用宿主功能。新 `/api/v1/assistant` 仅聊天与建议，禁止创作工具、改图、付费生成提议和助手回合撤销；节点自身生成和手动撤销保持原流程。旧 Agent 深链转为只读历史入口，已有任务与候选仍按原许可处理。

接口与协议限制见[助手 API](../../docs/api/assistant.md)，本次助手进度只更新[交接文档第 6.5 节](../../docs/plans/2026-10/2026-10-06-BeefTV画布迁移接续文档.md#65-m5画布助手工作流与最终验收)，不能据此宣称 M5 或完整画布迁移完成。共享代码使用宿主的 BigInt 版本保护，画布 TypeScript 编译目标与宿主对齐为 ES2022。

## 验证与来源

测试已按功能归档，`npm test` 和 `test:e2e` 必须追加功能或文件；用 `node ../../scripts/test.mjs --list` 查看编号，目录和级别规则见[测试规范](../../docs/instruction/按功能执行测试.md)。

- `npm test -- --feature canvas.document`：示例运行文档功能的 Node 测试。其他功能按编号选择；整体覆盖版本、路径、首次请求重试、HTTP 适配、启动顺序、个人视口/外观串行与冲突、撤权会话隔离、上传范围捕获及幂等身份合同，实际数量以当前执行结果为准。
- `npm run build`：完整画布类型检查与生产构建，包括 wasm/Worker。
- `npm run test:e2e -- --feature canvas.document`：真实浏览器与 API 夹具验证打开、拖拽、保存、视口/外观刷新恢复与撤权阻断；依赖兼容组件选择 `canvas.compat`。两份e2e及个人偏好服务器分别使用忽略的独立Vite缓存，保留原并行命令，避免与其他测试或开发进程共用预构建文件。可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 使用本机 Chromium；`CANVAS_BROWSER_BASE_URL` 可让原编辑器测试复用已启动的统一生产 preview，未设置时仍启动独立开发服务。该测试不代表 Python、媒体或供应商验收。
- Python 联调：在 `backend/` 设置隔离的 `TEST_DATABASE_URL`（库名以 `_test` 结尾，有临时建库权限）和 `RUN_CANVAS_BROWSER_INTEGRATION=1`，运行 `uv run pytest tests/integration/canvas/document/test_canvas_browser.py -q`。测试随机建库、真实登录后调用 `scripts/verify-python.mjs`，临时前端端口 4186，API 端口由系统分配，结束后清理。不拦截 HTTP；覆盖拖拽落库、刷新、外观恢复和跨窗口冲突。报告在 `.runtime/python-browser/report.json`，明确记录尚未迁移模块的真实 404；不代表媒体/供应商/助手验收。
- `npm run test:parity`：读取固定、干净的 BeefTV 源仓库，在已运行的原版开发服务与本包之间执行相同操作、截图和几何比较。默认源路径 `D:/code/BeefTV`、源 URL `http://127.0.0.1:3000`，可由 `BEEFTV_ROOT`/`BEEFTV_BASE_URL` 覆盖；目标临时端口 4184，退出后关闭。所有业务请求被测试夹具拦截，不写源工作区。截图、差异图与报告写入忽略目录 `.runtime/parity`。当前覆盖 17 个场景，包括基础画布、深浅/自定义外观、素材托盘、窄屏、历史预览和绘图；捕获前等待几何与控件颜色/阴影过渡稳定。报告差异不等于完整验收，真实媒体、生成和助手须另验。
- `npm run source:check`：核对固定源文件哈希与 `source-adaptations.json` 的显式适配记录；物理删除文件使用 `action: removed`，保留删除原因和上游哈希，检查已删除文件没有被重新引入。

资源 API、MySQL、MinIO 和真实 ffprobe/FFmpeg 样本测试继续使用隔离测试数据库与随机 `canvas-test-*` buckets，结束后清理。旧 `scripts/verify-resource-python.mjs` 包含已删除的源资产分类页与源项目库操作，整段浏览器 harness 已移出当前验收范围；过去选图、上传、登记、刷新下载及分类操作的报告只记录当时实现，不能证明当前宿主管理流程通过。GLB 字节存取不代表导演台模型验收，故障注入不代表真实断网实测。

以下旧浏览器 harness 依赖源 `/canvas-app/canvas` 项目库或 `/canvas-app/assets`，不再作为当前版本验收命令：`verify-creation-python.mjs`、`verify-creation-recovery-python.mjs`、`verify-creation-session-python.mjs`、`verify-resource-python.mjs`、`verify-recycle-python.mjs`、`verify-delete-recovery-python.mjs`、`verify-folders-python.mjs`、`verify-archive-python.mjs`、`verify-drawing-creation-python.mjs`，以及调用绘图创建旧流程的 `verify-drawing-python.mjs` 和 `compare-recycle-source.mjs`。脚本保留作历史测试资料，未改成跳过后宣称通过；对应项目/资产/回收/文件夹浏览器场景需要围绕宿主入口重新编写。独立编辑器的现行浏览器回归为 `verify-python.mjs` 和 `e2e/document/host-canvas.test.mjs`；媒体、生成和助手专项须核对自身入口后分别执行，不能用旧整站报告代替当前验收。

固定源版本的历史已知问题：`styles/workspace-product.css` 的减少动画规则把所有后代的 transition-duration 设为 1ms，会干扰 AntD 菜单同步测量。过去在 Chrome 154、1440×900、`reducedMotion=reduce` 下，源资产管理菜单实际定位到屏幕外。源资产页现已删除，该报告不属于当前宿主资产管理验收；共享画布 CSS 仍保留，上述画布对照范围也未覆盖这一旧整站菜单。

画布生成已接专用 Python `/canvas-runtime/tasks`：来源、目标和连线先真实保存，准入按本人模型与固定操作键登记任务，原版结果消费者直接绑定作品。刷新和未知回执查找同一任务，Config 批次来源独立收尾，稀疏产物保留原槽。供应商实际文本增量保存到 MySQL，通过 SSE 游标续读；不会用浏览器取消观察来取消收费任务，也不以 unknown 自动重发。媒体先检验并归档 MinIO、交付真实私人素材，原 bind 后才发布作品。完整合同及尚未支持的参数/协议见[画布 API](../../docs/api/canvases.md#画布生成任务与文本流)。

视频已增加原 OpenAI Videos multipart、NewAPI Channel 2 JSON 与官方 BeefAPI Seedance 预上传三条 Python 合同。参考资源使用永久身份和服务端真实元信息；Channel 2 本地参考保持原 HTTPS 替换弹窗，受理前失败可沿用原操作键，刷新后绑定同一真实任务。源全局 Ark 上传默认项在这些非 Ark 协议中不启动上传；未知收费提交显示 `submission_unknown`，原节点不提供重试。规范参数、原 ID 查询、30 秒轮询和有界下载恢复见[视频合同](../../docs/api/canvases.md#画布视频协议)。完整 M2 与收费供应商验收仍须继续。

生成真实联调测试位于 `backend/tests/integration/canvas/generation/test_canvas_generation_runtime.py`、`test_canvas_generation_media_runtime.py`、`test_canvas_text_stream.py`、`test_canvas_generation_browser.py`。在隔离 MySQL 环境显式设置 `RUN_CANVAS_RESOURCE_MINIO=1`、`RUN_CANVAS_BROWSER_INTEGRATION=1` 后运行对应 pytest；原 UI 浏览器脚本 `scripts/verify-generation-python.mjs` 临时使用 4188。测试采用真实执行器、存储、媒体工具及浏览器，但模型供应商为受控替身；不能作为付费供应商、真实消息队列或完整一比一验收。008 增量需要部署同版 API/Worker/scheduler 才能提供实时草稿，应用不自动建表。

视频专项为 `test_canvas_video_protocols.py`、`test_canvas_video_policy_runtime.py` 和 `test_canvas_video_options_runtime.py`，使用同一隔离 MySQL/MinIO 开关；原视频按钮的实际 UI 脚本为 `scripts/verify-video-protocols-python.mjs`，临时端口 4194。测试按原播放按钮激活播放器，核对真实 MP4 解码、刷新与全新缓存恢复，unknown 与 HTTPS 决策保留原入口和断言。此专项使用直接执行器与受控供应商，真实消息队列证据由独立 broker 测试提供，不能合并声称正式供应商验收。

音频专项为 `test_canvas_audio_protocols.py`、`test_canvas_audio_browser.py` 和
`scripts/verify-audio-protocols-python.mjs`，原UI临时端口4199。同步与async-audio支持
MP3/WAV/Opus/AAC/FLAC，容器经真实FFmpeg解码；PCM仅保留原字节、`audio/pcm`和未知
时长，原Chrome播放器的解码错误态保留。MiniMax默认音色及对应providerOptions四字段
已接入本人保存配置，未知字段沿既有400合同拒绝。options-native-b69项包含15个原UI
场景，真实播放/键盘进度、关闭旧context仅cookies恢复和Python权限/字节后置断言通过。
`test_canvas_audio_broker.py`需额外设置`RUN_CANVAS_BROKER_INTEGRATION=1`和
`SNOWFLAKE_WORKER_ID=1022`，独立Worker使用1023，随机namespace只消费audio队列。
它验证真实Publisher/RabbitMQ/Celery、同键及unknown单次create和bind前后访问边界。
并发归档404已用两协议确定时序复现并修复引用当前读；修复后音频/AMQP/素材库/账号
批次78项通过、1项既有MinIO导入已随机桶隔离补跑，完整unit/API2068项通过。
原UI69项早于该并发修复，未宣称已重做同版UI对照。收费供应商与企业授权按用户指示暂缓，
完整音频质量、其他协议/批次及完整R07仍待验收，最新证据见[剩余工作台账](../../docs/plans/2026-10/2026-10-08-BeefTV剩余工作台账.md)。

内置RunningHub三个协议与Workflow/App公开描述使用宿主`/ai_config`保存。画布恢复
稳定`logicalModelId`及保存标记，清除旧浏览器地址和积分/钱包/上传Key；就绪检查使用
宿主启用及凭据存在标记。读取公开字段后仍须保存。
专项为`backend/tests/integration/canvas/runninghub/test_canvas_runninghub_runtime.py`、
`test_canvas_runninghub_browser.py`与本包`scripts/verify-runninghub-python.mjs`。
需组合生产产物、隔离MySQL/随机MinIO桶及`RUN_CANVAS_RESOURCE_MINIO=1`、
`RUN_CANVAS_BROWSER_INTEGRATION=1`，原UI独占4199。固定源list菜单隐藏工作流创建，
专项从已有Config验证字段、先保存、仅cookies恢复原任务、真实媒体播放与unknown。
原UI使用直接执行器；独立AMQP专项见`test_canvas_runninghub_broker.py`，使用
`RUN_CANVAS_BROKER_INTEGRATION=1`和随机image/video/audio队列，经真实RabbitMQ及
独立Celery进程执行。所有供应商均为受控本机HTTP，收费供应商和企业授权按用户要求暂缓。

声明式`runninghub-workflow`使用原视频入口及本人保存的模型`default_options`，
执行独立Bearer/GET合同，不使用内置工作流选择器。全部视频先归档，仅交付第一个；
本地参考内联原字节，同源结果下载保留冻结鉴权，跨源去除鉴权。
专项`test_canvas_runninghub_declarative.py`、`test_canvas_runninghub_declarative_browser.py`
复用`scripts/verify-video-protocols-python.mjs`，原UI独占4194；环境开关同上。
接口边界见[声明式 RunningHub](../../docs/api/canvases.md#画布声明式-runninghub)。
归档与任务列表的真实MySQL并发回归见`test_canvas_media_archive_concurrency.py`；
临时数据库、随机桶、队列和Worker由专项fixture负责清理，不能改用业务数据库。

文字节点已保存图片接通 Chat Completions 与 Responses，使用本人保存的模型媒体能力与当前项目资源；
Worker按源默认manifest签发执行期MinIO URL，先 `image_url` 再 `video_url`，各组保留顺序。
Responses的配置系统提示词独立放入instructions，不能以本机受控HTTP通过声称原生供应商已接受。
未声明图片/视频能力时不自动开启。双协议图片/视频HTTP/MySQL/MinIO专项48项通过；
原UI自动引用、显式反序/重复去重/缺失拒绝及三份批量已分别验证。
份数为1–15，默认1可缺省；每份独立任务和操作键，终态直接回填。
绘图引用复用已保存版本的永久render；`test_canvas_text_drawing_browser.py` 与
`scripts/verify-text-drawing-python.mjs` 沿原右键搜索绘图、真实Excalidraw笔画和原保存入口，
双协议通过空拒绝、精确引用/零重复上传和关闭旧窗口后的预览/笔画恢复。
这些专项均采用受控供应商，不覆盖全部绘图组合或其他协议引用；仅免费 Agnes 文本/图片/视频真实验收已获授权，其他供应商协议、收费模型、真实音频与企业授权仍暂缓，结果以专项验收记录为准。
普通画布没有新增thinking UI，其他协议的多模态和工具仍按各自限制处理。
普通文字节点按源保持加载态至终态后回填，没有启用 `onTextDelta/useTextEvents`；
私人增量与 SSE 接口的恢复验证不能当作该节点的逐字展示验收。
升级前保存的绑定模型在新任务准入时刷新派生执行缓存，已提交任务保持原冻结版本。
完整范围与阶段限制见[文字引用合同](../../docs/api/canvases.md#画布文字图片引用)。

Claude与Gemini普通纯文字分别使用Python `claude_messages.v1`、`gemini_generate_content.v1`。
Claude使用本人保存的 `api_format=claude / protocol=claude-api`，支持正文流和非流；
Gemini普通任务使用源可达的 `api_format=openai / protocol=gemini-generate-content` 非流路径，
`api_format=gemini` 的custom gate仍明确拒绝。reasoning/thought只进本人任务记录，
普通节点终态只共享正文。原UI恢复已经实际验证，其他引用/工具不由纯文字通过推断。
详细限制见[Claude合同](../../docs/api/canvases.md#画布-claude-普通文字与图片引用)与
[Gemini合同](../../docs/api/canvases.md#画布-gemini-普通纯文字)。

图片裁切原 UI 专项位于 `backend/tests/integration/canvas/image/test_canvas_image_tools_browser.py`，
对应 `scripts/verify-image-tools-python.mjs`，临时端口 4199；需同时设置上述 MinIO 与浏览器开关。
该片已完成原上传/inline 裁切、取消、派生图、刷新、两处 503 草稿恢复、真实字节与权限验收；
裁切默认选区的奇数图像素导出沿源 ceil 算法，不能用 UI round 标签替代真实输出尺寸。
切格和标注各自验收，不用裁切通过代表整个图片工具完成。

模型管理统一进入宿主 `/ai_config`；`/canvas-app/settings` 兼容深链直接转宿主，不再渲染 BeefTV 设置页。画布模型列表只读本人宿主配置及完整运行 profile，旧 `channel::model` 别名恢复原稳定 ID，明确失效的选择提示不可用。画布偏好 PUT 只提交个人默认与生成参数，任何带 channels 的旧写入明确拒绝。进入配置前等待偏好 ACK 和作品远端保存，失败或 409 保留草稿并阻止离开；宿主返回只接受具体同源画布路径。

宿主“测试模型”复用独立 `/canvas-runtime/model-tests`，不依赖画布或虚构节点，固定操作键重放未知回执，只有实际任务结果才成功。结果媒体保持本人权限；普通停用配置不能借测试标记执行。旧渠道绑定保留作选择别名；新增宿主运行 profile、加密扩展凭据及绑定迁移标记，升级见[统一模型迁移](../../docs/database/migrations/2026-10-06-host-model-runtime/README.md)。当前完整 SQL 为 93 表，部署必须同步 API/Worker/scheduler，旧 Worker 不能解析新的标准 `model_auth_*` 或画布凭据信封。完整接口见[画布 API](../../docs/api/canvases.md#独立模型测试)。

BeefAPI 连接、状态和钱包入口统一在宿主提供。托管目录的地址/profile/模型身份由服务端维护，名称、启停及本人 headers 可保存并脱敏恢复；普通 CRUD 不能伪造托管凭据。授权由 scheduler 后台继续，关闭页面不会取消。真实企业登录与收费供应商仍需单独验收。本轮仅完成模型管理统一，项目、资产和其余整站入口的收敛仍是后续切片。

资源持久化固定使用 Python API；`isLocalWorkspaceMode` 仍保留源 UI 功能分支，IndexedDB 只用于草稿和缓存。不能切回浏览器本地资源存储来绕过缺失服务，否则选图可能仅在本机显示而未写入服务器。

`scripts/import-source.mjs` 用于固定基线首次迁入，不要在已经适配的目录强制重导覆盖；它明确拒绝重新写入已登记物理删除的文件。每次确需修改源文件，要核对实际差异并通过 `scripts/check-source.mjs --record <文件> <原因>` 更新记录；明确删除后使用 `--record-removal <文件> <原因>`，不得删掉固定来源清单来掩盖变化。哈希通过只证明差异可追踪，不证明交互与视觉一致。新增适配文件、测试与独立路由不属于上游文件哈希清单。

统一静态产物的完整生产部署验收、完整资源服务、模型执行、助手和原版逐项视觉/操作对照仍需完成。共同依赖调整后的视觉和行为要重新对照，不能沿用调整前的截图或构建结果。最终一比一判定以[迁移方案](../../docs/plans/2026-10/2026-10-05-BeefTV无限画布一比一迁移方案.md)的验收门禁为准。

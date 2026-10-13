# 标准模式创作流程实施验收记录

日期：2026-10-13 起持续更新，最后核对：2026-10-14。状态：**实现已进入验收，浏览器终验尚未完成**。

本记录对应[标准模式创作流程实施计划](2026-10-09-standard-creation-workflow-implementation.md)。它记录当前工作区中可以追溯到源码、测试、截图或命令输出的结果；接口夹具、Node 测试和本地数据库测试不等于真实模型、Worker 或视频供应商验收。工作区保留已有未提交改动，本轮没有执行 commit、reset 或清理。

## 1. 实施范围与当前交付物

标准分集入口已经接入四阶段顶部导航、满高工作区、小说改编、Quill 2.0.3 正文、素材四分类、分镜列表和独立 AI 助手。正文同时保存纯文本与受限 Quill Delta；素材、分镜图、分镜视频和小说改编分别保存本人草稿，生成成功后仍需要明确采用。生成请求保留幂等键、来源版本、上下文校验和 `unknown` 只读核对。

本轮新增或变更的持久化结构包括：

- `shot_scripts.title`、`shot_scripts.shot_size`；
- `episode_scripts.editor_document`；
- `user_creation_drafts` 及其范围唯一键、外键、JSON 和版本约束。

对应 ORM、Pydantic、Service、DAO、前端 DTO、完整 schema、旧库增量迁移和维护说明已经在工作区。`docs/数据库模型/migrations/2026-10-13-standard-creation-workflow/README.md` 记录了 2026-10-13 在当前本机 Docker MySQL `127.0.0.1:13306` 的 `short_drama` 库执行上述 DDL，以及幂等复核；本记录沿用该执行记录，不把测试替身当作生产数据库验收。

## 2. 已实际执行的验证

| 范围 | 命令或证据 | 实际结果 | 边界 |
| --- | --- | --- | --- |
| 后端标准契约 | `backend/.venv/Scripts/python.exe -m pytest tests/unit/test_standard_creation_contracts.py tests/unit/test_user_creation_drafts.py tests/api/test_standard_creation_contracts_api.py -q -p no:cacheprovider --basetemp=.runtime/standard-contracts-verification` | **47 passed in 18.77s** | SQLite/内存测试夹具；不调用真实模型或生产 Worker |
| 后端完整 unit/API | 并行 agent 报告的 `uv run pytest tests/unit tests/api -q` | **3118 passed，2 warnings** | 该结果来自并行 agent 的实际运行报告；需在最终日志中保留命令和警告详情 |
| Python 静态检查 | `backend/.venv/Scripts/python.exe -m ruff check src tests scripts` | **All checks passed** | 静态检查 |
| Python 格式检查 | `backend/.venv/Scripts/python.exe -m ruff format --check src tests scripts` | **856 files already formatted** | 格式检查 |
| 前端 Node 测试 | `frontend/npm.cmd test` | **254 passed，0 failed，0 skipped** | Node 测试和 HTTP/模型夹具，不代表浏览器或供应商验收 |
| 前端类型检查 | `frontend/npm.cmd run typecheck` | **通过** | 独立类型检查 |
| 前端生产构建 | `frontend/npm.cmd run build` | **通过**；标准包 4473 modules，画布包 14194 modules，生成 `dist/` 与 `dist/canvas-app/`；仅有动态 import/大 chunk 提示 | 构建通过不代表真实模型、Worker 或视频供应商验收；构建产物不作为交付源码 |
| 标准流程浏览器 | `frontend/npm.cmd run test:e2e -- e2e/standard-creation-workflow.spec.ts` | 默认浏览器缺少 `chromium_headless_shell-1243`，未启动测试；指定本机 Chrome 后仍未形成通过结果：桌面断言期望 53px、实测 `52.99366760253906px`，图片/视频 `unknown` 刷新核对各 30s 超时，套件随后中断 | 需要修正断言/恢复超时后重新运行；当前不能写成 E2E 通过 |
| 标准流程截图 | `frontend/.impeccable/review/` 下 `source-*`、`assets-*`、`storyboard-*` 及 assistant/settings 截图 | 六个尺寸的截图文件存在，时间集中在 2026-10-13 22:23–22:24 | 截图只证明当时捕获了界面，不能替代未通过的浏览器用例；需在最终修复后复核 |
| 数据库 DDL | `docs/数据库模型/migrations/2026-10-13-standard-creation-workflow/README.md` 与 `backend/scripts/standard_creation_ddl.py` | 记录为已在 `short_drama@127.0.0.1:13306` 执行并重入核对 | 本记录未再次读取配置或重执行 DDL |

尚未在本记录截止时宣称通过的命令：`frontend/npm.cmd run build`、完整标准流程 Playwright 复验、相关恢复类 Playwright 全套、`backend/scripts/run_integration.py` 随机 MySQL 集成测试，以及真实模型/Worker/视频供应商调用。

## 3. P0–P7 批次状态

| 批次 | 当前状态 | 证据与未完成项 |
| --- | --- | --- |
| P0 契约与迁移 | **实现完成，集成复核待完成** | 47 个标准契约测试通过；ORM、完整 SQL、增量迁移和 DDL 脚本已存在，DDL 执行记录已写入迁移 README；仍需最终随机 MySQL 集成命令和 schema 对照留档 |
| P1 工作区骨架 | **实现完成，浏览器终验待完成** | `EpisodePage`、顶部步骤、工作区和助手代码已接入；六尺寸截图已捕获；标准 E2E 的 53px 精确断言尚未通过 |
| P2 小说改编 | **实现完成，浏览器终验待完成** | Quill、原文抽屉、候选预览/采用和正文保存代码及 Node/后端契约已覆盖；`novel-quill.spec.ts` 尚未形成浏览器通过结果 |
| P3 生成输入 | **实现完成，浏览器终验待完成** | `GenerationComposer`、参考图、个人参数草稿和 unknown 只读核对已实现；Node 与后端契约通过，标准 E2E 仍受浏览器问题阻塞 |
| P4 素材工作区 | **实现完成，浏览器终验待完成** | 四分类即时新增、上传等待、临时草稿恢复和素材生成代码/测试已存在；`standard-assets-workflow.spec.ts` 尚未形成通过报告 |
| P5 分镜工作区 | **实现完成，浏览器终验待完成** | 镜头名称/景别、分页、图/视频候选、自动保存和冲突保护已实现；后端契约通过，标准 E2E 有精确尺寸失败和 unknown 超时 |
| P6 助手与适配 | **实现完成，浏览器终验待完成** | 助手独立栏、引用、阶段/对象隔离和旧恢复逻辑保留；需在标准流程及既有助手恢复场景重新运行后定案 |
| P7 回归与交付 | **进行中** | 已有静态、Node、后端契约结果；待完成生产构建、Playwright 复验、集成测试、最终 diff 检查和本记录更新 |

## 4. C1–C5 契约状态

| 契约 | 状态 | 已核对内容 | 尚缺验证 |
| --- | --- | --- | --- |
| C1 空白卡片与有效素材创建 | **代码/用例完成，浏览器待验** | 素材新建不弹窗、临时卡片、名称/类型门槛、原幂等键恢复；标准素材测试文件含正常、受理不明、上传等待和损坏恢复场景 | Playwright 需在浏览器可用且无超时后通过 |
| C2 素材类型修改 | **后端契约通过** | 合法转换、共享确认、版本冲突、镜头关联保留及角色声音/对白依赖阻止转换；纳入 47 个测试 | 随机 MySQL 集成和前端实际保存路径 |
| C3 镜头名称与景别 | **后端契约通过** | 创建、版本化修改、读取、空景别、长度/枚举校验；纳入 47 个测试 | 完整集成迁移复核和浏览器分页/深链 |
| C4 Quill 格式与纯文本正文 | **后端/Node 通过，浏览器待验** | Delta 白名单、纯文本一致性、格式-only 版本、旧客户端清除 Delta、撤销/重做会话测试 | `novel-quill.spec.ts` 浏览器全场景及生产构建 |
| C5 个人生成草稿 | **后端/Node 通过，浏览器待验** | 本人/项目/分集/对象/用途隔离、十进制 ID、草稿版本冲突、图片 16/17 张边界、视频来源冻结；纳入 47 个后端测试及前端生成会话测试 | 随机 MySQL、完整浏览器生成/刷新/采用路径；不验真实模型质量 |

## 5. A01–A22 交互验收矩阵

以下状态严格区分“代码/测试已覆盖”和“浏览器实际通过”。标准流程套件当前未完成，因此不能把截图或测试文件存在写成 A 项通过。

| 编号 | 当前状态 | 说明 |
| --- | --- | --- |
| A01 | **待修复/复验** | 顶部步骤与满高工作区已实现；桌面测试因 53px 与浮点实测值断言失败 |
| A02 | **待浏览器复验** | 左列表独立滚动、稳定对象和选择逻辑已有代码/用例 |
| A03 | **待浏览器复验** | 当前/候选/待制作状态已有组件逻辑；未取得完整套件通过结果 |
| A04 | **待浏览器复验** | 素材四分类和直接新增由 `standard-assets-workflow.spec.ts` 覆盖 |
| A05 | **待浏览器复验** | 原键恢复、失败保稿和本机损坏记录有素材/生成测试覆盖 |
| A06 | **待浏览器复验** | 素材新增/提取/导入及分镜新增/提取入口已在页面代码中 |
| A07 | **待浏览器复验** | 小说设置布局和原文抽屉已实现；需复验 180px 和键盘顺序 |
| A08 | **待浏览器复验** | 源页无聚焦描边规则已加入；需实际点击核对 |
| A09 | **待浏览器复验** | Node/后端已验证 Delta 与纯文本会话；Quill 浏览器场景待通过 |
| A10 | **待浏览器复验** | 候选只读预览与明确采用逻辑有 `novel-quill.spec.ts` 覆盖 |
| A11 | **待浏览器复验** | 生成输入组件和参数隔离已实现；需检查紧凑布局 |
| A12 | **待浏览器复验** | 素材详情名称/类型/描述布局已实现；需截图复核 |
| A13 | **待浏览器复验** | 分镜名称/脚本/景别显示已实现；需截图复核 |
| A14 | **后端/Node 通过，浏览器待验** | 图/视频私人参数和共享字段隔离由契约测试覆盖，实际界面仍待复验 |
| A15 | **后端/Node 通过，浏览器待验** | 串行保存、503/409 保稿和迟到响应有测试覆盖 |
| A16 | **待浏览器复验** | 对象/阶段/账号隔离代码和会话测试已有，需标准浏览器场景通过 |
| A17 | **后端/Node 通过，浏览器待验** | 4000/4001、16/17、版本/权限和 unknown 逻辑已有覆盖；标准浏览器 unknown 场景当前超时 |
| A18 | **待浏览器复验** | 候选采用、封面更新、历史隔离及过期 URL 需完整浏览器复验 |
| A19 | **待浏览器复验** | 助手开合与输入保留已有标准用例，但套件未完成 |
| A20 | **待浏览器复验** | loading/empty/error/缺图组件状态已有实现，需逐区域运行 |
| A21 | **待浏览器复验** | 分页/深链/列表外对象已有代码和恢复测试，需 20/21/80 镜浏览器复验 |
| A22 | **待浏览器复验** | 390px 截图已捕获，设置抽屉/助手互斥和焦点恢复尚未取得完整通过报告 |

## 6. 视觉证据与待办

标准流程截图位于 [`frontend/.impeccable/review`](../../frontend/.impeccable/review/)：

- `source-*x*.png`、`assets-*x*.png`、`storyboard-*x*.png` 覆盖 390×844、1024×768、1280×800、1440×900、1920×1080、1440×600；
- `*-assistant-*` 覆盖助手打开状态；`assets-settings-390.png`、`storyboard-settings-390.png` 覆盖窄屏设置抽屉。

截图中需要最终人工复核的重点为分镜中央预览高度、卡片名称/景别同一行、卡片固定高度、设置抽屉收起入口，以及图片/视频候选采用后是否回到当前作品。截图不能掩盖当前 Playwright 的浮点尺寸失败和 unknown 请求恢复超时。

Impeccable 既有 `frontend/.impeccable/review/detector.txt` 记录的是一次不可用引擎尝试；本轮不将其写成通过，也不重复运行 detector。最终视觉结论应在代码修复和标准流程浏览器复验后追加。

## 7. 未验收边界与完成条件

以下内容在当前记录中明确保持未验收：

- 真实付费/外部模型的生成质量、计费、限流和长耗时行为；
- 真实 Text/Image/Video Worker、RabbitMQ、MinIO 生产连接；
- 真实视频供应商返回的视频质量、编码和播放兼容性；
- 随机临时 MySQL 集成脚本最终一轮及其迁移对照（DDL 执行记录本身已保存）；
- `frontend/npm.cmd run build` 和标准流程/恢复类 Playwright 的最终绿轮。

完成本记录前仍需：修正 53px 浮点断言或对应布局、解决 unknown 图片/视频刷新核对超时，在指定六个视口重新运行标准流程和相关恢复测试；执行前端生产构建、后端集成验证及 `git diff --check`；再更新 P7、A01–A22 和截图结论。只有这些结果齐全后，才可将整套 P0–P7 标记为完成。

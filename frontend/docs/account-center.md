# 共享账号中心

更新于 2026-10-02。Mode: Operate。本次修正用户截图中占据左侧独立栏的账号名称与退出入口：账号操作统一从当前页面的右侧页头进入，共享账号中心在原页面上打开。

## 与既有系统的比较结果

**结论：本次属于既有工作台的功能扩展与界面细化，当前账号实现延续原系统，无需重写设计规范。** 已对照 [DESIGN.md](../DESIGN.md)、[设计扩展](../.impeccable/design.json)、[PRODUCT.md](../PRODUCT.md)、[表面方向契约](ui-redesign-surface.md) 以及 [tokens.css](../src/app/tokens.css)、[theme.ts](../src/app/theme.ts)、[workbench.css](../src/app/workbench.css) 与 [identity.css](../src/app/identity.css)。

| 系统要求 | 当前账号实现 |
| --- | --- |
| 冷黑画布、冷灰表面、蓝紫操作色与独立语义色 | 页头入口与账号面板使用现有表面、文字、边线及成功、警告、危险角色；Ant Design 控件继续使用暗色主题，邮箱验证与嵌入重新登录表单的启用主按钮局部指定既有填充与前景 token，保证实际文字对比度。 |
| 系统中文字体与紧凑层级 | 沿用既有字体；面板标题为 16px，身份名称为局部强调的 20px，信息行为 13px，辅助说明为 12px。不增加展示字体或全局字号。 |
| 控件圆角、面板圆角与结构性浮层 | 入口沿用 34px 桌面高度和现有控件形状；面板沿用 Dialog 的表面、边线、遮罩、阴影与左侧面板圆角。 |
| The Readable Accent Rule / The Task Hierarchy Rule / The Quiet Surface Rule | 可见焦点与文字状态说明结果，身份行通过标签与分隔组织，阴影留给操作浮层，编辑内容保留原空间。 |
| 局部滚动、固定操作区、缩窄时重排 | 面板正文独立滚动，标题与页脚固定；详情与分集页头在窄屏重排，账号入口保持可达。 |

本次没有更改调色板、字体、共享 token 或视觉身份，也没有新增交付位图。账号面板的宽度、信息行与安全表单是该功能的局部布局；本记录不将它们提升为全局规范。DESIGN、sidecar、PRODUCT 与表面方向契约保持原样。

### 启用主按钮的局部颜色与对比度

[identity.css](../src/app/identity.css) 对共享邮箱验证表单和嵌入重新登录表单的启用主按钮指定现有颜色角色：默认与按下状态使用 `accent-fill`，悬停与可见键盘焦点使用 `accent-hover`，四种状态的文字均使用 `on-accent`。规则限定在这两类表单并排除禁用按钮；公开邮箱验证与找回密码页面也复用同一表单样式。其余主题、焦点规则、布局及恢复逻辑保持原实现。

[button-contrast.ts](../e2e/button-contrast.ts) 在默认、悬停、键盘焦点和按下状态读取实际计算后的前景与背景，检查文字对比度至少 4.5:1。账号密码错误与嵌入重新登录错误两个流程各在 1440 / 390px 取证，共四份报告、16 个状态：[contrast-results.json](../.impeccable/review/account-center/fix1/contrast-results.json)。默认及按下为 5.733:1，悬停及键盘焦点为 5.355:1，最小值为 5.355:1。颜色对应既有 token；这组证据只证明两项流程的启用按钮状态，不等于全工作台控件的对比度审计。

## 挂载与入口

[App.tsx](../src/app/App.tsx) 的 `StudioLayout` 只挂载一个 [AccountCenterProvider](../src/features/auth/AccountCenter.tsx)。Provider 包住工作区，并将账号浮层渲染为工作区根节点外的兄弟节点；账号入口不再成为 `.studio` 的直接子级，也不占据根布局的一列。打开和关闭面板只改变 Provider 的状态，不导航、不替换当前编辑器。

[AccountControls.tsx](../src/features/auth/AccountControls.tsx) 只承担共享入口。认证启用且存在当前用户时，按钮提供“账号中心：完整身份名称”的可访问名称、弹窗提示与展开状态；认证关闭时不显示账号按钮。

| 页面或状态 | 入口位置 |
| --- | --- |
| 项目管理、三类素材、四类任务、两类媒体、AI 配置 | 通用工作区顶栏右侧。 |
| 项目详情 | [ProjectDetailHeader.tsx](../src/features/projects/ProjectDetailHeader.tsx) 的右侧操作组，与关闭项目操作相邻。 |
| 项目数据载入、项目请求失败 | [ProjectRoute.tsx](../src/pages/projects/ProjectRoute.tsx) 继续渲染详情页头；载入和“项目无法打开”状态均保留账号入口及返回项目入口。 |
| 小说改编、素材准备、分镜制作、成片合成与导出 | [EpisodePage.tsx](../src/pages/projects/EpisodePage.tsx) 的分集页头右侧；内容或成片编辑包载入时页头仍在。 |
| 专注剪辑 | 外围分集页头被既有专注模式隐藏；[AssemblyStage.tsx](../src/pages/projects/episode/AssemblyStage.tsx) 在可见的成片工具栏提供同一个共享入口。账号浮层不受专注区域隐藏规则影响。 |

专注剪辑中关闭账号面板会回到该工具栏入口，保留专注状态。这里的加载可达性指已渲染的通用外壳、项目数据请求及分集阶段加载；本次浏览器用例没有模拟详情路由代码包下载失败。

## 只读身份信息

数据来自 [AuthSession.tsx](../src/features/auth/AuthSession.tsx) 中当前用户，读取接口为 `GET /auth/me`。账号中心完整显示显示名称（为空时回退到账号名）、账号名、账号 ID、注册邮箱及邮箱验证状态。显示名称、账号名和邮箱在面板内允许换行；页头短入口的名称可省略，完整身份在面板中读取。

邮箱存在时，以“已验证”或“未验证”文字配合既有语义颜色表达状态。邮箱缺失时显示“未设置邮箱”，说明无法通过邮箱修改密码并禁用该操作，不提供虚构邮箱或绑定操作。信息列表显式使用块级外层，再为每行排列标签与值，避免继承全局 `dl` 布局而压缩内容。

当前后端没有修改显示名称、账号名或邮箱的资料更新接口，因此这些值均为只读，没有保存或编辑资料操作。接口与字段边界已核对 [auth.py](../../backend/src/short_drama/api/v1/auth.py)、[identity.py](../../backend/src/short_drama/schemas/identity.py) 与 [auth_service.py](../../backend/src/short_drama/service/auth_service.py)；本次未改后端。

## 密码更新、重新登录与草稿

“修改密码”展开 [EmailProofForm.tsx](../src/features/auth/EmailProofForm.tsx)，锁定当前注册邮箱。表单调用现有 `POST /auth/password/request` 获取 challenge，再以 challenge、六位邮箱验证码和新密码调用 `POST /auth/password/reset`。新密码长度为 12–128 位，发送成功后保留 60 秒重发等待；提示验证码十分钟内有效，并使用不泄露邮箱是否存在的发送说明。

发送失败或验证码无效、过期时就近显示错误，保留已填写的邮箱、验证码和新密码。处理中禁用重复提交与相关操作，并阻止关闭账号面板；“取消修改”或正常关闭面板会结束本次密码表单。

后端密码重置成功会撤销该用户的所有登录会话。账号中心成功回调立即关闭面板并触发现有 `session-expired` 事件，显示“重新登录，继续创作”弹窗；不等待下一次业务请求失败才恢复认证。原用户与编辑页面继续挂载，用户使用同一账号的新密码在原位登录；登录失败保留密码输入与页面草稿，成功后移除恢复弹窗并回到编辑。

这条保留路径针对当前仍挂载的编辑页面，不构成刷新、关闭浏览器或成功退出后的草稿保留承诺。浏览器用例明确检查了未保存的项目名称在打开/关闭账号中心、密码更新与重新登录前后的保留；没有逐一证明所有制作阶段的每种草稿字段。

公开的“验证注册邮箱”与“忘记密码”页面由 [AccountPages.tsx](../src/features/auth/AccountPages.tsx) 复用同一表单，分别调用现有邮箱验证与密码重置接口。公开流程成功后显示结果与返回登录入口；账号中心内的成功回调负责原位恢复认证。

## 确认退出与失败恢复

退出入口位于账号中心固定页脚。点击后调用现有 [confirm.tsx](../src/components/ui/confirm.tsx)，说明退出前需确认创作内容已保存，默认聚焦取消。取消不发起退出请求，当前编辑与账号保持原状态。

确认后等待 `POST /auth/logout` 完成；[AuthSession.tsx](../src/features/auth/AuthSession.tsx) 在请求成功后才清理用户与会话状态，随后由认证门返回登录页。请求失败时保留前端当前会话和编辑页面，显示“退出登录未完成”、可读错误及“返回创作”；用户可返回后重新打开账号中心再确认退出。失败弹窗关闭也可回到仍打开的账号中心。浏览器验证覆盖取消、失败、返回及重试成功，这些结果来自 fixture，不证明网络中断后真实服务端会话的最终状态。

## 响应式与键盘行为

| 条件 | 当前规则 |
| --- | --- |
| 桌面入口 | 最大宽度 200px，显示图标、可省略的名称与展开指示；默认最小高度 34px。 |
| ≤600px | 入口改为 44 × 44px，隐藏可视名称与展开指示，仍保留完整可访问名称；账号按钮和表单操作至少 44px，输入使用 16px 字号，面板去除圆角。 |
| ≤680px 的详情与分集页头 | 返回与右侧账号操作排在首行，标题/分集信息另行排列；分集保留独立保存状态，页头高度为 112px。 |
| 粗指针 | 账号入口及面板操作至少 44px；验证码表单输入使用 16px 字号。 |
| 账号面板 | 靠右、高度 100dvh，宽度为视口与 440px 中的较小值；320 / 390px 实测为全宽。正文使用独立纵向滚动，标题、关闭入口与退出/返回页脚保持在滚动区外。 |

身份行使用 72px 标签列、16px 列间距与可收缩的值列，长身份可自然换行。移动正文左右留白为 20px，桌面为 22px；手机长名称与长邮箱不会强制撑宽页面。

面板复用 [Dialog.tsx](../src/components/ui/Dialog.tsx) 的原生模态行为、可见控件 Tab 循环、Escape 关闭、中文标题及关闭按钮，关闭后恢复仍存在的入口焦点。提交期间按业务状态阻止关闭。专注剪辑中的 Escape 关闭账号中心后保留专注模式。全局可见焦点和减少动态效果偏好继续由既有样式及 `StudioProvider` / `useReducedMotion` 管理，本次没有另建行为体系。

## 验证与证据边界

当前 [validation.json](../.impeccable/review/account-center/validation.json) 与日志记录如下。本记录读取现有结果，没有另起浏览器运行。

| 验证 | 已记录结果与证据 |
| --- | --- |
| 账号与协作浏览器用例 | 当前 CSS 在[修复运行日志](../.runtime/account-center-fix1.log)中通过 24 项；两项重新登录对比度检查最初因加载图标参与可访问名称而未定位到按钮，修正测试定位器后在[重跑日志](../.runtime/account-center-fix1-retry.log)中通过，两次之间没有 UI 变更。合计仍为同一组 26 个独立用例，保留原密码错误、输入与草稿恢复检查，并新增四状态对比度断言。 |
| 工作区回归 | 保留既有 23 项通过结果：[工作区日志](../.runtime/account-center-workspace.log)。与账号/协作合计仍为 49 个独立用例，重跑不增加用例数。 |
| 单元验证 | 既有 124 项通过：[单元日志](../.runtime/account-center-unit.log)。本次仅修正局部 CSS 与取证测试，业务逻辑未改，未重复运行这组单元测试。 |
| TypeScript / 生产构建 | 当前均通过，结果记录于 validation.json 的 `review_fix1`；[当前构建日志](../.runtime/account-center-fix1-build.log)保留生产构建输出，构建后未更改 UI 源码。 |
| 源码对应 | [source-fingerprints.json](../.impeccable/review/account-center/source-fingerprints.json) 中 13 个实现与测试文件的当前 SHA-256 均与记录一致，包含新增的 `e2e/button-contrast.ts`。 |

截图索引共有 48 张：[final/index.json](../.impeccable/review/account-center/final/index.json)。项目概览、项目详情、四个分集阶段与专注剪辑账号面板覆盖 2048、1440、768、390、320px；2048px 用例的实际视口为 2048 × 1070，其余该宽度矩阵用例为 900px 高。完整页面截图可能超过视口高度。长身份、密码错误、退出失败及会话恢复另外覆盖 1440 / 390px；项目请求失败另有专门截图。其他素材、任务、媒体与 AI 区域通过共享顶栏进行功能验证，不代表这些区域都有完整的五宽截图组合。

初次文档比较查看了用户原始反馈图、当时的四张最终 contact sheet，并抽查了 1440 / 320px 专注剪辑账号面板、390px 长身份及密码错误的原始截图。错误表单截图有意滚动正文以露出错误与提交操作，固定标题和页脚仍在。用户反馈图中的真实项目与当前截图中的“雨夜来信”、admin 账号等 fixture 明确区分。

此前两张协作截图按新的页头位置断言重拍并替换原路径，索引仍为 48 张，contact sheet 同步重建；文档比较另行查看了当时更新后的第 4 张 contact sheet 及这两张 1440 / 390px 原始截图。重拍的 2 项重复既有用例，不计入额外的独立用例数，UI 源码未因那次取证修正改变。

本次局部对比度修复后，同一 48 张索引路径按原视口重新取证，四张 contact sheet 已重建，证据矩阵不变。文档的聚焦复查读取了 [fix1-packet.md](../.impeccable/review/account-center/fix1-packet.md)、当前身份样式、对比度 helper、两个受影响测试流程、四份渲染报告、当前日志及全部 13 个指纹；未重新审阅整个截图集合，也未运行浏览器或 Detector。

所有浏览器 API、模型与生成调用均由 fixture 拦截；没有验证真实后端集成、邮件送达或付费生成。Context / Detector 因本会话既有启动器受缓存与网络限制而不可用，未运行，也未重试。独立的新账号完整评审保存在 [full-review.md](../.impeccable/review/account-center/full-review.md)，只列出一项主操作文字对比度问题；该项修复已由 [account-center-finish-review.md](account-center-finish-review.md) 判定为 `resolved`，最终 disposition 为 `ship`，结论仅覆盖所列对比度修复。本记录不提供终审评分，不沿用前轮工作台评审的评分或发布结论，也不把这次修复判定扩大为新的全工作台认证。

## 保留的历史漂移

已观察到 DESIGN 的 `spacing.section-gap` 记为 16px，而当前 `tokens.css` 的 `--space-section` 为 24px；这是既有记录与代码的差异，本次账号实现未更改两者。DESIGN 的 Layout 验证范围及 sidecar 的断点清单仍为较早的桌面快照，没有收录后来细化的全部手机规则。本记录以实际源码说明账号行为，不借此重写共享规范或删除历史证据。

本次只更新 [ui-refinement.md](ui-refinement.md) 中过时的 AccountControls 组件说明，使其指向共享入口、Provider 与账号中心。该文档的其他历史陈述、既有产品范围和方向契约保持原样；未将既有漂移或本次验证范围写成新的全局规则。

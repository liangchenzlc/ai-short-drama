# 工作台界面细化记录

更新于 2026-10-02。本轮采用 Operate 模式，在既有冷黑、蓝紫工作台中细化视觉、交互、复用和响应式布局。视觉权威仍为 [DESIGN.md](../DESIGN.md)、[设计扩展](../.impeccable/design.json) 与实际源码；表面方向见 [ui-redesign-surface.md](ui-redesign-surface.md)。

## 保留的系统

主操作继续使用 `accent-fill`，文字与焦点使用 `accent`，当前选择使用 `pale` / `accent-border`；冷黑画布、冷灰表面及成功、警告、错误角色保持原值。颜色维护入口是 [tokens.css](../src/app/tokens.css) 与 [theme.ts](../src/app/theme.ts)，Ant Design 实际颜色继续由暗色算法派生。

系统中文字体、26px 页面标题、21px 阶段标题、13px 界面正文、14px 长文编辑与宽松行距保持既有层级。桌面控件保留 34px 密度、7px 控件圆角和 12px 面板圆角。内容按明度、边线和留白分组，浮层承担结构性阴影；不新增展示字体、装饰资产或视觉身份。

## 共享组件与维护入口

| 入口 | 用途与边界 |
| --- | --- |
| [Workspace.tsx](../src/components/ui/Workspace.tsx) | `PageHeader` 统一标题、说明与操作；`ListToolbar` 统一数量、提示与列表动作；`FilterPanel` 保留原生 `details` / `summary` 与受控展开状态。已用于项目、素材、任务、资产和 AI 配置相关页面；业务筛选、请求与恢复动作仍由页面管理。 |
| [StudioProvider.tsx](../src/components/ui/StudioProvider.tsx) | 应用根与独立确认根共享 Ant Design 暗色主题、中文 locale、按钮文字规则和动态偏好。入口分别为 [main.tsx](../src/main.tsx) 与 [confirm.tsx](../src/components/ui/confirm.tsx)。 |
| [AccountControls.tsx](../src/features/auth/AccountControls.tsx) | 顶栏、项目/分集页头与专注剪辑工具栏复用单一账号入口；[AccountCenterProvider / AccountCenter.tsx](../src/features/auth/AccountCenter.tsx) 统一只读身份、邮箱验证修改密码、退出确认与失败恢复，挂载在工作区根节点外。会话与原位重新登录由 [AuthSession.tsx](../src/features/auth/AuthSession.tsx) 维护，完整行为及验证边界见 [account-center.md](account-center.md)。 |
| [confirm.tsx](../src/components/ui/confirm.tsx) / [Dialog.tsx](../src/components/ui/Dialog.tsx) | 确认说明对象与影响，默认聚焦取消，重复触发不会创建第二次操作。弹窗负责 Tab 循环、关闭规则、内部下拉挂载和返回入口焦点。 |

[Sidebar.tsx](../src/components/layout/Sidebar.tsx) 的页面入口使用真实路由链接并保留 `aria-current`；素材分类展开仍使用按钮及 `aria-expanded`。项目资源与协作区域跨详情网格全部列；素材卡的图片、描述、动作和批量选择保持明确位置。素材与分镜复用 [BatchGeneration.tsx](../src/features/generations/BatchGeneration.tsx) 的已加载项选择，部分选中使用 `indeterminate`，与整范围预检保持不同入口。

## 偏好与运动

[useReducedMotion.ts](../src/components/ui/useReducedMotion.ts) 订阅系统 `prefers-reduced-motion`，偏好变化会更新 `StudioProvider` 的 Ant Design `motion` 开关。原生弹窗和抽屉关闭入场动画，展开箭头关闭旋转过渡，滚动改为即时；原生颜色与边线反馈继续保留。具体原生规则位于 [workbench.css](../src/app/workbench.css)、[web.css](../src/app/web.css) 和 [production.css](../src/app/production.css)。本轮同步了 DESIGN 与 sidecar 中原有的全局 0.01ms 描述和示例。

模型选择偏好继续由现有 [ModelPreferences.tsx](../src/features/auth/ModelPreferences.tsx) 管理；成片布局偏好继续由 [useAssemblyLayout.ts](../src/features/projects/useAssemblyLayout.ts) 管理。通用页面外壳不承接这两类业务状态。

## 响应式与触控

布局最终覆盖层位于 [workbench.css](../src/app/workbench.css)，基础规则位于 [web.css](../src/app/web.css)；导入顺序见 [main.tsx](../src/main.tsx)。修改断点时需同时检查窄屏与桌面，避免后加载样式恢复被压缩的列。

| 条件 | 当前规则 |
| --- | --- |
| 681–900px | 保留纵向侧栏；项目列表与继续创作区域改为上下排列。上下文按剩余宽度自动适配，最小列宽受容器宽度约束；筛选字段最多两列。 |
| ≤680px | 主导航转为五入口网格，素材子导航单独一行；页面标题与动作堆叠，动作可换行，内容左右留白 16px，筛选字段单列。项目搜索保持可用，只隐藏独立的辅助文案。 |
| ≤800px | AI 配置表改为带字段标签的纵向条目，取消桌面最小宽度；分页、列表操作允许换行。 |
| 粗指针设备 | 常用 Ant Design 按钮、输入、选择器，以及标签和状态筛选等指定目标使用 44px 高度。手机弹窗关闭按钮、素材操作和导航子入口也使用 44px；桌面保持紧凑密度。 |

宽任务表和时间轴仍在自身容器内滚动；浮层正文可滚动，标题和保存、采用等操作区保持独立。项目详情资源和协作区域使用完整内容宽度，窄屏协作搜索按字段顺序堆叠。

## 嵌入认证表单

重新登录弹窗使用 `identity-form identity-form-embedded`。变体占满正文宽度，使用 22px 内边距和 20px 字段节奏，取消内层独立背景、边框和圆角，与弹窗标题的左右边距对齐。独立登录页仍使用既有面板。样式入口是 [identity.css](../src/app/identity.css)，使用入口是 [AuthSession.tsx](../src/features/auth/AuthSession.tsx)；错误保留输入，提交期间显示处理中状态。

## 按需加载与图片

[App.tsx](../src/app/App.tsx) 按路由加载素材、任务、资产、AI 配置和项目详情；[EpisodePage.tsx](../src/pages/projects/EpisodePage.tsx) 延后加载成片编辑阶段。加载状态与路由错误边界保留恢复入口。生产 smoke 验证了首项目路由的延后编辑包边界；这不等于完整场景性能测量。

[ImagePreview.tsx](../src/components/ui/ImagePreview.tsx) 的缩略图默认惰性加载，缩略图和大图使用异步解码，并保留键盘预览、失败提示与重试。截图与 fixture 图片仅作验收证据，本轮没有新增交付的位图资产。

## 验证与记录边界

- 修正后的 TypeScript 与生产构建通过：[构建日志](../.runtime/ui-refinement-review-build.log)。
- 单元测试 124/124 通过：[单元日志](../.runtime/ui-refinement-unit.log)。
- 完整浏览器回归 58 项通过，1 项生产专用用例跳过，0 项失败；该用例在重建后的生产预览独立通过。因此两个运行合计覆盖 59 个独立用例：[浏览器日志](../.runtime/ui-refinement-review-e2e.log)、[生产日志](../.runtime/ui-refinement-review-production.log)。

浏览器 API、模型与生成均使用 fixture；上述结果不证明真实模型、付费生成或服务端集成。渲染证据包含手机、触控中间宽度和桌面，不能推导每条路由都有全部宽度的完整组合。Context / Detector 因缓存写入与网络下载失败未运行。

独立终审确认原三项问题全部解决，`remaining: clear`；基于原完整评审及三个修复的复验，加权综合分更新为 **9.10/10**，严格大于 9。`disposition: ship` 仅覆盖原三项修复的评分验收；此次未启动新的全面审计，原证据与抽样限制继续成立，详见 [终审报告](ui-refinement-finish-review.md)。

本轮只同步与当前改动相关的运动描述；[PRODUCT.md](../PRODUCT.md)、既有 assembly 文档及方向契约 FORM 的历史漂移保持原样，未据其改写产品事实或建立新规范。

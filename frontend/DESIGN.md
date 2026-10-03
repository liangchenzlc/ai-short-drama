---
name: 短剧工作台
description: 冷黑、紧凑的中文桌面创作与剪辑界面
colors:
  accent-fill: "#4c5cc7"
  accent-hover: "#5261ca"
  accent: "#a5b2ff"
  accent-strong: "#d5dcff"
  pale: "#222840"
  accent-border: "#495886"
  canvas: "#0b0d12"
  navigation: "#101219"
  surface: "#14171f"
  surface-muted: "#1a1e28"
  surface-raised: "#1c202b"
  surface-hover: "#222838"
  ink: "#edf0f7"
  sub: "#a6afc2"
  placeholder: "#959fb4"
  disabled-ink: "#6c7588"
  hair: "#292f3d"
  border-control: "#333b4c"
  border-hover: "#56617a"
  on-accent: "#ffffff"
  success: "#69cfac"
  success-bg: "#142e27"
  success-border: "#2c5b4c"
  warning: "#e9b96d"
  warning-bg: "#30271b"
  warning-border: "#675033"
  danger: "#f38995"
  danger-bg: "#321e28"
  danger-border: "#68404b"
typography:
  page-title:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "26px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "-0.025em"
  stage-title:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  body:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "13px"
    lineHeight: 1.6
  editor:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "14px"
    lineHeight: 2
  label:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "12px"
  native-action:
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.6
  technical:
    fontFamily: "Consolas, monospace"
    fontSize: "13px"
    lineHeight: 1.8
rounded:
  tag: "4px"
  status: "5px"
  control: "7px"
  media: "8px"
  inset: "9px"
  monitor: "10px"
  panel: "12px"
spacing:
  field-gap: "6px"
  action-gap: "8px"
  group-gap: "12px"
  section-gap: "16px"
  production-inset: "20px"
  dialog-inset: "22px"
  compact-inset: "24px"
  workspace-inline: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent-fill}"
    textColor: "{colors.on-accent}"
    typography: "{typography.native-action}"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.on-accent}"
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "6px 12px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-hover}"
  button-link:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
    typography: "{typography.native-action}"
    rounded: "0px"
    padding: "0px"
  button-link-hover:
    textColor: "{colors.accent-strong}"
  input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
  navigation:
    backgroundColor: "transparent"
    textColor: "{colors.sub}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "9px 11px"
  navigation-selected:
    backgroundColor: "{colors.pale}"
    textColor: "{colors.accent-strong}"
  filter-chip:
    backgroundColor: "transparent"
    textColor: "{colors.sub}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "4px 11px"
  filter-chip-selected:
    backgroundColor: "{colors.pale}"
    textColor: "{colors.accent-strong}"
  tag-success:
    backgroundColor: "{colors.success-bg}"
    textColor: "{colors.success}"
    rounded: "{rounded.tag}"
    padding: "2px 9px"
  card-project:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "17px 18px"
  card-project-hover:
    backgroundColor: "{colors.surface-raised}"
  storyboard-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "0px"
    padding: "10px 16px"
  storyboard-row-selected:
    backgroundColor: "{colors.pale}"
    textColor: "{colors.accent-strong}"
  timeline-clip:
    backgroundColor: "{colors.pale}"
    textColor: "{colors.accent}"
    rounded: "{rounded.tag}"
    padding: "10px"
  timeline-label-selected:
    backgroundColor: "{colors.accent-fill}"
    textColor: "{colors.ink}"
    padding: "0px 4px"
    height: "20px"
---

# Design System: 短剧工作台

## Overview

**Creative North Star: "冷黑剪辑工作台"**

项目与分集采用内容卡片，分集卡片保持固定媒体尺寸；媒体资产桌面四列，名称定位在画面左下，悬停仅对画面做轻微 transform 放大并突出边框，降低动效偏好下关闭缩放。分集制作采用流程、作品、AI 创作三栏，提示词和 Agent 共用右侧创作区域；窄屏明确切换作品与 AI 创作。账号中心为独立页面，项目详情以三个有可访问名称的图标入口分区。

冷黑剪辑工作台把正文、镜头、素材和候选放在视线中心。黑色画布承载连续编辑，紧凑工具围绕内容排列；标题负责定位，模型设置和低频选项按需展开。视觉服务于中文桌面创作的扫描、核对与剪辑。

冷灰表面通过明度、边线和留白分层，蓝紫色标记操作入口与当前选择，成功、警告、错误各有独立语义。系统字体保持熟悉的阅读感；短促状态变化和轻微弹窗入场提供反馈。桌面窗口收缩时调整列宽、间距和局部滚动，保持内容与操作可达。

**Key Characteristics:**

- 黑色画布与三层冷灰内容表面。
- 紧凑控件、明确中文标签和就近状态反馈。
- 蓝紫操作与选择色，独立的成功、警告、错误语义。
- 内容优先、局部滚动、固定弹窗操作区和可见键盘焦点。

本规范提取自当前实现；设计变量由 [tokens.css](src/app/tokens.css) 与 [theme.ts](src/app/theme.ts) 定义，布局与状态由 [workbench.css](src/app/workbench.css)、[production.css](src/app/production.css) 及实际组件补充。产品事实见 [PRODUCT.md](PRODUCT.md)，本次表面方向见 [ui-redesign-surface.md](docs/ui-redesign-surface.md)。

## Colors

冷灰构成编辑环境，浅蓝紫用于可读的文字与焦点，较深蓝紫用于带浅色文字的填充。

### Primary

- **冷蓝紫填充**（`accent-fill`）：原生主要操作、步骤编号与选中时间轴标签；悬停使用 `accent-hover`。
- **浅蓝紫**（`accent`）：链接、播放头、焦点和图标；`accent-strong` 用于当前导航与选择文字。
- **蓝灰选择面**（`pale` / `accent-border`）：选中行、导航、筛选与候选的背景和边线。

Ant Design 使用 `darkAlgorithm`；`accent-fill` 对应其 `colorPrimary` 种子，实际主按钮、悬停、禁用与派生颜色由算法生成，不能把种子值当作每个 Ant Design 控件的最终像素颜色。原生填充、链接和选择色各保留自己的用途。

### Neutral

- **黑色画布**（`canvas`）：页面、文字输入底与媒体留白。
- **导航冷黑**（`navigation`）：侧栏、顶栏与分集标题栏。
- **内容冷灰**（`surface`）：编辑器、列表条目、素材卡与监视器外框。
- **分组冷灰**（`surface-muted`）：表头、嵌套设置、分区标题与次级内容。
- **浮层冷灰**（`surface-raised`）：弹窗与原生次级按钮；`surface-hover` 表达悬停反馈。
- **亮正文**（`ink`）与**次级冷灰文字**（`sub`）：正文和辅助说明；占位提示使用 `placeholder`，Ant Design 禁用文字使用 `disabled-ink`。
- **细分隔线**（`hair`）、**控件边线**（`border-control`）与**悬停边线**（`border-hover`）：分别划分内容、界定输入和反馈交互。`on-accent` 用于原生主按钮文字。

### Semantic States

成功使用 `success` 及其深色底、边线，表示已保存、已完成或可用；警告使用 `warning` 组合，提示来源过期或需核对；错误与破坏性操作使用 `danger` 组合。状态必须同时带中文文字或图标。

### Named Rules

**The Readable Accent Rule.** 小字号文字必须按实际填充与前景检查对比度；原生主按钮使用 `on-accent`，选中时间轴标签使用 `ink`，Ant Design 派生色另行检查。

## Typography

**Display Font:** 页面标题沿用系统字体，没有独立展示字体。

**Body Font:** Segoe UI，按系统可用性回退到 PingFang SC、Microsoft YaHei、sans-serif。

**Label/Mono Font:** 中文标签沿用正文；技术 JSON 内容使用 Consolas、monospace。

**Character:** 字形选择以长时间编辑和中文扫描为准。紧凑标签与正文拉开层次，正文编辑器保留更宽松的行距。

### Hierarchy

- **Headline:** 页面标题使用 `page-title`；分集阶段标题使用 `stage-title`，局部标题按已有组件保持层次。
- **Title:** 弹窗标题为中等强调（16px、600）；素材与分镜分区标题多为紧凑尺寸（14–15px）。
- **Body:** 通用界面使用 `body`；说明段落通常使用更松行距（1.75），任务页简介限制阅读宽度（65ch）。
- **Editor:** 小说与剧本使用 `editor`；分镜脚本使用紧凑正文（13px、1.7 行距）。
- **Label:** 字段说明使用 `label`；状态与元数据使用小字号（11px）。时间、任务表与镜号使用等宽数字排列。
- **Action:** Ant Design 按钮为中等字重（500）；原生配置保存按钮与任务链接使用 `native-action`。

### Named Rules

**The Task Hierarchy Rule.** 用标题、邻近分组和行距区分信息层级；紧凑工具不压缩小说与剧本的阅读行距。

## Layout

通用工作区由固定宽度侧栏（200px）、窄顶栏（52px）和流式内容区组成。内容最大宽度受限（1700px），默认内边距为上下分层与左右编辑留白（30px 32px 40px）。项目页使用制作条目与继续创作区域；项目详情和分集使用各自标题栏，分集四步流程栏可收起，主编辑区占用剩余空间。

间距以字段内、操作组、内容分区递进。`field-gap` 与 `action-gap` 保持控件邻近；`group-gap` 与 `section-gap` 分开功能组；制作区、弹窗和工作区使用相应 inset。素材编辑抽屉把文字与图片设置并排，分镜只展开选中镜头，成片把素材区、监视器、工具和时间轴组织成编辑桌。

桌面收缩规则由实际媒体查询组成：窗口在 901–1280px 时侧栏收至 182px，主区使用 `compact-inset`，工具可换行；1200px 以下写作模型栏收至 240px；1100px 以下素材编辑抽屉改为 `min(900px, 94vw)`，分镜工具操作移到下一行；1700px 以上项目条目采用两列。1150px 以下项目详情改为单列。更窄的既有回退规则仍在源码中，当前交付验证范围是桌面 1024、1280、1440、1920px。

任务表保留明确列宽（总宽 1120px，其中任务 / 模型列 250px），空间不足时由表格自己的横向滚动承载。长历史与候选在浮层正文内滚动；标题和采用、保存、导出动作保留在滚动区外。原生视频表单的内部媒体 ID 字段隐藏并保留表单登记，用户通过图片选择器操作。

## Elevation & Depth

深度主要来自冷灰明度和细边线。项目条目、编辑器和按钮在静止时保持平面，按钮不添加主色阴影；弹窗、抽屉和下拉浮层使用结构性阴影，区分当前操作层与后台内容。

### Shadow Vocabulary

- **原生浮层阴影**（`--shadow-overlay: 0 24px 80px rgb(0 0 0 / 55%)`）：当前原生 Dialog 和素材抽屉的遮挡层。
- **Ant Design 浮层阴影**（`0 12px 36px rgba(0, 0, 0, 0.35)` / `0 16px 48px rgba(0, 0, 0, 0.45)`）：主题中的主、次浮层阴影。
- **Ant Design 输入焦点**（`0 0 0 2px rgba(101, 116, 239, 0.18)`）：输入框活动态。
- **原生表单焦点**（`0 0 0 3px var(--surface-muted)`）：配置输入等原生表单的焦点辅助层，同时改变边线。
- **时间轴选中描边**（`inset 0 0 0 1px rgb(0 0 0 / 45%)`）：缩略图之上的选中轮廓。

### Named Rules

**The Quiet Surface Rule.** 编辑内容默认平面呈现；阴影用于操作层与焦点，卡片悬停通过明度和边线反馈。

## Shapes

控件用小圆角，内容面板用较大圆角：`control` 与 `panel` 为主要形状。媒体框、嵌套设置、监视器和标签使用 frontmatter 中各自已有圆角；分镜连续行保留直角分隔，避免给每行重复加卡片边框。素材抽屉只保留左侧外圆角，贴合窗口右缘。图标采用现有 SVG 线性图标（1.6px 描边、圆端点和连接）。

## Components

### Buttons

紧凑、直接，操作文字说明结果。

- **Shape:** 原生控件采用 `control`；默认最小高度为 34px，内边距由对应组件 token 定义。Ant Design 常规、小、大控件分别为 34 / 28 / 38px，按钮横向内边距为 13px。
- **Primary:** 原生保存配置使用 `button-primary` 和悬停变体；Ant Design 主操作继承暗色主题种子与算法。
- **Hover / Focus:** 原生颜色与边线过渡为 160ms；键盘按钮焦点使用浅蓝紫轮廓（2px，外移 3px）。禁用原生按钮降低透明度（0.45）并禁止提交。
- **Secondary / Link:** 次级按钮使用浮层冷灰，悬停提高表面与边线亮度；任务链接保持透明底，悬停文字变亮并出现下划线。删除与放弃修改使用明确中文动词及危险态。

### Chips

筛选和状态各表达自己的含义。

状态筛选为可按压按钮（最小 30px），当前项使用选择底和选择边线，并通过 `aria-pressed` 表达状态。任务标签是只读信息，字号为 11px，成功、失败、排队与取消各带文字；不把只读标签做成伪按钮。

### Cards / Containers

条目易扫描，编辑表面给内容留空间。

项目条目使用 `card-project`：画幅图标板、名称、梗概、分集与风格、最近打开时间及进入指向组成一个可键盘操作的入口。悬停提高表面明度与蓝灰边线。图标板表达画幅，不伪造项目封面。素材卡和编辑器沿用内容冷灰、细边线与面板圆角；选中素材、分镜与时间轴通过选择底或描边反馈。

### Inputs / Fields

字段标签明确，输入结果可核对。

原生配置输入使用黑色输入底、控件边线和 `control`，最小高度 34px；悬停保持原样，焦点改变蓝紫边线与辅助层。Ant Design 输入使用暗色主题及主题焦点阴影，Select 的已选菜单项使用既有蓝灰底（`#2a304d`）与强选择文字。占位符保持可读，但字段含义由中文标签提供。错误就近提示并保留输入；处理中禁用提交。参考图、首尾帧通过图片选择与预览操作，隐藏内部 ID。

### Navigation

当前位置有明确选择底，其他入口安静排列。

通用侧栏按钮包含线性图标与中文名称，当前入口使用 `aria-current`、选择底和边线；素材分类按需展开。分集四步导航保留顺序编号和当前步骤，可收为窄栏。导航悬停提高表面明度，键盘焦点与按钮一致。桌面尺寸调整只改变空间分配，步骤编号不表示内容已经确认。

### Dialogs / Drawers

操作层有完整的标题、正文和出口。

原生 Dialog 采用浮层冷灰、控件边线、面板圆角及结构性阴影；背景遮罩为冷黑半透明层（`rgb(2 4 10 / 72%)`）。标题区有关闭按钮，正文按用途滚动。同步、导出、历史、采用成片共享正文 inset（20px 22px）与固定页脚（16px 22px 20px）；普通成片操作弹窗宽度受限（560px），历史弹窗较宽（820px），并为窗口四周保留空间。

应用确认使用统一样式和 Ant Design 按钮，说明对象与影响，先聚焦取消；嵌套弹窗关闭后恢复原入口焦点。Dialog 包含可见控件的 Tab 循环、Escape 关闭及内部下拉层挂载；提交期间按业务规则阻止关闭。刷新或关闭浏览器时，未保存保护仍由浏览器管理的 `beforeunload` 承担。

弹窗入场为短暂淡入与上移复位（180ms，从 6px 位移返回，`--ease-out`）；Ant Design 中、慢运动为 0.18 / 0.24s；分镜展开箭头为 160ms 旋转。减少动态效果偏好由 `useReducedMotion` 订阅并交给 `StudioProvider`，关闭 Ant Design 运动；原生弹窗与抽屉关闭入场动画，展开箭头关闭旋转过渡，滚动改为即时，颜色与边线反馈仍保留。

### Editing Selection

选中对象的位置稳定，内容与候选有清楚边界。

分镜摘要行包含镜号、省略脚本、时长与状态；展开行在列表内保留摘要位置，编辑器展示脚本、关联素材、图片与视频。候选先预览，再通过显式采用改变当前结果。成片时间轴的选中轮廓覆盖缩略图，标签使用深蓝紫填充与亮文字，播放头使用浅蓝紫；专注剪辑收起外围导航并保留退出与保存反馈。

`.impeccable/design.json` 展示原生按钮、字段、导航、筛选、状态、项目条目、分镜摘要与时间轴片段。片段的变量带当前值回退，可在独立 shadow DOM 中展示；色阶仅供色板预览，由已提取颜色推导，不构成新增实现变量。应用组件的状态逻辑仍由实际 React 组件承担。

## Do's and Don'ts

### Do:

- **Do** 从共享颜色角色与组件 token 延续冷黑工作台，保持正文、镜头和候选优先。
- **Do** 用明确中文标签、文字状态和就近反馈说明保存、生成、候选与当前采用。
- **Do** 保留可见键盘焦点、图片预览入口、弹窗内滚动和固定操作区。
- **Do** 在桌面窗口收缩时重排功能组，并把宽表与长历史的滚动限制在自己的容器内。
- **Do** 对删除、替换、共享影响和放弃修改使用有对象、有后果的应用确认。

### Don't:

- **Don't** 恢复已替换的浅色青绿视觉体系，或把编辑工作台改为营销首屏。
- **Don't** 用过大的控件、重复卡片阴影、虚构统计或无业务用途的装饰面板挤占编辑空间。
- **Don't** 把内部媒体 ID 作为可见输入，或让用户靠填写 ID 完成图片选择。
- **Don't** 仅靠颜色表达确认、错误或候选采用，也不要把步骤序号视为完成状态。
- **Don't** 自动采用生成候选、隐藏失败后的输入，或静默覆盖版本冲突。

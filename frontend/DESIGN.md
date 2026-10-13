---
name: 短剧工作台
description: 中性炭黑、紧凑的中文桌面创作与剪辑界面
colors:
  accent-fill: "#f5f5f5"
  accent-hover: "#ffffff"
  accent-active: "#e5e5e5"
  accent: "#c1c1c5"
  accent-strong: "#f5f5f5"
  pale: "#2b2b2b"
  accent-border: "#c1c1c5"
  canvas: "#101010"
  navigation: "#161616"
  surface: "#181818"
  surface-muted: "#202020"
  surface-raised: "#1f1f20"
  surface-hover: "#242424"
  media-canvas: "#080808"
  ink: "#f3f3f5"
  sub: "#b4b4bc"
  placeholder: "#9797a1"
  disabled-ink: "#737373"
  hair: "#2d2d2d"
  border-control: "#4a4a4a"
  border-hover: "#737373"
  on-accent: "#171717"
  success: "#4ade80"
  success-bg: "#152a1c"
  success-border: "#2d593a"
  warning: "#fbbf24"
  warning-bg: "#30271b"
  warning-border: "#675033"
  danger: "#f87171"
  danger-bg: "#321f1f"
  danger-border: "#683b3b"
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
    textColor: "{colors.on-accent}"
    padding: "0px 4px"
    height: "20px"
---

# Design System: 短剧工作台

## Overview

**Creative North Star: "中性炭黑剪辑工作台"**

项目与分集采用内容卡片，分集卡片保持固定媒体尺寸；媒体资产桌面四列，名称定位在画面左下，悬停仅对画面做轻微 transform 放大并突出边框，降低动效偏好下关闭缩放。标准分集使用顶部四步骤与满高创作工作区：小说为左设置、右剧本，素材和分镜为左侧单列卡片、预览与设置主面板。AI 创作助手由顶部保存状态左侧按钮打开，桌面增加独立右列，生成设置继续可用；窄屏助手使用抽屉，手机设置与助手互斥显示并保留各自输入。成片沿用内部剪辑布局，声音、字幕和配乐从成片标题下方的入口在工作区展开。账号中心为独立页面，项目详情以三个有可访问名称的图标入口分区。

中性炭黑剪辑工作台把正文、镜头、素材和候选放在视线中心。黑色画布承载连续编辑，紧凑工具围绕内容排列；标题负责定位，模型设置和低频选项按需展开。视觉服务于中文桌面创作的扫描、核对与剪辑。

色调参考 `frontend/canvas` 的默认暗色主题：中性灰表面通过明度、边线和留白分层，白底深字标记主要操作，灰色选择面与浅灰描边表达当前对象；成功、警告、错误各有独立语义。系统字体保持熟悉的阅读感；短促状态变化和轻微弹窗入场提供反馈。桌面窗口收缩时调整列宽、间距和局部滚动，保持内容与操作可达。

**Key Characteristics:**

- 炭黑画布与中性灰内容表面。
- 紧凑控件、明确中文标签和就近状态反馈。
- 白色主操作、灰色选择面与浅灰焦点，独立的成功、警告、错误语义。
- 内容优先、局部滚动、固定弹窗操作区和可见键盘焦点。

本规范提取自当前实现；设计变量由 [tokens.css](src/app/tokens.css) 与 [theme.ts](src/app/theme.ts) 定义，通用布局与状态由 [workbench.css](src/app/workbench.css)、[production.css](src/app/production.css) 及实际组件补充。标准分集布局使用 [episode-creation.css](src/features/projects/episode-creation.css)、[novel-editor.css](src/features/projects/novel-editor.css) 和 [episode-assets.css](src/features/assets/episode-assets.css) 的局部规则。产品事实见 [PRODUCT.md](PRODUCT.md)，工作区设计依据见[确认版设计](../docs/design/2026-10-08-standard-creation-workflow.md)，其他桌面表面方向保留 [ui-redesign-surface.md](docs/ui-redesign-surface.md)。

## Colors

中性灰构成编辑环境，浅灰用于链接、焦点与选择描边，近白填充搭配深色文字用于主要操作。

### Primary

- **近白填充**（`accent-fill`）：原生主要操作、步骤编号与选中时间轴标签，搭配 `on-accent` 深色文字；悬停使用 `accent-hover`，按下使用 `accent-active`。
- **浅灰强调**（`accent`）：链接、播放头、焦点和图标；`accent-strong` 用于当前导航与选择文字。
- **灰色选择面与浅灰描边**（`pale` / `accent-border`）：选中行、导航、筛选与候选的背景和边线；正文选择使用 `pale` 与 `ink`，避免浅底亮字。

Ant Design 保留 `darkAlgorithm`，并显式设置主按钮默认、悬停、按下的填充与深色文字、灰色选择面和禁用态，避免仅改种子后出现前景错配。危险主按钮使用红底深字，开关启用态使用绿色；浮层提示保留深底亮字。原生填充、链接和选择色各保留自己的用途，验收检查实际计算颜色。

### Neutral

- **黑色画布**（`canvas`）：页面、文字输入底与媒体留白。
- **导航炭黑**（`navigation`）：侧栏、顶栏与分集标题栏。
- **内容中性灰**（`surface`）：编辑器、列表条目、素材卡与监视器外框。
- **分组中性灰**（`surface-muted`）：表头、嵌套设置、分区标题与次级内容。
- **浮层中性灰**（`surface-raised`）：弹窗与原生次级按钮；`surface-hover` 表达悬停反馈。
- **媒体深黑**（`media-canvas`）：图片预览与视频播放的留白。
- **亮正文**（`ink`）与**次级灰色文字**（`sub`）：正文和辅助说明；占位提示使用 `placeholder`，禁用文字使用 `disabled-ink`。
- **细分隔线**（`hair`）、**控件边线**（`border-control`）与**悬停边线**（`border-hover`）：分别划分内容、界定输入和反馈交互。`on-accent` 用于浅色填充上的深色文字，不用于图片上的十字线等亮色前景。

### Semantic States

成功使用 `success` 及其深色底、边线，表示已保存、已完成或可用；警告使用 `warning` 组合，提示来源过期或需核对；错误与破坏性操作使用 `danger` 组合。状态必须同时带中文文字或图标。

### Named Rules

**The Readable Accent Rule.** 小字号文字必须按实际填充与前景检查对比度；原生主按钮与选中时间轴标签使用 `on-accent`，图片上的亮色前景使用 `ink`，Ant Design 派生色另行检查。

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

通用工作区由固定宽度侧栏（200px）、窄顶栏（52px）和流式内容区组成。内容最大宽度受限（1700px），默认内边距为上下分层与左右编辑留白（30px 32px 40px）。项目页使用制作条目与继续创作区域，项目详情保留自己的标题栏。

标准分集覆盖通用内容宽度限制，桌面容器高为 `100dvh`，分集顶栏（53px）与顶部步骤（52px）之外的空间全部交给工作区。工作区外边距为上下 12px、左右 20px；列表、正文、预览和设置按各自容器滚动。小说设置在左、Quill 剧本在右，DOM 与键盘顺序均先设置、后正文；素材与分镜的标题横跨预览和设置，设置收起后预览扩展。桌面助手打开后增加独立右列，主面板缩窄，保持设置与输入。

标准分集的列宽、卡片高度和间距来自局部 `--creation-*` 变量；下表记录当前源码值。素材与分镜卡片的名称行固定为 38px，选中、图片加载和待制作标签不会改变卡片高度。

| 窗口宽度 | 小说设置 | 素材／镜头列表 | 面板内设置 | 助手 | 卡片总高 | 列间距 |
| --- | --- | --- | --- | --- | --- | --- |
| ≥1700px | 330px | 280px | 349px | 340px | 203px | 16px |
| 1440–1699px | 306px | 250px | 324px | 320px | 187px | 12px |
| 1280–1439px | 282px | 220px | 298px | 288px | 187px | 12px |
| 1100–1279px | 270px | 208px | 280px | 264px | 173px | 10px |
| 1000–1099px | 260px | 200px | 280px | 264px | 173px | 10px |
| 701–999px | 260px | 200px | 280px | 330px 抽屉 | 173px | 12px |
| ≤700px | 按需抽屉 | 与主面板纵排 | 按需抽屉 | 按需抽屉 | 171px | 10px |

素材与分镜以主面板容器宽度判断预览和设置的排列；当前在 725px 及以下改为上下排列，因此打开助手也会触发重排。1000px 以下助手为右侧抽屉。手机步骤栏为 48px，工作区留白为 10px；素材和镜头列表高 332px，只在列表内部滚动，正文和页面可纵向滚动。素材、分镜及小说设置抽屉宽为 `min(366.667px, 100vw)`，与助手互斥显示；横向步骤只在自身容器滚动。

间距以字段内、操作组、内容分区递进。`field-gap` 与 `action-gap` 保持控件邻近；`group-gap` 与 `section-gap` 分开功能组；制作区、弹窗和工作区使用相应 inset。提示词内容区默认高 180px，可在 160–360px 内拖动调整，模型与参数操作行另占高度；高度不超过 700px 的桌面窗口中，素材和分镜内容区默认 160px，小说改编要求保持 180px。独立个人／项目素材库继续使用文字与图片设置并排的详情抽屉，成片仍把素材区、监视器、工具和时间轴组织成编辑桌。

其他页面保留既有媒体查询：窗口在 901–1280px 时通用侧栏收至 182px，主区使用 `compact-inset`，工具可换行；1100px 以下独立素材编辑抽屉为 `min(900px, 94vw)`；1700px 以上项目条目采用两列，1150px 以下项目详情改为单列。标准分集采用上表的独立布局规则。

任务表保留明确列宽（总宽 1120px，其中任务 / 模型列 250px），空间不足时由表格自己的横向滚动承载。长历史与候选在浮层正文内滚动；标题和采用、保存、导出动作保留在滚动区外。原生视频表单的内部媒体 ID 字段隐藏并保留表单登记，用户通过图片选择器操作。

## Elevation & Depth

深度主要来自中性灰明度和细边线。项目条目、编辑器和按钮在静止时保持平面，按钮不添加主色阴影；弹窗、抽屉和下拉浮层使用结构性阴影，区分当前操作层与后台内容。

### Shadow Vocabulary

- **原生浮层阴影**（`--shadow-overlay: 0 24px 80px rgb(0 0 0 / 55%)`）：当前原生 Dialog 和素材抽屉的遮挡层。
- **Ant Design 浮层阴影**（`0 12px 36px rgba(0, 0, 0, 0.35)` / `0 16px 48px rgba(0, 0, 0, 0.45)`）：主题中的主、次浮层阴影。
- **Ant Design 输入焦点**（`0 0 0 2px rgba(193, 193, 197, 0.18)`）：输入框活动态。
- **原生表单焦点**（`0 0 0 3px var(--surface-muted)`）：配置输入等原生表单的焦点辅助层，同时改变边线。
- **时间轴选中描边**（`inset 0 0 0 1px rgb(0 0 0 / 45%)`）：缩略图之上的选中轮廓。

### Named Rules

**The Quiet Surface Rule.** 编辑内容默认平面呈现；阴影用于操作层与焦点，卡片悬停通过明度和边线反馈。

## Shapes

控件用小圆角，内容面板用较大圆角：`control` 与 `panel` 为主要形状。媒体框、嵌套设置、监视器和标签使用 frontmatter 中各自已有圆角；分镜卡片使用 12px 圆角和细边线，选中描边表达当前编辑对象。素材抽屉只保留左侧外圆角，贴合窗口右缘。图标采用现有 SVG 线性图标（1.6px 描边、圆端点和连接）。

## Components

### Buttons

紧凑、直接，操作文字说明结果。

- **Shape:** 原生控件采用 `control`；默认最小高度为 34px，内边距由对应组件 token 定义。Ant Design 常规、小、大控件分别为 34 / 28 / 38px，按钮横向内边距为 13px。
- **Primary:** 原生保存配置使用 `button-primary` 和悬停、按下变体；Ant Design 主操作显式使用近白填充和深色前景。
- **Hover / Focus:** 原生颜色与边线过渡为 160ms；键盘按钮焦点使用浅灰轮廓（2px，外移 3px）。禁用原生按钮降低透明度（0.45）并禁止提交。
- **Secondary / Link:** 次级按钮使用中性灰，悬停提高表面与边线亮度；任务链接保持透明底，悬停文字变亮并出现下划线。删除与放弃修改使用明确中文动词及危险态。

### Chips

筛选和状态各表达自己的含义。

状态筛选为可按压按钮（最小 30px），当前项使用选择底和选择边线，并通过 `aria-pressed` 表达状态。任务标签是只读信息，字号为 11px，成功、失败、排队与取消各带文字；不把只读标签做成伪按钮。

### Cards / Containers

条目易扫描，编辑表面给内容留空间。

项目条目使用 `card-project`：画幅图标板、名称、梗概、分集与风格、最近打开时间及进入指向组成一个可键盘操作的入口。悬停提高表面明度与灰色边线。图标板表达画幅，不伪造项目封面。素材卡和编辑器沿用内容中性灰、细边线与面板圆角；选中素材、分镜与时间轴通过选择底或描边反馈。

### Inputs / Fields

字段标签明确，输入结果可核对。

原生配置输入使用黑色输入底、控件边线和 `control`，最小高度 34px；悬停保持原样，焦点改变浅灰边线与辅助层。Ant Design 输入使用暗色主题及主题焦点阴影，Select 的已选菜单项使用灰色底（`#2b2b2b`）与亮文字。占位符保持可读，但字段含义由中文标签提供。错误就近提示并保留输入；处理中禁用提交。参考图、首尾帧通过图片选择与预览操作，隐藏内部 ID。

标准创作的圆角提示词框把输入区与模型、参数、发送操作合在一起，参考图片置于框上方。素材名称与类型同排，描述常驻；分镜图、视频使用一致的名称与脚本表单。小说使用 Quill 2.0.3，正文为 14px、双倍行距，格式工具与场次定位保持紧凑。“查看原文”打开左侧全高抽屉（宽为 `min(560px, 100vw)`）。仅小说正文、改编要求和框内文本模型选择不增加聚焦外框或阴影；插入光标、文本选区、可访问名称及其他控件的键盘焦点保留。

### Navigation

当前位置有明确选择底，其他入口安静排列。

通用侧栏按钮包含线性图标与中文名称，当前入口使用 `aria-current`、选择底和边线；素材分类按需展开。标准分集顶部四步导航显示顺序编号、当前步骤及真实作品状态，当前项使用选择面与底边线，并通过 `aria-current="step"` 表达。手机步骤仅在自身容器横向滚动。导航悬停提高表面明度，键盘焦点与按钮一致；步骤编号不表示内容已经确认。

### Dialogs / Drawers

操作层有完整的标题、正文和出口。

原生 Dialog 采用浮层中性灰、控件边线、面板圆角及结构性阴影；背景遮罩为黑色半透明层（`rgb(0 0 0 / 72%)`）。标题区有关闭按钮，正文按用途滚动。同步、导出、历史、采用成片共享正文 inset（20px 22px）与固定页脚（16px 22px 20px）；普通成片操作弹窗宽度受限（560px），历史弹窗较宽（820px），并为窗口四周保留空间。

应用确认使用统一样式和 Ant Design 按钮，说明对象与影响，先聚焦取消；嵌套弹窗关闭后恢复原入口焦点。Dialog 包含可见控件的 Tab 循环、Escape 关闭及内部下拉层挂载；提交期间按业务规则阻止关闭。刷新或关闭浏览器时，未保存保护仍由浏览器管理的 `beforeunload` 承担。

弹窗入场为短暂淡入与上移复位（180ms，从 6px 位移返回，`--ease-out`）；Ant Design 中、慢运动为 0.18 / 0.24s。减少动态效果偏好由 `useReducedMotion` 订阅并交给 `StudioProvider`，关闭 Ant Design 运动；原生弹窗与抽屉关闭入场动画，滚动改为即时，颜色与边线反馈仍保留。

### Editing Selection

选中对象的位置稳定，内容与候选有清楚边界。

标准分集的素材与分镜均使用单列固定高度媒体卡片，下方单行显示名称及类型或景别，长名称省略并提供完整名称。素材卡封面只取当前采用图片；镜头卡随图、视频标签显示对应当前媒体及待制作状态，镜号和脚本时长放在媒体区。待制作标记位于媒体左下，选中标记只表达当前编辑对象；卡片不展开，也不常驻采用状态文字。

素材新增直接追加临时卡片，补齐名称和类型后原位接入服务端对象；镜头新增直接追加空白镜头。作品信息与私人参数分别自动保存，顶部区分本机保留、保存中、已保存、失败及冲突，就近保留重试与核对入口。分镜主面板只有“分镜图 / 分镜视频”两个标签，镜头名称独立于脚本，景别仅作卡片元信息；前后镜头切换与已有隐藏字段保留。候选在主面板预览，剧本通过“采用并编辑”进入当前稿，图片和视频通过明确采用更新作品；生成记录按需弹窗查看。

成片时间轴的浅灰选中轮廓覆盖缩略图，标签使用近白填充与深色文字，播放头使用浅灰；专注剪辑收起外围导航并保留退出与保存反馈。

### AI 创作助手

分集顶部保存状态左侧固定提供“AI 创作助手”，不展示创作方式切换。助手复用画布的标题栏、历史与新对话图标、消息气泡、安全 Markdown、引用标签、3–8 行输入、左下模型选择和圆形发送/停止按钮。桌面助手使用上表列宽，打开时主面板让出空间，选择对象、编辑及展开设置保持助手可见；关闭后主面板恢复宽度。窄屏使用右侧抽屉并支持 Escape、Tab 循环和关闭后焦点恢复；手机与设置互斥，各自输入保留。

会话标题采用项目名称，切换分集或选择对象只改变本条消息的可移除作品引用，不更换项目对话。新对话、选择、搜索、归档和重命名统一放在项目历史管理中；旧消息放入“历史创作记录”，保留原任务与候选处理入口。附件与 Skill 放入画布输入区的工具行，主题变量由宿主和画布各自适配，不引入整套画布全局样式。素材卡点击或 Enter/Space 选择对象，预览图片另有入口；切换对象前等待相关保存回执，失败保留原编辑会话。

`.impeccable/design.json` 展示原生按钮、字段、导航、筛选、状态、项目条目、分镜摘要与时间轴片段。片段的变量带当前值回退，可在独立 shadow DOM 中展示；色阶仅供色板预览，由已提取颜色推导，不构成新增实现变量。应用组件的状态逻辑仍由实际 React 组件承担。

## Do's and Don'ts

### Do:

- **Do** 从共享颜色角色与组件 token 延续中性炭黑工作台，保持正文、镜头和候选优先。
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

## 无限画布的源视觉边界

本文件的主题、布局和组件替换约定适用于标准工作台。无限画布按用户明确的一比一要求，使用 `frontend/canvas/` 独立子包中的 BeefTV Provider、CSS、字体、组件和交互实现，不套用标准页面的全局样式，也不重排源工具栏与节点面板。按用户后续调整，共同声明但版本不同的直接依赖采用宿主版本，源独有依赖仍由画布自己的锁文件固定；兼容适配不能改变原操作流程。新模式选择仍使用宿主现有创建弹窗风格。验收要在依赖对齐后重新核对源/目标的同环境截图、控件几何和操作轨迹，源码哈希、旧版本截图或构建通过不能替代本次视觉验收。

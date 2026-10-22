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

项目卡片名称、最近打开日期和三点更多按钮同行，名称过长时截断并保留完整提示；卡片链接在新标签页打开，更多按钮独立于链接，不触发导航。项目、素材和分镜的删除均收进更多菜单，确认窗明确展示对象与后果，默认聚焦取消；删除进行中阻止重复操作，错误与核对动作使用当前操作层的统一反馈详情，保留原输入。

作品列表采用内容卡片，创作模式卡片直接打开四阶段工作区；媒体资产桌面四列，名称定位在画面左下，悬停仅对画面做轻微 transform 放大并突出边框，降低动效偏好下关闭缩放。创作模式使用顶部四步骤与满高创作工作区：小说为左设置、右剧本，素材和分镜为左侧单列卡片、预览与设置主面板。AI 创作助手由顶部保存状态右侧按钮打开，桌面增加独立右列，生成设置继续可用；窄屏助手使用抽屉，手机设置与助手互斥显示并保留各自输入。成片使用紧凑剪辑布局，标题行提供图标入口，声音、字幕和配乐在右侧可收起面板内编辑，窄屏使用局部操作层。账号中心为独立页面；没有项目详情、分集、协作及全局素材库页面。

中性炭黑剪辑工作台把正文、镜头、素材和候选放在视线中心。黑色画布承载连续编辑，紧凑工具围绕内容排列；标题负责定位，模型设置和低频选项按需展开。视觉服务于中文桌面创作的扫描、核对与剪辑。

色调参考 `frontend/canvas` 的默认暗色主题：中性灰表面通过明度、边线和留白分层，白底深字标记主要操作，灰色选择面与浅灰描边表达当前对象；成功、警告、错误各有独立语义。系统字体保持熟悉的阅读感；短促状态变化和轻微弹窗入场提供反馈。桌面窗口收缩时调整列宽、间距和局部滚动，保持内容与操作可达。

**Key Characteristics:**

- 炭黑画布与中性灰内容表面。
- 紧凑控件、明确中文标签和就近状态反馈。
- 白色主操作、灰色选择面与浅灰焦点，独立的成功、警告、错误语义。
- 内容优先、局部滚动、固定弹窗操作区和可见键盘焦点。

本规范提取自当前实现；设计变量由 [tokens.css](src/app/tokens.css) 与 [theme.ts](src/app/theme.ts) 定义，通用布局与状态由 [workbench.css](src/app/workbench.css)、[production.css](src/app/production.css) 及实际组件补充。创作模式布局使用 [episode-creation.css](src/features/projects/episode-creation.css)、[novel-editor.css](src/features/projects/novel-editor.css)、[episode-assets.css](src/features/assets/episode-assets.css)、[storyboard-layout.css](src/features/projects/storyboard-layout.css) 和 [standard-assistant.css](src/features/ai-assistant/standard-assistant.css) 的局部规则。产品事实见 [PRODUCT.md](PRODUCT.md)，工作区设计依据见[确认版设计](../docs/design/2026-10-08-标准模式创作流程设计.md)，其他桌面表面方向保留 [ui-redesign-surface.md](docs/ui-redesign-surface.md)。

## Colors

中性灰构成编辑环境，浅灰用于链接、焦点与选择描边，近白填充搭配深色文字用于主要操作。

### Primary

- **近白填充**（`accent-fill`）：原生主要操作、步骤编号与选中时间轴标签，搭配 `on-accent` 深色文字；悬停使用 `accent-hover`，按下使用 `accent-active`。
- **浅灰强调**（`accent`）：链接、播放头、焦点和图标；`accent-strong` 用于当前导航与选择文字。
- **灰色选择面与浅灰描边**（`pale` / `accent-border`）：选中行、导航、筛选与候选的背景和边线；正文选择使用 `pale` 与 `ink`，避免浅底亮字。

Ant Design 保留 `darkAlgorithm`，并显式设置主按钮默认、悬停、按下的填充与深色文字、灰色选择面和禁用态，避免仅改种子后出现前景错配。危险主按钮使用红底深字，开关启用态使用绿色；浮层提示保留深底亮字。原生填充、链接和选择色各保留自己的用途，验收检查实际计算颜色。

### Neutral

- **黑色画布**（`canvas`）：页面、文字输入底与媒体留白。
- **导航炭黑**（`navigation`）：侧栏、顶栏与创作标题栏。
- **内容中性灰**（`surface`）：编辑器、列表条目、素材卡与监视器外框。
- **分组中性灰**（`surface-muted`）：表头、嵌套设置、分区标题与次级内容。
- **浮层中性灰**（`surface-raised`）：弹窗与原生次级按钮；`surface-hover` 表达悬停反馈。
- **媒体深黑**（`media-canvas`）：图片预览与视频播放的留白。
- **亮正文**（`ink`）与**次级灰色文字**（`sub`）：正文和辅助说明；占位提示使用 `placeholder`，禁用文字使用 `disabled-ink`。
- **细分隔线**（`hair`）、**控件边线**（`border-control`）与**悬停边线**（`border-hover`）：分别划分内容、界定输入和反馈交互。`on-accent` 用于浅色填充上的深色文字，不用于图片上的十字线等亮色前景。

### Semantic States

成功使用 `success` 及其深色底、边线，表示已保存、已完成或可用；警告使用 `warning` 组合，提示来源过期或需核对；错误与破坏性操作使用 `danger` 组合。状态必须同时带中文文字或图标。

创作模式在 `.web-episode` 内按确认原型覆盖状态色：成功 `#8be1ac`、警告 `#f1c376`、错误 `#fa9898`、低强调文字 `#9999a2`。当前采用与候选标签分别使用深绿、深棕底及对应细边线；这些局部值不修改其他页面的通用 token。

### Named Rules

**The Readable Accent Rule.** 小字号文字必须按实际填充与前景检查对比度；原生主按钮与选中时间轴标签使用 `on-accent`，图片上的亮色前景使用 `ink`，Ant Design 派生色另行检查。

## Typography

**Display Font:** 页面标题沿用系统字体，没有独立展示字体。

**Body Font:** Segoe UI，按系统可用性回退到 PingFang SC、Microsoft YaHei、sans-serif。

**Label/Mono Font:** 中文标签沿用正文；技术 JSON 内容使用 Consolas、monospace。

**Character:** 字形选择以长时间编辑和中文扫描为准。紧凑标签与正文拉开层次，正文编辑器保留更宽松的行距。

### Hierarchy

- **Headline:** 页面标题使用 `page-title`；创作阶段标题使用 `stage-title`，局部标题按已有组件保持层次。
- **Title:** 弹窗标题为中等强调（16px、600）；素材与分镜分区标题多为紧凑尺寸（14–15px）。
- **Body:** 通用界面使用 `body`；说明段落通常使用更松行距（1.75），任务页简介限制阅读宽度（65ch）。
- **Editor:** 小说与剧本使用 `editor`；分镜脚本使用紧凑正文（13px、1.7 行距）。
- **Label:** 字段说明使用 `label`；状态与元数据使用小字号（11px）。时间、任务表与镜号使用等宽数字排列。
- **Action:** Ant Design 按钮为中等字重（500）；原生配置保存按钮与任务链接使用 `native-action`。

### Named Rules

**The Task Hierarchy Rule.** 用标题、邻近分组和行距区分信息层级；紧凑工具不压缩小说与剧本的阅读行距。

## Layout

通用工作区由固定宽度侧栏（200px）、窄顶栏（52px）和流式内容区组成。内容最大宽度受限（1700px），默认内边距为上下分层与左右编辑留白（30px 32px 40px）。项目页使用制作条目与继续创作区域，项目详情保留自己的标题栏。

创作模式覆盖通用内容宽度限制，桌面容器高为 `100dvh`，60px单层顶栏包含作品名称、居中四步和保存/助手入口，其余空间交给工作区；创作工作区与专注剪辑均不显示账号入口。工作区外围内边距桌面默认12px、手机10px；剧本创作（source）与分镜（storyboard）局部统一为8px，素材与分镜面板间距也为8px。列表、正文、预览和设置按各自容器滚动。小说设置在左、Quill剧本在右，DOM与键盘顺序均先设置、后正文；素材与分镜的标题横跨预览和设置，设置收起后预览扩展。桌面助手打开后增加独立右列，主面板缩窄，保持设置与输入。

创作模式的列宽、卡片高度和间距来自局部 `--creation-*` 变量；下表记录素材与分镜精修后的有效尺寸，面板内设置宽度四舍五入到整像素，剧本创作间距也固定为8px。素材与分镜卡片的名称行固定为34px，选中、图片加载和待制作标签不会改变卡片高度。

| 窗口宽度 | 小说设置 | 素材／镜头列表 | 面板内设置 | 助手 | 卡片总高 | 列间距 |
| --- | --- | --- | --- | --- | --- | --- |
| ≥1700px | 330px | 252px | 349px | 默认 380px，可调 | 187px | 8px |
| 1440–1699px | 306px | 228px | 324px | 默认 380px，可调 | 171px | 8px |
| 1280–1439px | 282px | 204px | 298px | 默认 380px，可调 | 171px | 8px |
| 1100–1279px | 270px | 192px | 280px | 默认 380px，可调 | 157px | 8px |
| 1000–1099px | 260px | 184px | 280px | 随可用空间，至少 320px | 157px | 8px |
| 701–999px | 260px | 184px | 280px | 330px 抽屉 | 157px | 8px |
| ≤700px | 按需抽屉 | 与主面板纵排 | 按需抽屉 | 按需抽屉 | 155px | 8px |

素材与分镜通过 ResizeObserver 读取主面板可用宽度，设置宽度为 `--creation-settings-base × 10 / 9`；当主面板 `clientWidth < 设置宽度 + 402px` 时，预览与设置在面板内改为上下排列，因此打开助手也会触发重排。阈值随当前设置列宽变化，此时预览区域高 460px，面板内部滚动。

1000px 以下助手为右侧抽屉。桌面与手机均使用 60px 单层头部，四步导航在等宽左右轨道之间居中；窄屏仅导航内部横向滚动，工作区外围留白默认10px，剧本创作保持8px；素材和镜头列表高 332px，只在列表内部滚动，主面板最小高 510px，内部作品面板最小高 508px、媒体预览高 260px。手机剧本创作区域高为 `calc(100dvh - 76px)`，页面可纵向滚动。素材与分镜设置抽屉宽为 `min(366.667px, 100vw)`，小说设置为 `min(440px, 100vw)`，手机助手为 `min(340px, 100vw)`；设置与助手互斥显示。横向步骤只在自身容器滚动；1279px 及以下隐藏顶栏“标准模式”标签。

间距以字段内、操作组、内容分区递进。`field-gap` 与 `action-gap` 保持控件邻近；`group-gap` 与 `section-gap` 分开功能组；制作区、弹窗和工作区使用相应 inset。素材与分镜提示词组合框最小220px并填满设置余高，输入区最小160px，短窗口由设置区滚动；小说改编要求保持180px。独立个人／项目素材库继续使用文字与图片设置并排的详情抽屉，成片仍把素材区、监视器、工具和时间轴组织成编辑桌。

创作模式的面板内距默认12px，剧本、素材与分镜设置正文为10px，设置标题桌面44px；功能组距8–12px，普通浮层正文默认16px、页脚12×16px，不把全站`production-inset`改成局部值。分镜生成记录与参考图选择正文收至12px。手机生成、模型及参数操作行的可见高度为44px，圆形发送为44×44px；素材与分镜组合框保持同排，模型可收缩，生成选项与模型间距4px，发送通过自动外边距靠右。小说使用144px紧凑文本模型，与44px发送同排、8px间距；模型外框44px、内层Select与selector42px，180px文本区不压缩，组合框内距8px、总高约248px。小说桌面组合框约236px，正文阅读宽度与行距保留。

其他页面保留既有媒体查询：窗口在 901–1280px 时通用侧栏收至 182px，主区使用 `compact-inset`，工具可换行；1100px 以下独立素材编辑抽屉为 `min(900px, 94vw)`；1700px 以上项目条目采用两列，1150px 以下项目详情改为单列。创作模式采用上表的独立布局规则。

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

控件用小圆角，内容面板用较大圆角：`control` 与 `panel` 为主要形状。媒体框、嵌套设置、监视器和标签使用 frontmatter 中各自已有圆角；创作模式的素材与分镜卡片使用 8px 圆角和细边线，选中描边表达当前编辑对象。素材抽屉只保留左侧外圆角，贴合窗口右缘。图标采用现有 SVG 线性图标（1.6px 描边、圆端点和连接）。

## Components

### Buttons

紧凑、直接，操作文字说明结果。

- **Shape:** 原生控件采用 `control`；默认最小高度为 34px，内边距由对应组件 token 定义。Ant Design 常规、小、大控件分别为 34 / 28 / 38px，按钮横向内边距为 13px。
- **Primary:** 原生保存配置使用 `button-primary` 和悬停、按下变体；Ant Design 主操作显式使用近白填充和深色前景。
- **Hover / Focus:** 原生颜色与边线过渡为 160ms；键盘按钮焦点使用浅灰轮廓（2px，外移 3px）。禁用原生按钮降低透明度（0.45）并禁止提交。
- **Secondary / Link:** 次级按钮使用中性灰，悬停提高表面与边线亮度；任务链接保持透明底，悬停文字变亮并出现下划线。删除与放弃修改使用明确中文动词及危险态。

创作模式的手机生成/参数控件采用上述44px局部规格；助手标题工具沿用40px。悬停仅改变背景、文字和边线明度，不改变按钮宽高或位置。低频同对象操作收在一级更多菜单，生成、采用、前后镜、播放与导出等必经动作保留直达。

### Chips

筛选和状态各表达自己的含义。

状态筛选为可按压按钮（最小 30px），当前项使用选择底和选择边线，并通过 `aria-pressed` 表达状态。任务标签是只读信息，字号为 11px，成功、失败、排队与取消各带文字；不把只读标签做成伪按钮。

### Cards / Containers

条目易扫描，编辑表面给内容留空间。

项目条目使用 `card-project`：模式图标板、名称、最近打开时间及进入指向组成一个可键盘操作的入口。悬停提高表面明度与灰色边线。图标板表达创作模式，不伪造作品封面。素材卡和编辑器沿用内容中性灰、细边线与面板圆角；选中素材、分镜与时间轴通过选择底或描边反馈。

标准素材列表标题保留新增与提取工具，对象标题保留生成记录及设置开合，并增加更多图标；上传、媒体资产库选择及移除仍在生成记录底部的“素材操作”，主面板更多复用相同回调。分镜列表标题保留新增与提取，对象标题提供前后镜头、媒体记录、设置及更多；主面板与“分镜生成记录”底部复用批量与排序等原操作。权限、选中边界与核对规则一致，从记录内执行额外操作先关闭记录弹窗。

单张作品图片工具条保留预览和条件设主图。“编辑图片”一级菜单按基础处理5项及AI处理5项分组；“更多图片操作”放下载、看图反推、复制提示词、条件加入私人参考图及原图历史。历史图片不显示编辑和看图反推。桌面hover与`focus-within`均可显现工具，触屏提供常驻入口，不增加二级菜单。

### Inputs / Fields

字段标签明确，输入结果可核对。

原生配置输入使用黑色输入底、控件边线和 `control`，最小高度 34px；悬停保持原样，焦点改变浅灰边线与辅助层。Ant Design 输入使用暗色主题及主题焦点阴影，Select 的已选菜单项使用灰色底（`#2b2b2b`）与亮文字。占位符保持可读，但字段含义由中文标签提供。错误就近提示并保留输入；处理中禁用提交。参考图、首尾帧通过图片选择与预览操作，隐藏内部 ID。

标准创作的圆角提示词框把输入区与模型、参数、发送操作合在一起，参考图片置于框上方。素材名称与类型同排，描述常驻；分镜图、视频使用一致的名称与脚本表单。小说使用 Quill 2.0.3，正文为 14px、1.95 行距，最大阅读宽度 760px 并居中，格式工具与场次定位保持紧凑。正文滚动区内边距桌面为16px 24px 24px，1279px及以下为16px 20px 20px，手机为12px 14px 20px。剧本标题区桌面为48px、手机为52px；左侧剧本设置标题行为44px，仅保留收起入口。

小说来源摘要整块作为“查看原文”按钮，打开“小说原文”左侧全高抽屉（宽为 `min(500px, 100vw)`）；空白、梗概与导入来源保留各自描述及独立原文入口，设置标题行不重复放置原文图标。仅小说正文、改编要求和框内文本模型选择不增加聚焦外框或阴影；插入光标、文本选区、可访问名称及其他控件的键盘焦点保留。

图片生成选项浮层宽为 `min(376px, 100vw - 24px)`，内边距12px，不显示气泡箭头。素材与分镜图片浮层标题为“生成选项”，依次提供比例、分辨率和“选择张数”（1／2／4张），不再外置独立张数下拉。七个比例图形选项中2:3、3:2禁用，其余按模型能力启用；图片分辨率为1K／2K／4K，比例及分辨率均可用“模型默认”恢复为`null`。分镜视频也使用“生成选项”标题，在同一浮层提供比例、分辨率与“选择时长”；分辨率为480p／720p／1080p／模型默认，并按模型能力禁用不支持档位。时长使用实际能力提供的秒数按钮，保留当前不支持的值并禁用，未提供时长档位时使用1–3600秒整数输入。入口显示比例、分辨率及秒数。关闭后卸载浮层，关闭按钮及Escape恢复入口焦点；手机Escape只关闭当前选项，保留底层设置。

标准创作模型下拉展开为“名称 / 型号与默认或停用状态”两行，默认条目48px、菜单宽360px并受视口限制；收起仍单行省略。剧本创作的文本模型、素材与分镜的图片模型、分镜视频模型使用compact变体：收起最大宽144px，不显示前置图标、尾部箭头或清除入口，保留搜索、loading与disabled状态；展开宽240px并受视口限制，条目44px、内边距4px 8px。设置较窄时触发器可继续缩小，生成选项紧邻模型，发送靠右；空间不足允许参数与发送换行。其他模型选择保留默认360px菜单与48px条目。

图片与视频生成选项均为 `min(376px, 100vw - 24px)` 宽、12px内距；共用选项与参考图片编号样式随创作工作区加载，直达分镜不得依赖先进入素材页。参考编号覆盖在缩略图底部，移除按钮保持22px圆形。

模型目录、偏好读取失败使用公共 Message，重试保留在原保存/详情入口，组合框提供错误态；正常读取与能力核对仅使用 loading，不在组合框或提示词后追加 Alert。初次未选模型不自动弹警告，禁用原因可通过聚焦发送入口查看。空稿和梗概分支同样使用无布局反馈；梗概按钮为“保存梗概”，不暗示已经支持梗概生成。导入剧本先只读预览，采用后保存；文件读取时显示loading、禁止重复操作及关闭，格式/读取失败归属当前弹窗并保留粘贴内容。分类、时长、去重和恢复规则见[标准创作流程反馈规则](../docs/design/创作反馈规则.md)。

### Navigation

当前位置有明确选择底，其他入口安静排列。

通用侧栏按钮包含线性图标与中文名称，当前入口使用 `aria-current`、选择底和边线；素材分类按需展开。创作模式顶部采用 60px 单层三列 Grid，四步导航几何居中，只展示编号和名称；24px 圆形编号与步骤间连接线表达顺序，当前项使用 8px 圆角选择面与完整边线，并通过 `aria-current="step"` 表达。步骤不显示完成状态、勾选、计数或画幅风格，作品状态在当前编辑区域表达。手机步骤仅在自身容器横向滚动。顶栏左侧保留返回列表和可原位编辑的作品名称，名称桌面与手机均可见，长名称省略且有完整标题；名称编辑只提交 name/row_version，串行保存及409恢复使用公共反馈入口。移除分集选择和项目资料设置，不以隐藏层或占位分集维持导航。导航悬停提高表面明度，键盘焦点与按钮一致。

### Dialogs / Drawers

操作层有完整的标题、正文和出口。

原生Dialog采用浮层中性灰、控件边线、面板圆角及结构性阴影；背景遮罩为黑色半透明层（`rgb(0 0 0 / 72%)`）。标题区有关闭按钮，正文按用途滚动。标准创作通过`standard-creation-dialog`、`standard-creation-popup`及`standard-creation-drawer`显式限定局部浮层；普通正文默认16px、页脚纵向12px，成片局部沿既有规则统一到相邻基线。普通成片操作弹窗宽度受限（560px），历史弹窗较宽（820px），并为窗口四周保留空间，不把所有浮层强制成同一宽度。

“小说原文”抽屉标题区内边距10px 12px，工具栏与页脚为8px 12px，正文textarea横向留白12px。工具栏按钮为“导入小说”，仍仅接收最多1 MiB的TXT，按UTF-8／GB18030解码，替换前保留本机副本。

应用确认使用统一样式和Ant Design按钮，说明对象与影响，先聚焦取消；嵌套弹窗关闭后恢复原入口焦点。Dialog包含可见控件的Tab循环、Escape关闭及内部下拉层挂载；提交期间按业务规则阻止关闭。Escape先关闭当前下拉或顶层浮层，不同时收起底层设置/助手。标准模式应用内导航不使用保存确认框；刷新或关闭浏览器时，仅内存且未可靠保留的输入由`beforeunload`保护。

弹窗入场为短暂淡入与上移复位（180ms，从 6px 位移返回，`--ease-out`）；Ant Design 中、慢运动为 0.18 / 0.24s。减少动态效果偏好由 `useReducedMotion` 订阅并交给 `StudioProvider`，关闭 Ant Design 运动；原生弹窗与抽屉关闭入场动画，滚动改为即时，颜色与边线反馈仍保留。

### Editing Selection

选中对象的位置稳定，内容与候选有清楚边界。

创作模式的素材与分镜均使用单列固定高度媒体卡片，下方单行显示名称及类型或景别，长名称省略并提供完整名称。素材卡封面只取当前采用图片；镜头卡随图、视频标签显示对应当前媒体及待制作状态，镜号和脚本时长放在媒体区。待制作标记位于媒体左下，选中标记只表达当前编辑对象；卡片不展开，也不常驻采用状态文字。

素材列表标题、筛选与卡片区横内距8px，卡间距8px，说明区34px，各断点卡片比原高度减少16px。桌面列表宽按视口为252／228／204／192／184px，手机保留纵排。列表标题桌面44px、手机48px；素材对象标题桌面45px，设置标题44px，两者手机均52px。设置正文10px、信息组距及参考图区间距8px。提示词框最小220px并填满设置余高，短窗口允许设置区滚动。预览内距8px；未生成时仅显示“还没有生成图片”，不显示图位序号、默认主图说明或设置按钮。实际生成组保留固定图位、主图和图片工具；移除素材预览常驻数量/刷新栏，生成中与部分失败通过原图位及详情表达，不增加独立状态行；失败图片提供原位重新读取。

素材列表与预览主面板间距固定8px。提取入口直接使用按钮图标，悬停深灰底、亮色图标，避免外置图标被背景遮挡；预览标题不再展示“已生图”状态标签。参考图空态与已有图片时共用64×58px虚线加号块，缩略图按实际提交顺序标记“图片N”。提示词输入`@`显示缩略图引用列表，支持过滤、方向键、Enter/Tab及Escape；选中后显示22px行内标签和18px缩略图。菜单宽最多320px，手机受视口限制，键盘关闭菜单不连关设置。共享图片在前、私人图片在后，按媒体ID首次去重；删除图片时同步移除其引用并重排后续编号，提示词和私人参考图一次保存。

分镜沿用素材的8px列表内距与卡距、34px名称行和缩小后的卡片规格。列表标题桌面44px／手机48px，作品标题及设置标题桌面44px／手机52px；图／视频切换位于镜头标题栏内，采用32px高的双分段控件，手机44px，保留tab语义和方向键、Home／End切换。标题栏空间不足时内部两排排布，不另设独立标签行。页面与预览内距8px，设置正文10px、字段组距8px。首次无明确选择或待恢复编辑时静默选首个可见镜头，不自动打开手机设置。未生成图片／视频仅显示对应短空态，读取失败独立重试；已有图片保留固定图位、主图及工具。主视频播放器填满预览区剩余宽高，画面按原比例显示，不保留播放器下方的独立空白栏；历史视频维持记录弹窗尺寸。分镜生图参考区共用64×58px加号块及素材的`@`缩略图菜单／行内标签；关联素材按ID升序、共享参考、私人参考依次首次去重，缺预览URL仍保留编号，引用变化与提示词一次保存。视频仍只引用当前分镜主图。

素材新增直接追加临时卡片，补齐名称和类型后原位接入服务端对象；镜头新增直接追加空白镜头，名称可为空且不出现必填提示。作品信息与私人参数分别自动保存，顶部使用单个可聚焦、非模态的固定尺寸保存图标，通过可访问名称、悬浮说明与详情表达保存中及异常计数；不弹自动保存成功提示，详情提供重试与核对入口。分镜主面板只有“分镜图 / 分镜视频”两个模式，镜头名称独立于脚本，景别仅作卡片元信息；前后镜头切换与已有隐藏字段保留。剧本通过“采用并编辑”进入当前稿；图、视频预览标题栏均不展示状态标签；视频成功归档后自动确定，不显示采用按钮、未采用角标或结果操作栏，主预览只读取服务端镜头视频，历史仅供播放。素材图和分镜图生成后直接保存完整图片组、第一张默认主图，不增加图片候选采用步骤。生成记录按需弹窗查看。

成片时间轴的浅灰选中轮廓覆盖缩略图，标签使用近白填充与深色文字，播放头使用浅灰；专注剪辑收起外围导航并保留退出与保存反馈。

成片使用满高剪辑工作区，区域间距8px。标题行组合画幅、片段数、时长及声音、同步、历史、专注和导出图标入口；声音能力载入、失败或不可用时不隐藏同步入口。常用剪辑操作使用带悬浮说明和可访问名称的图标，前后移动与恢复完整放更多菜单。预览与成片共用紧凑播放控件，版本信息、刷新与下载融入播放工具，不增加独立说明栏；时间轴动态反馈覆盖在原轨道内。

成片为一条连续画面轨及原声、对白、字幕、配乐、音效五类辅助轨，共用播放头；辅助轨显示真实波形或字幕块，支持拖动、裁剪、锁定、静音及折叠空轨。多轨高度280–360px，滚动限制在轨道内，不随任务提示改变。原声、对白、字幕、音效默认关联镜头，配乐默认固定整集位置，关联入口使用图标及明确的悬浮说明。

画面、声音和字幕属性在右侧280px可收起设置中编辑，内距10px、组距8px；1200px及以下使用统一Dialog，提供焦点循环、Escape和回焦，背景不可交互。正文独立滚动；选中对象显示相应属性，字幕即时保存输入，秒数保留三位小数并转整数毫秒提交。声音设置包含音量、响度、裁剪、循环与淡入淡出；画面提供适应／填充、缩放、位置及仅预览的安全区。旧配音面板继续保留生成、试听和明确采用，打开前保存且不与新设置重叠编辑。

素材入轨、短视频替换确认和字幕识别各用紧凑Dialog。识别结果先预览／导出SRT，再明确应用；记录、取消及核实位于当前操作层，不在工作台追加说明块。默认本地识别，云端模型需要主动选择。导出清晰度集中于原导出弹窗，修改先保存再冻结快照；不新增导出前检查步骤。播放器、轨道手动高度与本机尺寸偏好保留，当前成片仍需明确选择。详见[成片设计约定](docs/assembly-surface.md)。

### AI 创作助手

创作页顶部保存状态右侧固定提供“AI 创作助手”，不展示创作方式切换。标准与画布助手统一使用 Ant Design X 的 Bubble、Sender、Suggestion、Prompts 和 Conversations 组织内部界面，消息正文为 13px，保留安全 Markdown；流式回复与最终消息使用相同的气泡几何。可移除作品引用固定在输入框上方，不随历史消息滚动；输入贴底，底部保留模型选择及发送／停止按钮，回复等待状态显示在消息区。标准桌面助手默认 380px，可拖动左侧分隔条在 320–560px 内调节，受主编辑区至少 640px 及间距约束；宽度按账号本机记忆，分隔条支持方向键、Home／End 和可见焦点。打开时主面板让出空间，选择对象、编辑及展开设置保持助手可见；关闭后主面板恢复宽度。画布保留自己的外层容器、明暗主题、宽度拖动与窄屏抽屉，内部组件按可用宽度自适应，不引入宿主全局样式。窄屏头部按钮宽高至少 40px，完整容纳于标题栏内；抽屉支持 Escape、Tab 循环和关闭后焦点恢复。手机与设置互斥，各自输入保留。

会话以项目为主体，标题栏保留新对话、记录与关闭；记录提供当前项目的新建、选择、搜索、永久删除和重命名。切阶段不更换项目会话，跨项目恢复各自会话及草稿；原任务目标在受理后冻结。旧“历史创作记录”只读。模型旁依次放 32px 添加和 Skill 图标，手机至少 40px；添加菜单列出图片、文本、当前画布及资产库，创作模式禁用当前画布项并就近说明。Skill 使用锚定列表，搜索、加载更多和复选直接生效，保留版本及不可用原因；`/` 的“浏览全部 Skill”复用此列表；Enter 确认后，方法在输入框内显示为可移除的高亮块。外点关闭，Escape 关闭并回焦图标，列表内可键盘操作。删除附件展开行、本人可见说明、任务设置、上下文管理和记忆管理入口及弹窗；附件、引用、方法标签与错误仍就近可见，上传重试和恢复继续可用。局部精修通过 `layout="standard"` 传递专属标记，标准助手样式限定于 `.ai-assistant-standard` 及 `.standard-assistant-*`，保留画布主题。回看历史时新回复不强制滚到底部，通过“查看最新消息”返回；载入更早消息保留当前阅读位置。素材卡点击或 Enter／Space 选择对象，预览图片另有入口；切换对象立即生效，后台继续原账号与资源的串行保存，失败保留原编辑会话。

`.impeccable/design.json` 展示原生按钮、字段、导航、筛选、状态、项目条目、分镜摘要与时间轴片段。片段的变量带当前值回退，可在独立 shadow DOM 中展示；色阶仅供色板预览，由已提取颜色推导，不构成新增实现变量。应用组件的状态逻辑仍由实际 React 组件承担。

## Do's and Don'ts

### Do:

- **Do** 从共享颜色角色与组件 token 延续中性炭黑工作台，保持正文、镜头和候选优先。
- **Do** 用明确中文标签、文字状态和就近反馈说明保存、生成、候选与当前采用。
- **Do** 保留可见键盘焦点、图片预览入口、弹窗内滚动和固定操作区。
- **Do** 在桌面窗口收缩时重排功能组，并把宽表与长历史的滚动限制在自己的容器内。
- **Do** 对永久删除使用有对象、有后果的应用确认；日常导航、恢复版本选择与采用剧本不追加确认，替换输入前保留可找回副本。

### Don't:

- **Don't** 恢复已替换的浅色青绿视觉体系，或把编辑工作台改为营销首屏。
- **Don't** 用过大的控件、重复卡片阴影、虚构统计或无业务用途的装饰面板挤占编辑空间。
- **Don't** 把内部媒体 ID 作为可见输入，或让用户靠填写 ID 完成图片选择。
- **Don't** 仅靠颜色表达确认、错误或候选采用，也不要把步骤序号视为完成状态。
- **Don't** 自动采用需要用户选择的剧本候选、给直接保存的图片组或自动确定的分镜视频额外插入采用步骤、隐藏失败后的输入，或静默覆盖版本冲突。

## 无限画布的源视觉边界

本文件的主题、布局和组件替换约定适用于标准工作台。无限画布按用户明确的一比一要求，使用 `frontend/canvas/` 独立子包中的 BeefTV Provider、CSS、字体、组件和交互实现，不套用标准页面的全局样式，也不重排源工具栏与节点面板。按用户后续调整，共同声明但版本不同的直接依赖采用宿主版本，源独有依赖仍由画布自己的锁文件固定；兼容适配不能改变原操作流程。新模式选择仍使用宿主现有创建弹窗风格。验收要在依赖对齐后重新核对源/目标的同环境截图、控件几何和操作轨迹，源码哈希、旧版本截图或构建通过不能替代本次视觉验收。


## 创作模式单层边界（2026-10-21）

创作模式沿用冷黑、紧凑的工作台主题和 60px 顶栏；四阶段导航、保存状态与助手位置保持稳定。作品名称在原位置编辑，不新增设置面板。私人剧本梗概来源继续保留，删除的是项目资料梗概。图片与视频的局部画幅默认 `null`（模型默认），不继承作品级默认值。成片导出提供 auto/16:9/9:16，播放器按服务端返回的实际宽高展示。

全局素材库、项目资料/资源库、共享协作、跨库素材导入和手动分镜归档均退役。当前作品素材的生成、媒体资产库选择、参考图与图片组交互继续沿用既有组件。无限画布保持自己的素材、角色、画风、保存与回收边界，移除标准来源项目、分集及共享角色目录，不引入创作模式的候选采用步骤。

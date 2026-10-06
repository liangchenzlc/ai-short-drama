# 全工作台界面重塑

Mode: Operate

## Direction contract

THESIS: 用黑色剪辑工作台承载从小说到成片的制作流程。正文、镜头和候选居于核心；不增加虚构统计或没有业务用途的装饰面板。

OWN-WORLD: 参考 `frontend/canvas` 默认暗色主题的炭黑画布与中性灰表面，白底深字主操作、灰色选择面与浅灰焦点，独立的成功/警告/错误语义色。34px 控件、7px 控件圆角和 12px 内容面板圆角，统一线性图标与中文界面。

STORY: 用户找到项目，进入分集，编辑并确认内容，核对素材和镜头候选，剪辑和导出。载入、保存、候选、采用和失败都就近反馈。

FIRST VIEWPORT: 200px 左侧导航、52px 工作区顶栏、紧凑页面标题与操作。项目内容为可扫描的制作条目，右侧继续创作。分集保留四步导航，编辑区获得剩余空间。

FORM: 用户指定参考现有无限画布的色调，调整标准工作台与公共页面的配色。保留既有布局、字体、尺寸、圆角和交互，显式核对 Ant Design 与原生控件的前景、填充和状态。无限画布继续使用其独立主题与样式。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## 验证边界

只改前端。浏览器验证拦截 API 和外部请求，生成与模型探测使用测试替身，不访问用户的模型或修改服务端数据。验证范围包括项目/分集、四个制作步骤、素材、任务、媒体、AI 配置、账号及弹窗/抽屉，并覆盖桌面与窄屏。配色检查使用实际计算颜色，重点核对主按钮的默认/悬停/键盘焦点/按下态、选中时间轴标签和正文选择；测试替身通过不代表真实模型或生成供应商验收。

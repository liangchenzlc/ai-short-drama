# 全工作台界面重塑

Mode: Operate

## Direction contract

THESIS: 用黑色剪辑工作台承载从小说到成片的制作流程。正文、镜头和候选居于核心；不增加虚构统计或没有业务用途的装饰面板。

OWN-WORLD: 黑色画布、三层冷灰表面、蓝紫操作与选择色、独立的成功/警告/错误语义色。34px 控件、7px 控件圆角和 12px 内容面板圆角，统一线性图标与中文界面。

STORY: 用户找到项目，进入分集，编辑并确认内容，核对素材和镜头候选，剪辑和导出。载入、保存、候选、采用和失败都就近反馈。

FIRST VIEWPORT: 200px 左侧导航、52px 工作区顶栏、紧凑页面标题与操作。项目内容为可扫描的制作条目，右侧继续创作。分集保留四步导航，编辑区获得剩余空间。

FORM: 用户授权自主选择方向，指定炫酷黑色与紧凑桌面自适应。上下文加载器不可用；从现有代码、浏览器截图和两项技能检索建立设计。不使用模型生成图片；公开 GitHub 参考因网络不可用未纳入已观察证据。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## 验证边界

只改前端。浏览器验收拦截所有 API 和外部请求，生成与模型探测使用测试替身，不访问用户的模型或修改服务端数据。验收覆盖项目/分集、四个制作步骤、三类素材、四类任务、两类媒体、AI 配置及弹窗/抽屉，桌面宽度 1024 / 1280 / 1440 / 1920。

# 成片时间轴实施与验收

实施日期：2026-09-29。用户要求的大播放器、分镜视频轨道、分割和裁剪已落地，入口为分集创作流程第四步「成片合成与导出」。

## 已交付行为

- 大播放器连续查看剪辑，支持定位、逐帧、全屏；轨道游标与画面联动。
- 分镜素材添加或拖入单视频轨道，按时长显示片段；拖动排序、两端裁剪、键盘逐帧裁剪、分割、删除、静音。
- 删除自动收拢间隙；同一视频可多次使用，分割保留共同边界。会话内最多 100 步撤销/重做，已保存编辑可刷新恢复。
- 自动保存、幂等请求回执、版本冲突保留本地草稿；显式同步新素材保留既有裁剪。
- 后台 360p 合成预览、正式 MP4 导出、下载与采用。实际结果在大播放器查看，配套冻结的只读轨道；编辑后标记结果过期。
- 真实缩略图与低清代理用于剪辑，原素材用于导出。新快照统一 30fps；短片段的音频在拼接后统一 AAC 编码。

## 验证记录

| 检查 | 结果及证据范围 |
| --- | --- |
| 前端 Node 测试 | 113 项通过，包含帧覆盖、边界分割、裁剪、排序、保存和冲突 |
| 前端生产构建 | TypeScript 与 Vite 构建通过；剪辑器独立延迟加载。主应用/AntD 仍有大于 500kB 的构建提示 |
| 后端单元/API 回归 | 初轮 515 项通过；最终时间轴文件 5 项通过，含新增的 31 次预览后保留成功导出记录回归；原成片文件 5 项通过 |
| MySQL 集成 | 3 项通过：完整 SQL 与 ORM 一致、HTTP 编辑/分割/幂等/实际编码/上传失败重试/下载/采用、旧表迁移重复执行保留数据 |
| 真实 FFmpeg | 6 个短片段拼成 24 帧、800ms；解码前 800ms 音频为 38,400 个 48kHz 单声道采样，静音段能量与有声段分别验证；实际生成代理与 JPEG |
| 浏览器交互 | 实际鼠标拖动排序、边缘裁剪、素材拖入，分割、数字裁剪、删除、撤销/重做、刷新、跨片段播放、导出/合成预览模式通过 |
| 浏览器边界 | 模拟 409 保留编辑；300 个 12 秒片段共 1 小时可加载并定位到 3599 秒，播放器固定 2 个 video 元素。一次本机观测为加载 643ms、定位 575ms，非生产性能承诺 |
| 全屏和响应式 | 1440px 桌面、390px 手机检查；全屏可播放/暂停/逐帧/退出，可在播放中进入，手机无页面横向溢出 |
| 静态检查 | 本次 Python 文件 Ruff 通过，git diff --check 无空白错误 |
| 本地启动 | 已执行时间轴迁移并备份成片表，重启 API/调度器/render；数据库和 MinIO 健康接口通过，真实分集第四步已加载 |

浏览器自动化使用隔离 API 测试替身和 FFmpeg 合成测试视频，未调用 AI 生成供应商。MySQL 集成使用真实临时数据库、ASGI HTTP 与实际 FFmpeg，存储为内存替身，直接调用渲染执行器；不等同于 RabbitMQ/MinIO 故障注入测试。真实本地页面检查及健康接口补充验证实际服务连通性。

Impeccable 独立复核对本轮 UI 修复清单给出 **ship**：全屏控件及过时提示均 resolved；该结论限定于修复清单。复核读取截图与代码，没有独立重跑自动化。设计检测器未产生可用报告，不宣称自动设计审计通过。产品与表面文档另由独立 documenter 核对。

## 复现浏览器验收

先启动前端 8080 端口，安装 Playwright CLI 与 Chromium。在仓库根目录准备隔离测试页和视频：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File frontend/scripts/prepare-timeline-acceptance.ps1
npx --package @playwright/cli playwright-cli -s=timeline-editor open http://127.0.0.1:8080/.runtime/timeline-editor.html
npx --package @playwright/cli playwright-cli -s=timeline-editor run-code --filename=frontend/scripts/timeline-acceptance.mjs
npx --package @playwright/cli playwright-cli -s=timeline-editor run-code --filename=frontend/scripts/timeline-interactions.mjs
npx --package @playwright/cli playwright-cli -s=timeline-editor run-code --filename=frontend/scripts/timeline-drag-acceptance.mjs
npx --package @playwright/cli playwright-cli -s=timeline-editor run-code --filename=frontend/scripts/timeline-edge-acceptance.mjs
npx --package @playwright/cli playwright-cli -s=timeline-editor run-code --filename=frontend/scripts/timeline-fullscreen-acceptance.mjs
```

这些 `.mjs` 是 CLI 读取的函数，不属于 `npm run test:e2e` 收集范围。`.runtime` 视频与 `.impeccable/review` 验收截图不提交。旧列表编辑器的 `assembly-acceptance.mjs` 已注明停用。

## 使用边界

当前为单视频轨道、30fps、最多 300 个片段；导出时长上限默认 1 小时。素材应先在分镜中生成并采用，缺视频或裁剪越界会明确阻止导出。即时剪辑预览切镜仍取决于浏览器解码和网络，后台合成预览用于检查实际拼接结果。后台预览与正式导出共用单并发 render，正式导出优先投递但不抢占已运行任务。

本轮未添加字幕、多音轨、转场、变速或特效。撤销历史限当前会话；刷新后保留服务端保存结果。素材区为单张代表图，时间轴已在分步优化 2 中增加连续采样缩略图，最多每个来源 24 帧，不代表逐帧精确画面。

运行升级见[迁移说明](../数据库模型/migrations/2026-09-29-assembly-timeline/README.md)，协议见[成片 API](../api/episode-assembly.md)，依赖来源见[时间轴依赖](../../frontend/docs/timeline-dependencies.md)。

## 分步优化 1：专注剪辑与可调整布局

本轮为既有工作台的布局扩展，未改变后端及数据库。新增专注模式，收起分集外围标题、流程栏和步骤导航，退出时恢复原折叠状态；退出按钮与保存状态保留在标题栏。素材和属性可独立折叠，桌面播放器及时间轴支持指针和键盘调整高度。布局偏好本机持久化并可重置，窄屏使用自然布局。

本轮验证：

- `npm run build` 通过，113 项前端 Node 测试通过；现有主应用/AntD 大包提示仍存在。
- `timeline-focus-shell-acceptance.mjs` 在真实分集页面检查流程栏隐藏/恢复、键盘焦点返回和宽度变化；该次 1440px 视口中播放器区域从 980px 增至 1170px。只调整布局，没有编辑真实分集。
- `timeline-layout-acceptance.mjs` 在隔离 API 测试页使用真实本地视频，验证播放不中断、DOM 播放器不重建、位置保持、面板开关、指针/键盘调整、刷新恢复、布局重置，以及布局操作前后 API 草稿完全一致。
- 同一脚本验证 1440px / 900px / 390px 无页面横向溢出，窄屏不显示桌面拖动条，弹窗 Esc 不同时退出专注模式。响应式断言等待 React 更新完成后执行。
- `timeline-interactions.mjs` 回归分割、删除、撤销/重做、添加、裁剪、排序、保存、跨片段播放和结果模式切换，通过。
- 桌面及手机截图已检查：`.impeccable/review/timeline-focus-desktop.png`、`timeline-focus-mobile.png`，画面为合成测试素材。本轮为主代理布局与交互核验，未新增独立审美复核；检测器无可用输出，不作为通过证据。

使用前述测试素材准备步骤及 `timeline-acceptance.mjs` 初始化，再以 Playwright CLI `run-code --filename=frontend/scripts/timeline-layout-acceptance.mjs` 运行布局验收。流程栏集成脚本需先打开任一已有成片草稿的真实分集页面，再执行 `timeline-focus-shell-acceptance.mjs`；脚本会将测试浏览器的剪辑布局重置为默认。

## 分步优化 2：移除属性区与时间轴反馈

按用户反馈移除片段属性区、属性开关和重置布局功能，以及对应本机配置字段。原先属性区的数字裁剪改为轨道边缘拖动和键盘逐帧裁剪，静音/恢复原声与恢复完整保留在工具栏。

新增连续缩略图：FFmpeg 从预览代理均匀采样最多 24 帧，生成 160×90 每格的 JPEG 拼图并归档，GET 返回 `filmstrip` 元数据。前端按当前裁剪来源时间选格，只创建可见范围内的图像元素，每片段最多 32 格；不新增视频解码器。已有草稿打开时幂等补齐缩略图，不同步来源或覆盖剪辑，生成中或图片加载失败时回退到代表图。无需数据库迁移。

裁剪显示来源入点、出点和保留时长，越界提示且不提交无效裁剪；松开后保存。Alt + 左右键逐帧调整出点，加 Shift 调整入点。排序时显示插入线和目标位置，不发生实际顺序变化时明确提示。缩放保持播放头屏幕位置，播放头不可见时带回视口中部；适应全部返回起点，刻度间距按缩放调整。

验证结果：

- 116 项前端测试与最终生产构建通过；大包提示保持原有范围。新增测试覆盖缩放锚点、长时间轴刻度、裁剪来源到缩略图格的映射和可见格数上限。
- 6 项时间轴后端测试通过，包括真实 FFmpeg 多时间点采样、拼图尺寸及片段帧数/音频回归；原成片测试 5 项通过。Ruff 通过。
- 3 项真实 MySQL 集成通过，验证探测后的拼图元数据以及已有分割草稿再次初始化不修改版本或片段。
- 浏览器 `timeline-layout-acceptance.mjs`、`timeline-readability-acceptance.mjs`、`timeline-interactions.mjs` 和 `timeline-edge-acceptance.mjs` 均通过，覆盖已移除控件、播放保持、缩放位置、真实缩略图加载、实时裁剪提示、拖动排序落点、保存/撤销、静音、结果模式和 300 片段/1 小时轨道。
- 桌面与 390px 手机截图已检查，无页面横向溢出。浏览器流程使用 API 测试替身及真实合成测试视频。
- 已重启本地 API/调度器/render。在真实现有分集验证两个来源自动补齐拼图，草稿版本仍为 11；页面 7 个可见缩略图图像均成功加载，属性区及重置按钮均不存在。这一检查走实际本地队列与媒体存储。

复现新增交互验收：先运行素材准备脚本及 `timeline-acceptance.mjs`，再通过 Playwright CLI 执行 `run-code --filename=frontend/scripts/timeline-readability-acceptance.mjs`。布局测试现在直接清理测试浏览器中的布局偏好进行初始化，不再调用已删除的重置按钮。

## 可靠性与操作效率：当前提交进度

素材区已按分镜编号升序排列，轨道选中片段增加覆盖缩略图的边框与文字标记。新增吸附开关、播放头边界吸附、上一段／下一段及悬停和聚焦详情；保留片段拖动与裁剪吸附。预览增加定位意图保留、缓冲超时、地址刷新重试及后台标签页暂停；成片播放器增加加载与重试反馈。

真实 FFmpeg 检查复现了 24 fps 素材在输入端定位后再转 30 fps 导致的取帧偏差。现在按完整来源的 30 fps 帧坐标裁剪，音频使用对应的采样范围，并更新片段缓存签名。扩展验证覆盖 24 fps、29.97 fps、可变帧率，360p／720p／1080p，以及音画脉冲与静音片段，4 项通过；此前相关后端 13 项通过。前端构建通过，全量 118 项检查中的吸附阈值边界失败已修复，针对性 5 项复查通过，全量尚未重跑。

本次按用户要求先提交当前进度，尚未完成最终验收：浏览器脚本已走过跳转、吸附、详情、快速逐帧、跨片播放、静音及预览地址重试；在模拟成片错误后等待“刷新成片并重试”按钮时超时，需排查事件时序或实现问题。最终桌面／窄屏截图检查、扩展长轨道回归及本轮后端服务重启尚未完成。此提交不代表两个优化任务已全部验收。

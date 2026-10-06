# BeefTV 绘图空白场景的严格视觉诊断（2026-10-06）

固定源 commit：`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。

本诊断阶段只新增临时诊断文件，没有改动产品 CSS、源文件、正式对照脚本、0px 阈值或 mask，没有启动 native 数据库、Python API 或供应商 fixture。目标 Vite 使用端口 4184，诊断结束后已经关闭；源只读 Vite 3000 保留。

## 结论

正式门禁仍为 17 场景几何相同，16 场景 0px，`drawing-editor-empty` 有 4px。不能声明完整一比一验收通过。

4 个点 `(443,137)`、`(996,137)`、`(444,140)`、`(995,140)` 在 Excalidraw toolbar 阴影外沿，差异为灰阶 17/18。固定源自身不同运行之间也出现相同 4px，故不能直接归因为迁移 CSS。现有资源与 DOM 审计没有查出字体、Excalidraw 包或最终 toolbar 布局差异；目前未获得稳定且可验证的产品修复。

另有一条独立且已采证的尺寸问题：Excalidraw 在弹窗进入动画期间读取 `getBoundingClientRect()`，偶发把缩放中的尺寸保留为内部 canvas CSS 与 bitmap 尺寸，而外层几何早已稳定。这解释软件栅格实验中的右侧/底部新缺口，但不能解释原 4 个阴影点。正式几何采样没有包含内部两个 canvas，因此未发现这一状态。

## 资源与最终样式

- 两侧 `@excalidraw/excalidraw` 都为 0.18.1，包内 JS/CSS/WOFF/WOFF2 SHA 全部相同。
- 两侧 Inter、JetBrainsMono 都为 5.3.0，字体文件及 CSS SHA 相同。
- 源 React 19.2.7，目标 React 19.3.0；依用户要求保留宿主版本，未降级。
- Toolbar Island 两侧最终 rect 都为 `(445,100,550,44)`；所有非 CSS variable computed styles、rect、pseudo、字体列表一致。
- Toolbar `box-shadow` 两侧为 `rgba(0,0,0,.17) 0 0 .931014px 0, rgba(0,0,0,.08) 0 0 3.12708px 0, rgba(0,0,0,.05) 0 7px 14px 0`。
- 诊断阶段曾发现 modal transform-origin 有点击位置与中心的不同，最终 transform 都为 none；没有证据证明它直接导致 4px。source 后续采样也出现两种 transform-origin，但像素一致。

## 3 源 + 3 目标 fresh-context 窄实验

以下均复用正式交互步骤与稳定捕获，只记录绘图空白场景。

| renderer | 实际结果 | 结论 |
| --- | --- | --- |
| 默认 AMD610M / ANGLE_D3D11 / GaneshGL | 3 源相同，3 目标分别 4 / 0 / 4px | 原阴影点会在同目标代码不同上下文波动 |
| `--disable-gpu` + sRGB | 4 阴影点都相同，但一个目标有 11042px 右侧/底部新差异 | 不能直接采用 CPU flag 作为修复 |
| SwiftShader + sRGB | 源自己有 4px 波动；3 目标相对源有 73px toolbar SVG 图标差异 | 不能直接采用 SwiftShader 作为修复 |
| 仅 `--disable-gpu-rasterization` + sRGB | 阴影点全相同，两个目标 0px，一个目标 2234px | GPU compositing / 2D canvas 保留，仍不能稳定全图 0px |
| 默认 AMD，baseline 后派发 resize，二次严格截图 | 5 组 0px；target-2 前后仍 4px；所有 bitmap 均 1408×780 | resize 不解决原阴影差异，不宜将它当成修复 |

仅禁 GPU rasterization 的 target-2 两个内部 canvas：bitmap `1406×779`，CSS `1406.53125×779.1875`；其他 5 次均为 `1408×780`。同一次 target-2 的外层/modal rect 仍为 `1408×780`，transform none。缺口差异 bounds 为 `[21,84,1424,864]`。

## 可审查的后续方向

若处理内部尺寸问题，可在目标 drawing modal 的 `afterOpenChange(true)` 后才挂载 Excalidraw，避免首次 ResizeObserver 在 transform 中读取缩放尺寸。需要分别覆盖数据先完成/弹窗先打开、关闭重开、窄屏与加载错误，且仍必须对照原视觉、保存与操作；此候选尚未实现或验证。

不推荐 `api.refresh()`：已读 0.18.1 实际声明与实现，它只更新 offsetLeft/offsetTop，不刷新 width/height。内部 `onResize` 才调用 `updateDOMRect()`，全局 resize 派发有额外影响，并且本次实验表明它不消除阴影 4px。

原 4px 必须继续保留为未解决项。不能改源、改 CSS 阴影、加 mask、放宽阈值、改依赖版本或反复运行全 17 场景以碰到一次 0px 后声明解决。

## 证据

- `frontend/canvas/.runtime/drawing-paint-audit/`：全 computed styles、字体、bitmap 像素与初始严格 0px 截图。
- `frontend/canvas/.runtime/drawing-raster-default/comparison.json`
- `frontend/canvas/.runtime/drawing-raster-cpu/comparison.json`
- `frontend/canvas/.runtime/drawing-raster-swiftshader/comparison.json`
- `frontend/canvas/.runtime/drawing-raster-software-raster/comparison.json` 及 `drawing-editor-empty.target-2.bitmap.json`
- `frontend/canvas/.runtime/drawing-raster-default-remeasure/comparison.json`：原图及 resize 后图分别比较。
- `.runtime/drawing-raster-software.log`、`.runtime/drawing-raster-default-remeasure.log`：进程 exit0 仅表示诊断执行成功，不表示视觉门禁通过。

新增本地诊断脚本：`.runtime/prepare_drawing_raster_probe.py`、`.runtime/compare_drawing_raster.py`、`.runtime/compare_drawing_bitmaps.py`、`.runtime/compare_drawing_remeasure.py`。全部不属于产品变更。

本文件只归档上述诊断结论，不修改正式门禁。证据目录与临时诊断脚本是本机复查材料，不作为需要提交的产物；不保证其他机器保留同一路径。

# 时间轴组件与参考实现

运行依赖 `@xzdarcy/react-timeline-editor` 1.0.0（MIT），由 npm 锁文件固定实际依赖版本。使用其刻度、游标、拖动、缩放及左右拉伸交互；业务状态、磁吸式连续排序、来源裁剪、分割、保存和渲染规则由本项目实现。其依赖 `@xzdarcy/timeline-engine` 的类型仅用于适配。

上游：<https://github.com/xzdarcy/react-timeline-editor>。该版本 npm 包声明 MIT，但未携带 LICENSE 文件；已将上游原文保存在 [THIRD_PARTY_NOTICES.txt](../public/THIRD_PARTY_NOTICES.txt)，Vite 构建会复制到发布目录。发布分发应保留依赖许可证。本次没有复制 OpenCut、Remotion 或 Editly 源码。

调研参考：

- OpenCut classic `cf5e79e919144200294fb9fed22a222592a0aeea`：独立片段实例、分割共用边界、撤销和播放管理。MIT。
- Remotion `d458be6a37a0259979507eaee6f3bfaf314bfedb`：帧时间轴、播放器定位和缓冲；专用许可证，未引入运行依赖或复制源码。
- Editly `dc46674052eacd15ef6e0f563a58eb378b3804ad`：声明式剪辑与视频/音频统一渲染。MIT，未引入其 Node 渲染服务。

React 19 的兼容性通过本项目 StrictMode 浏览器验收：片段拖动、边缘裁剪、素材拖入、缩放、播放指针和组件刷新。库类型不作为 API 持久化协议，后续可以替换 UI 组件。

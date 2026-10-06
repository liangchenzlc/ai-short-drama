# 模型 Logo 原始子集

来源为 `@lobehub/icons@5.16.0` 的 npm 发布包，许可证见 [LICENSE](LICENSE)，原始路径及 SHA-256 见 [upstream.json](upstream.json)。

仅保留画布模型 Logo 选择器实际使用的 321 个 Mono SVG 组件、目录数据及其必要的样式常量/辅助模块，文件内容未修改。保留原始目录数据以维持名称与排序；选择器继续按需加载 Mono 组件。

该版本的完整 npm 包声明 Ant Design 6 与 LobeHub UI 5 的 peer dependencies；本子集仅依赖 React 和 `es-toolkit`，用于在宿主 Ant Design 5 下保留原版所有 Logo。不要通过升级宿主或减少 Logo 数量解决依赖冲突。

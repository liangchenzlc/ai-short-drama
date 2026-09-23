# 前端

React 19 + TypeScript + Vite + Ant Design 网页工作台。项目、正文、素材、分镜、生成任务和采用结果通过 Axios 连接后端；模型选择保留为浏览器偏好。无需 Electron。

## 运行与检查

Node.js 20.19+ 或 22.12+，在本目录执行：

```powershell
npm ci
npm run dev
```

开发地址 `http://127.0.0.1:5173`，API 默认代理至 `http://127.0.0.1:8000`。修改代理时使用 `.env.local`，参照 [.env.example](.env.example)，禁止在 VITE 变量放凭据。

```powershell
npm test
npm run typecheck
npm run build
npm run preview
```

`build` 包含类型检查，产物为 `dist/`；当前Vite preview会继承开发配置的API代理；静态dist文件本身不含代理能力，正式部署仍需配置反向代理。Node测试覆盖保存队列、导航、版本冲突、模型选择、生成契约和部分UI源码约定，不代替浏览器交互测试。

## 路由

| 路径 | 页面 |
| --- | --- |
| `/projects` | 项目搜索、分页和继续创作 |
| `/projects/:projectId` | 项目信息、分集、项目资源库 |
| `/projects/:projectId/episodes/:episodeId/:stage?` | 分集制作；stage为source/script/assets/storyboard |
| `/assets/:kind` | character/scene/prop三类全局素材 |
| `/ai` | 文本、图片、视频模型配置 |
| `/tasks/:kind` | text/image/video生成任务 |
| `/media-library/:kind` | image/video生成媒体资产 |

旧video和无效步骤链接按当前待处理阶段规范化；旧快照算出的video阶段映射到storyboard。BrowserRouter使用真实路径；生产部署要配置API代理、SPA fallback及正确的Vite资源base，详见[部署限制](../docs/development.md#部署与维护)。

## 模块职责

- `src/app`：应用入口、路由、主题和样式。
- `src/pages`：项目、分集、素材、任务、资产和配置页面。
- `src/features/projects`：正文保存会话、导航保护、素材提取、分镜候选与图片采用。
- `src/features/assets`：三层素材库共用UI和请求状态。
- `src/features/generations`：通用生成、任务详情、轮询与幂等请求标识。
- `src/features/media-library`：媒体详情与图片选择器。
- `src/api`：DTO、请求封装和统一错误；见[前端API约定](src/api/README.md)。

`episode-workflow.ts`仍承担旧浏览器快照读取/校验与模型偏好兼容。保留的旧媒体、宫格、video类型不是当前页面能力；对应演示生成组件已经移除。不要绕过服务端重新启用本地生成结果。

## 保存与交互

小说与剧本停顿1秒自动保存，串行队列共享服务器版本，旧响应不覆盖新输入。切步骤/离开页面先等待保存，失败则保留草稿并要求明确处理；刷新关闭用浏览器离开提示。冲突可下载草稿，再载入服务端版本手动核对。

AI结果先预览后采用。素材上传不自动确认，分镜生成不直接覆盖镜头，任务页成功不等于项目已采用。详情页与任务列表刷新使用当前服务器状态；临时媒体URL失效时重新读取。

旧版浏览器正文只允许显式导入；旧素材/制作数据提供JSON下载，不自动上传或覆盖服务端数据。保留兼容读取不代表浏览器存储仍是业务数据源。

产品边界见[PRODUCT.md](PRODUCT.md)，界面约定见[DESIGN.md](DESIGN.md)，后端契约见[接口说明](../docs/api/README.md)。

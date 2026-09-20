# AI 短剧工作台 · 网页演示

基于 `C:/Users/snow/code/ai-video-integration/frontend` 页面与制作流程改造的 React + Vite + TypeScript 前端，采用适合网页的紧凑布局与石墨灰、琥珀色主题，项目和素材使用本地演示数据，AI 配置通过 Axios 连接后端并持久化。无需 Electron。产品范围见 [PRODUCT.md](PRODUCT.md)，视觉约定见 [DESIGN.md](DESIGN.md)。

## 运行

在本目录执行（Node.js 20.19+ 或 22.12+）：

```sh
npm install
npm run dev
```

开发地址：<http://localhost:5173>（绑定 `127.0.0.1`，端口占用时启动失败）。

```sh
npm run build
npm run preview
```

构建结果位于 `dist/`；预览地址以终端输出为准，默认端口为 `4173`。`npm run typecheck` 可单独检查 TypeScript。

## 页面与交互

- 项目管理：紧凑项目列表、新建与打开项目、继续分集创作。
- 项目详情：剧集信息、分集列表、项目资源库。
- 分集制作：按步骤切换小说与剧本、素材拆解、素材图片；分镜按镜头编号，以脚本、分镜图、分镜视频三栏展示。
- 全局素材库：角色、场景、道具的创建、搜索与删除。
- AI 配置：仅保留文本模型、生图模型、生视频模型三个标签，各分类独立添加、编辑、删除及设置默认配置。

剧本、素材拆解和素材图片生成均为前端演示，不会调用模型或产生费用。素材图片支持本地导入；分镜图与视频生成按钮沿用原界面的禁用状态。没有真实视频生成、剪辑或成片导出。

## 数据保存

首次访问会填充示例数据。项目、分集编辑和素材信息保存在 `localStorage`；导入的媒体文件保存在 `IndexedDB`，重新打开后可恢复预览。数据仅属于当前浏览器和当前站点地址，切换浏览器、端口或 `localhost` / `127.0.0.1` 会使用独立存储；清除站点数据会删除这些内容。

AI 配置保存在后端数据库，按文本、生图、生视频类型分页查询。API Key 随表单提交到后端加密保存，响应只返回是否已配置；前端不把密钥写入浏览器存储。编辑时留空保留原密钥，也可明确选择清除。保存配置不会调用模型服务。

新增或编辑时，填写服务地址和 API 密钥后点击「获取模型」，即可搜索选择服务返回的模型标识；未提供列表接口的服务可手动填写。探测只读取模型目录，不执行生成，也不会自动保存表单。编辑配置可复用已保存密钥，更换服务地址后需重新输入该服务的密钥。

## 路由与部署

页面使用 React Router（BrowserRouter），支持直接访问、刷新及浏览器前进后退：

- `/projects`：项目管理。
- `/projects/:projectId`：项目详情。
- `/projects/:projectId/episodes/:episodeId/:stage`：分集制作；步骤为 `source`、`script`、`assets`、`storyboard`。省略步骤时进入本集待完成步骤。
- `/assets/character`、`/assets/scene`、`/assets/prop`：素材分类。
- `/ai`：AI 配置。

部署到静态托管时，需要将未命中文件的页面路径回退到 `index.html`（SPA fallback），以支持深层链接刷新。Vite 开发和预览服务器已支持此行为。

## AI 配置接口联调

先按后端 README 启动 API，再运行本前端。开发服务器将 `/api` 代理到 `http://127.0.0.1:8000`。需要更改时复制 `.env.example` 为 `.env.local`，设置 `API_PROXY_TARGET` 并重启 Vite；浏览器请求前缀由 `VITE_API_BASE_URL` 控制，默认 `/api/v1`。环境文件不得包含模型密钥或数据库凭据。

生产部署应在同域名把 `/api/` 反向代理到后端，并在其后配置 SPA fallback。Vite 开发代理不会进入构建产物。详细请求分层、DTO、错误和版本冲突约定见 [API 约定](src/api/README.md)。

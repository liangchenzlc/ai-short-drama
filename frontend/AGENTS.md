# frontend 协作指南

> 本文件只写 `frontend/`（含 `frontend/canvas/`）特有约定。通用规则见根 `AGENTS.md` 与 `PRINCIPLES.md`；冲突时停下来说明，不自行取舍。

React 19 + TypeScript strict + Vite 7 + Ant Design，无路径别名、无 ESLint/Prettier 配置，风格靠 typecheck、build 与相邻文件对齐。两个包：宿主 `frontend/` 与画布 `frontend/canvas/`，统一由宿主安装、启动、构建。

## 文件存放

| 目录 | 放什么 | 不该放 |
| --- | --- | --- |
| `src/app` | 路由（`App.tsx`、`paths.ts`）、主题、全局与跨页样式 | 业务逻辑、请求 |
| `src/pages` | 页面级组合：路由入口、布局、阶段切换 | 复杂交互实现，应下沉 `features` |
| `src/features/<域>` | 业务组件、Hook、编辑会话、保存与生成状态 | 跨域复用组件，应进 `components` |
| `src/components/ui`、`layout` | 无业务语义的通用组件 | 引 `api/modules`、读业务上下文 |
| `src/api/http.ts` | 唯一 HTTP 客户端与错误映射 | 业务转换、页面文案 |
| `src/api/modules/<资源>.ts` | 按资源分模块的请求函数 | 组件内另建 axios 实例 |
| `src/api/types/<资源>.ts` | 跨模块共享的 DTO 类型 | 与 `modules/` 重复定义同一类型 |
| `src/lib` | 跨 feature 的纯函数，不依赖 React | 组件、Hook |
| `src/types` | 跨模块共享的前端类型 | 单 feature 专用类型，就近 export |
| `tests/`、`e2e/`、`runtime-tests/`、`performance/` | 与 `src` 同域目录、同语义命名 | 手工调试脚本，进 `.runtime/` |

命名与样式：

- 组件 `PascalCase.tsx`，Hook `useXxx.ts`，纯逻辑与请求模块 `kebab-case.ts`。
- 一个 feature 一个目录；不加 `-component` 后缀，不新建 `index.tsx` 桶文件。
- 样式沿用普通 `.css`：feature 级放该 feature 目录，跨页面放 `src/app`；不引入 CSS Modules 或 CSS-in-JS。
- 画布代码只进 `frontend/canvas/src`，不在宿主 `src` 里实现画布交互；跨包数据走宿主 `src/api`。
- 遗留命名：`pages/projects/EpisodePage.tsx`、`features/projects/useEpisodeWriting.ts` 是历史名，实际已是创作工作区；新增代码用 Creation／Project 语义，不要顺手改名。

## 代码编写规范

- 相对导入，不用别名。顺序：`react` → 第三方 → `src` 内模块 → 同目录文件。
- DTO 类型目前两种落法并存：多数资源直接内联在 `modules/<资源>.ts`，部分在 `api/types/`。跟随该资源现有做法，不要在两者间来回搬；同一类型只定义一次。
- DTO 保留服务端 `snake_case`，不在前端改名；ID 与版本是十进制字符串，禁止 `Number()` 转换。
- 请求只经 `src/api/http.ts` 与 `modules`。错误映射集中在 `http.ts`，页面用 `StudioFeedback` / `useStudioMessage` 就近反馈，不新造 `alert` 或常驻 Alert 挤压编辑区。
- 每个异步操作有明确 loading；每个数据区域有 loading / 空 / 错误三态。
- 自动保存保持串行、版本校验与导航保护：409 保留草稿等用户处理，旧响应不得覆盖新输入。
- 状态就近：`useState` / `useReducer` / feature 内 Hook。不新增全局 store。
- 类型不写 `any`；跨模块类型进 `src/api/types` 或 `src/types`。
- 组件只做渲染与事件绑定，格式化与派生计算放同目录 `*-model.ts` 或 `src/lib`。
- 不引入未使用的依赖；新增依赖说明理由，并同步两个包的锁文件。

## 验证

- `npm run typecheck` 快速检查；`npm run build` 含两包类型检查与生产构建。
- 功能测试 `npm test -- --feature <编号>`；浏览器测试 `npm run test:e2e -- --feature <编号>`，范围选择见根 [按功能执行测试](../docs/instruction/按功能执行测试.md)。
- 改画布另跑 `cd canvas && npm run source:check`，并对照[画布迁移边界](../docs/instruction/画布迁移边界.md)。

# frontend 协作指南

> 本文件只写 `frontend/`（含 `frontend/canvas/`）特有约定。通用规则见根 `AGENTS.md` 与 `PRINCIPLES.md`；冲突时停下来说明，不自行取舍。

React 19 + TypeScript strict + Vite 7 + Ant Design，宿主使用 `@host/` 指向 `frontend/src/`，TypeScript、Vite 与画布保持同一解析规则。无新增格式工具依赖，风格靠 typecheck、build 与相邻文件对齐。两个包：宿主 `frontend/` 与画布 `frontend/canvas/`，统一由宿主安装、启动、构建。

## 桌面支持范围

宿主与无限画布均只支持桌面浏览器，最低内容视口宽度 **1280 CSS px**。新增和修改页面不设计手机、平板、触屏专属布局或验收矩阵；桌面多栏、容器排布、面板调宽、键盘可访问性和内部滚动继续维护。不得重新引入按设备切换的抽屉、工具栏或焦点陷阱，具体尺寸与验收见 [PRODUCT.md](PRODUCT.md)、[DESIGN.md](DESIGN.md)。

## 文件存放

| 目录 | 放什么 | 不该放 |
| --- | --- | --- |
| `src/app/{router,providers,layouts,styles}` | 路由、应用提供器、桌面壳与全局样式 | 业务规则 |
| `src/pages/<业务>/<页面>/index.tsx` | 路由页面组合；页面样式放同目录 index.css | 复杂交互实现 |
| `src/features/<业务>/<功能>/` | 组件、Hook、编辑会话；复杂功能继续细分 | 无业务语义的公共组件 |
| `src/shared/{ui,async,navigation,media}` | 通用界面、异步工具、地址与媒体能力 | 引业务 API、读业务上下文 |
| `src/api/http.ts` | 唯一 HTTP 客户端与错误映射 | 业务转换、页面文案 |
| `src/api/<业务>/<功能>.ts` | 请求函数，复杂功能再建目录 | 重复定义 DTO、另建 axios 实例 |
| `src/types/<业务>/<功能>/` | DTO、Props、会话与纯逻辑的具名类型 | 运行时代码、反向引用 features |
| `src/integrations/canvas/` | 助手、会话、图片工具桥接及契约 | 画布内部交互 |
| `tests/`、`e2e/`、`runtime-tests/`、`performance/` | 与业务同域、同语义命名的验证 | 临时调试脚本 |

命名与样式：

- 组件 PascalCase.tsx，Hook useXxx.ts，纯逻辑与请求模块 kebab-case.ts。
- 页面 index.tsx 是实际入口，不是转发桶；不存在页面样式时不创建空 CSS。功能组件按职责命名，目录可有多级，不制造只有一行转发的空层级。
- 样式沿用普通 CSS：页面级在页面目录，feature 级在功能目录，跨页面在 src/app/styles；不引入 CSS Modules 或 CSS-in-JS。
- 画布内部代码进 frontend/canvas/src；跨包访问宿主通过 src/integrations/canvas 的公开入口。
- 项目直接拥有作品、素材与分镜；宿主用 Project／Creation／ProjectAsset，现行协议中的历史字段以契约为准，不盲改持久化字段。

## 代码编写规范

- 宿主内部用 `@host/` 导入，纯类型用 `import type`。类型依赖只指向其他类型，公共层不反向依赖业务层。
- 具名类型集中到 `src/types` 对应业务／功能目录；API 模块只实现请求，同一 DTO 只定义一次。
- DTO 保留服务端 `snake_case`，不在前端改名；ID 与版本是十进制字符串，禁止 `Number()` 转换。
- 请求只经 `src/api/http.ts` 与业务请求模块。错误映射集中在 `http.ts`，页面用 `StudioFeedback` / `useStudioMessage` 就近反馈，不新造 `alert` 或常驻 Alert 挤压编辑区。
- 每个异步操作有明确 loading；每个数据区域有 loading / 空 / 错误三态。
- 自动保存保持串行、版本校验与导航保护：409 保留草稿等用户处理，旧响应不得覆盖新输入。
- 状态就近：`useState` / `useReducer` / feature 内 Hook。不新增全局 store。
- 类型不写 `any`，具名类型统一进入 types 对应业务目录。
- 组件做渲染与事件绑定；状态生命周期放 Hook／编辑会话，格式化与派生计算放具体功能的纯逻辑文件。
- 版本围栏、409 保稿、原请求核对、迟到响应与离页恢复等关键处写中文注释，解释约束及原因，不逐行翻译代码。
- 不引入未使用的依赖；新增依赖说明理由，并同步两个包的锁文件。

## 验证

- `npm run typecheck` 快速检查；`npm run build` 含两包类型检查与生产构建。
- 在仓库根执行 `node scripts/check-quality.mjs`，结构与禁令规则见[代码质量规范](../docs/instruction/代码质量规范.md)。
- 功能测试 `npm test -- --feature <编号>`；浏览器测试 `npm run test:e2e -- --feature <编号>`，范围选择见根 [按功能执行测试](../docs/instruction/按功能执行测试.md)。
- 改画布另跑 `cd canvas && npm run source:check`，并对照[画布迁移边界](../docs/instruction/画布迁移边界.md)。

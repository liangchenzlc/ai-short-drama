# frontend 协作指南


React 19 + TypeScript strict + Vite 7 + Ant Design，宿主使用 `@host/` 指向 `frontend/src/`，TypeScript、Vite 与画布保持同一解析规则。无新增格式工具依赖，风格靠 typecheck、build 与相邻文件对齐。两个包：宿主 `frontend/` 与画布 `frontend/canvas/`，统一由宿主安装、启动、构建。

## 桌面支持范围

- 宿主与无限画布均只支持桌面浏览器，最低内容视口宽度 **1280 CSS px**。
- 新增和修改页面不设计手机、平板、触屏专属布局或验收矩阵。
- 桌面多栏、容器排布、面板调宽、键盘可访问性和内部滚动继续维护。


## 命名

**文件与目录一律小驼峰，无例外。**

```
showBox.tsx        fileTree.ts        apiHelper.ts
editorPanel/       contextMenu/       useProjectSave.ts
```

禁止短横线（`show-box.tsx`）、蛇形（`show_box.tsx`）、帕斯卡（`ShowBox.tsx`）与全大写（`SHOWBOX.tsx`）。现有代码里的例外不作为新开发的参照。页面入口沿用 `index.tsx`。

**组件导出名用 PascalCase。** 这是 JSX 语法强制，不是风格选择：首字母小写的标签会被当作原生元素，`<showBox />` 不会渲染组件。

```tsx
// showBox.tsx
export function ShowBox() { ... }

import { ShowBox } from './showBox'
<ShowBox />
```

文件名描述用途，导出名是组件，两者大小写不同是预期行为。查找按文件名，引用按导出名。

**变量、函数、ref、计算值、事件处理用**小驼峰：

```tsx
const editorConfig = ...
const requestTimeout = 3000
const panelWidth = 280
const handleSubmit = () => ...
const [fileTree, setFileTree] = useState(...)
```

不使用全大写常量，用语义化小驼峰：`maxRetry`、`apiBase`、`defaultPageSize`。

**类型与接口用** PascalCase（`ProjectDetail`、`ShowBoxProps`）。类型不是运行时值，不受小驼峰约束。

**Hook 命名**：`use` + 小驼峰（`useProjectSave`、`useStudioMessage`）。

**第三方 import 保留原始导出名**，不为转大小写加 `as` 别名：

```tsx
import { Button, Table } from 'antd'
```

## 组件与 JSX

**props 一律小驼峰**，包括声明、静态属性与动态绑定：

```tsx
<ShowBox showArrow nodeTypes={nodeTypes} snapToGrid />
```

禁止 `show-arrow`、`:node-types`、`snap-to-grid`。

**第三方组件直接用库的导出名**，写 `<Button />`、`<Table />`、`<Modal />`；Ant Design 是 React 库，不存在 `<a-button />`、`<el-button />` 这类写法。

**只有 DOM 标准与第三方接口强制的名称保留原样**：`aria-label`、`data-*`、`className`、`htmlFor`、`tabIndex`。React 的 DOM 属性本身是小驼峰，不要改回全小写。

**单个 `.tsx` 自上而下固定顺序**，缺省段可省略，相对顺序不变：

```tsx
// 1. import
import { useState } from 'react'

// 2. 类型
type ShowBoxProps = { ... }

// 3. 模块级常量
const defaultWidth = 280

// 4. 主组件
export function ShowBox({ ... }: ShowBoxProps) {
  // 4.1 hooks
  // 4.2 派生值
  // 4.3 事件处理
  // 4.4 渲染
}
```

一个文件一个主组件。只被它使用的子组件放同文件、主组件之后；被两处以上使用才拆文件。

**控制嵌套**：条件分支超过两层用提前返回或提取子组件，不写三元套三元；列表优先 `map` 前先过滤，不在 JSX 里堆多段 `&&`。

## 函数与抽象

目标是单屏读完。经验上限：50 行、3 层嵌套、5 个参数。

只在满足任一条时抽取：被两处以上调用；包住了一段确实复杂的判断；抽出来后能起一个准确的名字。

一次性逻辑不抽函数，也不新建工具文件。

## 样式

**类名统一小驼峰**：`leftMenu`、`panelHeader`、`fileTreeItem`。禁止 `left-menu`、`panel-header`。

**按 DOM 结构组织**，用后代选择器加缩进表达层级，不堆扁平的单类名：

```css
.fileTree { }
.fileTree .fileTreeItem { }
.fileTree .fileTreeItem .fileTreeIcon { }
```

**可以使用原生 CSS 嵌套**。CSS Nesting 已是标准，Chrome 112+、Safari 16.5+、Firefox 117+ 支持，Vite 7 默认构建目标覆盖这个范围，不需要 sass 或任何插件：

```css
.fileTree {
  display: flex;

  .fileTreeItem {
    padding: 4px 8px;
  }
}
```

原生嵌套的 `&` 只能出现在开头，不能拼接 `&__item`、`&--active`，所以不用 BEM，直接写完整子选择器名。只在新增或整体重写组件样式时使用，存量文件保持扁平写法。

**不引入** sass、less、CSS Modules、CSS-in-JS、Tailwind。样式走普通 CSS：页面级放页面目录，功能级放功能目录，跨页面放 `src/app/styles`。

**状态类名用 `is` 前缀**避免与属性混淆：`isSelected`、`isDisabled`、`isLoading`，不用 `selected`、`active` 这类裸词。

## 复用

新增逻辑前按顺序找：现有工具函数 → 现有 Hook → 现有 Store 方法 → 相邻模块的同类实现。四步都找不到才新建，新建时先判断能否加进已有文件，而不是新增一个 `utils` 条目。

## 存量

- 新文件、新组件、新类名一律按本节。
- 已存在的 PascalCase 组件文件、kebab-case 类名不主动重命名。
- 改动某个模块时，新增部分按本节；顺手能统一且不扩大 diff 的，一并改掉。
- 不做全库批量重命名：收益是形式统一，代价是几百个文件的 diff 和一次高风险回归。

## 边界

`frontend/canvas/src` 内的一比一迁移代码沿用上游命名与结构，不套用本节的命名规则；宿主与画布之间的桥接代码在 `src/integrations/canvas/`，按本节执行。

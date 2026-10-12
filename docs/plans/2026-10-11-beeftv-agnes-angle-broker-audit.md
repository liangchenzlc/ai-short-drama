# 原多角度的 Agnes 真实供应商验收

接续[打光真实审计](2026-10-10-beeftv-agnes-lighting-broker-audit.md)，使用用户已授权的
[Agnes 免费图片模型](https://wiki.agnes-ai.cn/zh-Hans/docs/overview)
`agnes-image-2.5-flash`。桌面 b1440 和窄屏 c390 分别 **1 passed / 1 deselected**，
全部技术后置实际执行，两个原产物及恢复截图已查看。成功仅限本次蓝盒示例的
可见视角变化与主体保持，不保证所有角度，也不证明数值相机参数精确执行。
390 面板仍存在文字和控件裁切；固定源与目标成对运行复现相同压缩，不能将生成
成功算作完整视觉验收或 R09/R23/M0–M5 完成。

## 实施与真实链路

新增[独立 Python 验收](../../backend/tests/integration/test_canvas_agnes_angle_broker_live.py)
和[原 UI 浏览器驱动](../../frontend/canvas/scripts/verify-agnes-angle-python.mjs)。
复用冻结的正式八角色栈和真实观测网关，没有修改生产代码、HTTP 契约、DDL、
依赖、标准模式、固定源树或源适配账本。原多角度替身测试继续保留其原范围。

先核对[原组件](../../frontend/canvas/src/components/canvas/canvas-node-angle-dialog.tsx)、
[生成 Hook](../../frontend/canvas/src/pages/canvas/use-canvas-media-tools.ts)、
[标题和提示词](../../frontend/canvas/src/lib/canvas/canvas-project-domain.ts)及
[任务 consumer](../../frontend/canvas/src/lib/canvas/canvas-project-generation.ts)。
多角度从原“生成新角度”按钮直接提交；强制单份，来源绑定为原图片节点。
打光先建 idle 节点的流程没有套用到这里。

参考为已验收的匿名蓝盒原 PNG，1024×1024，SHA-256：
`a47ed24be0e913236bf292aba8b23d03cbe2feb7b33550eacf20fd04808f79b6`。
每场从原文件 input 上传，个人份数保存为3，原工具仍只提交一份。
客户端 `model` 严格核对保存模型的 `host-{id}::agnes-image-2.5-flash`，
`logicalModelId`、供应商原名及冻结模型快照另行核对。
供应商实际请求为 `POST /v1/images/generations`、1K、1:1，
`extra_body.image` 传原图 Data URI，真实返回 `b64_json`；不是旧替身的 multipart
`/images/edits`。凭据沿已有加密信封在服务端内存解密，不传给浏览器。

## 实际批次与验证

| 批次 | 实际结果 | 供应商调用及范围 |
| --- | --- | --- |
| a1440 | **1 skipped / 1 deselected，2.77s，exit0** | 直接 pytest 未配置 `TEST_DATABASE_URL`，数据库 fixture 跳过；零供应商调用，不算通过 |
| b1440 | **1 passed / 1 deselected，99.44s，exit0** | 独占临时 MySQL；一次真实200，全部 Python 后置及实际内容查看通过 |
| c390 | **1 passed / 1 deselected，93.71s，exit0** | 桌面同版代码；一次真实200，全部 Python 后置及实际内容查看通过；面板裁切单列 |

a 的跳过源为 `tests/integration/conftest.py::isolated_mysql_database`：
`TEST_DATABASE_URL is absent; real MySQL integration tests skipped`。
改用既有独占 MySQL 包装后才开始真实 b/c；没有改 skip 门禁。a 的 pytest 缓存
权限 warning 保留，b/c 显式关闭缓存插件。两成功场的 Starlette 弃用 warning 保留。

两宽度都实际操作默认45°/0°/4.8、camera/标准、六预设、三 Slider 的 Home/End、
四方向按钮、skybox/camera 鼠标拖拽、重置、取消及重开默认值。提交前关闭旧窗口，
仅 cookies/空 origins 恢复原参考；选择 -90°/60°/10/广角并按原按钮直接提交。
running 再次恢复同一任务后才释放真实调用，终态直接绑定 Worker 的永久产物，
再关闭窗口恢复作品；四阶段均解码1024图片并核对原参考摘要。
390 的适应画布沿原 Ctrl+0，按键并未修复面板裁切。

两场共四个正常 AMQP 动作、四个重复/旧版本终态探针，八个回执均完成 SUCCESS，
探针没有造成额外供应商 POST。正常 lifespan、独立 Scheduler 和六类 Worker
实际启动，节点980–987，fixture1022；不是线程任务执行器替身。
每场1任务/1记录/1绑定/1已附着产物、原节点/连线、MySQL/MinIO原返回字节、
1024尺寸、作者私人模型/任务、成员共享媒体及外人404均完成后置验证。
页面与 HTTP 错误均为零。

## 实际内容与界面限制

两个原1024图片、参考图、源结果合成图、两宽度恢复原图和合成图已实际查看。
产物均仍为白色桌面上的一个蓝色带盖方盒，盒盖顶面变大、视点抬高，
桌面透视和阴影发生变化，颜色/材质/画面风格保持；没有可见新增人物、物体、文字
或标识。它们通过本次可见视角变化和主体保持的内容检查。
参考盒体对称，无法凭这些图片精确识别水平旋转90°；俯视60°、距离10和广角
也没有测量证明。六预设的控件检查不等于六种真实生成质量验收。

| 屏宽 | 原供应商产物 SHA-256 |
| --- | --- |
| 1440 | `82ac0260edb3fec191c7596c514956efff58a56b2fe81ea69928853c6975b55a` |
| 390 | `d4183d95490f7f373e9f437ea7dc72ddbb63caf004ae0278ded8d3b4d5f1eef7` |

390 的 `angle-selected.png` 明确出现标题、选项和 Slider 标签裁切。随后另做
`agnes-angle-narrow-pair-a`：同机 Chrome154、390×900、正常动效，用既有 API
夹具分别打开固定源 `4ca2a65a7780a8dfcaaa86c33679f84fb04e055c` 和目标真实
React 编辑器，沿原菜单/控件操作到同一参数，没有点击生成或调用供应商。
两原截图已查看：面板均366×528、网格列均 `280px 54px`，控件内宽52、
滚动内容宽148；面板 `scrollLeft=85`，文字挤压和裁切在两端复现。
角度组件和 CSS 分别与固定源字节相同。夹具报告零页面错误/未知请求，
这只是该可读性问题的源成对诊断；背景节点位置等全屏差异仍保留，未做零像素
完整验收。按一比一边界记录源固有问题，没有应用 R23 绘图候选或调整源布局。
完整两原截图另有130472个像素不同，原 diff 保存；不能把面板几何一致说成全屏
像素通过。源 AntD 的 `destroyOnClose` 弃用提示和目标大块 Babel 优化提示保留，
临时图片汇总脚本的 Pillow `getdata` 弃用 warning 也单列，不改依赖或系统策略。

## 命令、范围与清理

在 `backend/` 设置四个现有 opt-in、`SNOWFLAKE_WORKER_ID=1022`、已知 Chrome 路径，
清除助手生命周期观测模式；`AGNES_ANGLE_REFERENCE_IMAGE` 指向上述可信蓝盒。
每批使用独立 log/basetemp；实际沿已有独占 MySQL 包装调用以下集成入口：

```powershell
uv run python scripts/run_integration.py `
  tests/integration/test_canvas_agnes_angle_broker_live.py -q -k 1440 --tb=short `
  -p no:cacheprovider --basetemp=.runtime/agnes-angle-broker-new1440-temp
```

390 将 `-k` 改为390，并使用另一个独立目录。真实 b/c 记录、两份 acceptance、
原图片、最终 matrix/content-review、环境及清理报告位于忽略的 `backend/.runtime/`，
不提交缓存、截图、日志或凭据。
全仓 Ruff check、新 Python format 和新 JS 语法检查通过。全仓 format 实跑结果为
763份已格式化、`scripts/apply_assembly_migration.py` 第16和23行混合换行不符合；
该文件与本片2844文件初始基线摘要相同，没有顺手修改。
未重复旧快测、Node整组、统一 build 或完整像素门禁。
收尾去敏核验首轮将 PowerShell 的 UTF-16 日志按 UTF-8 读取，
`Path.read_text` 报 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0`。
改为识别 BOM 后，三个日志和八份变更源码/文档实际重查通过，没有输出凭据。

两正式栈的22队列/4交换机被动确认不存在，20角色/执行PID无存活，4199及两 API
端口无 listener，任务容器/测试桶0，原3桶与用户两服务启动时间保持。
诊断自建4253/4254与浏览器关闭，两个源树继续干净。
本片仅两份源码＋六份文档变化，27前片源码保持，新两源码单独冻结后合计29。
共用助手仍纯对话、零工具；正式视频390成功、其余独立工具、完整源对照与
[剩余 M0–M5 门禁](2026-10-08-beeftv-remaining-work.md)继续，goal active。

# 原手工表情 Agnes 正式队列验收

记录日期：2026-10-11。验收截至 A–F1440、G/H390，不替代完整 M0–M5 验收。

本片原 UI、正式 API/Scheduler/独立生成 Worker/真实 AMQP 与 Agnes 图片已经实际运行。
E、F、H 的测试和全部 Python 后置通过；但人工查看真实原图、供应商原图、派生图及 fresh
恢复截图后，确认三批都改变了目标人物的头部朝向和目光方向。G 的部分实际画面也保留
同一内容失败，但其 Python 后置和终态 fresh 未执行。**作品内容验收失败**，
不能将供应商 200、任务 succeeded 或局部像素保护写成满足全部验收标准。
最终2341192a驱动当前只完整执行并通过H390技术后置；F1440使用前版980b驱动，E使用更早驱动且未独立冻结执行摘要，
不能合并称最终同版两宽度通过。I无进程/0调用，不是供应商拒绝。

## 输入与本片范围

- 已授权模型 `agnes-image-2.5-flash`，本人保存的 `openai-image` 配置，实际适配器为
  `openai_images.v1`。冻结配置/凭据保持服务端加密，日志和归档 JSON 的密钥检查已执行。
- 输入为此前真实 Agnes 归档的匿名合成人像 PNG，1024×1024、961053 字节，SHA256 为
  `29b3fde68f828e41b0011e75176f3b3a063157401315bfba6b35c750efe12fdb`。
  原打光产生其他人物的内容失败保留其历史结论，此处只复用明确归档的参考字节。
- 原文件上传入口创建源节点；实际检测提示为“识别到 1 张人脸，请选择人物”，随后主动
  使用原手工框选。记录该提示不等于证明自动检测框正确。本片没有真人生物识别判断。
- 手工框 `x=496,y=191,width=194,height=226`；目标“角色1”，欣喜若狂，
  `presetId=emotion-2-2,intimacy=2,arousal=2`；`maskSupported=false`，单份。
- 原算法提交两份不同永久参考：第一份是编辑区域 PNG，区域
  `x=331,y=21,width=524,height=622`，解码后的全部 RGBA 与源图精确裁切相同；
  第二份是 384×384 身份参考 JPEG。两份参考 SHA256 分别为
  `09377b35a3cd63f5f3c70a7639b8b1883dcf1297c20379bbd57997cf7dd066c8`、
  `bf2f837dced09ad4e7bff6d5420fd55574d26152eba126552f108f3c1784a3da`。
- 应用受理输入为 `quality=high,count=1`，无 mask；实际供应商请求为
  `POST /v1/images/generations`，模型正确，`size=1024x1024`，两个图像参考顺序及摘要
  正确。请求字段为 `extra_body/model/prompt/return_base64/size`；不将应用冻结的 high
  设置冒称供应商请求另有未归档 quality 字段。

原 UI 提示词、受理 task、源节点和数据库冻结 request 的精确字符串已互相核对。
其中明确要求“保持眼睛大小与方向”、严格保持“头部朝向”与人物姿势、身份、镜头、
光线及背景；第一张图是唯一编辑目标，第二张仅核对身份，不能复制它的构图和光线。
本片内容失败必须按这些已有要求判断，不能删掉朝向约束、放宽内容要求或改成仅验露齿笑。
当前观测器保存实际请求字段、参考摘要和供应商状态，但未保存完整原始请求 prompt；
上述精确字符串一致性是应用输入/冻结合同证据，不能冒称独立原始 wire prompt 归档。

本片只覆盖这一输入、手工单人、这一预设、单份及无供应商 mask 的局部合成。其他人脸、
25 种真实作品效果、其他参数/批次、mask 渠道、角色参考、全异常及完整源视觉未覆盖。

## A–H 实际批次

所有批次均使用原测试文件
`backend/tests/integration/test_canvas_agnes_emotion_broker_live.py`，A–F为1440×900、
G/H为390×900 Chromium，
独立 basetemp、随机临时 MySQL 库/MinIO 桶与 AMQP namespace；原日志保留。
测试显式要求 `RUN_AGNES_LIVE/RUN_CANVAS_RESOURCE_MINIO/RUN_CANVAS_BROWSER_INTEGRATION/
RUN_CANVAS_BROKER_INTEGRATION=1`、`SNOWFLAKE_WORKER_ID=1022`、既有匿名人像路径和
本机 Chromium。密钥由现有受保护运行环境提供，不写入复现命令或文档。

| 批次 | 原 pytest 摘要 | 真实供应商 | 实际结论 |
| --- | --- | --- | --- |
| a1440 | pytest.exit；0 passed、1 deselected、1 warning，119.22s | 0 提交/0受理、无 provider report | 手动框选按钮等待超时；未执行成功后的 Python 后置或内容验收。 |
| b1440 | pytest.exit；0 passed、1 deselected、1 warning，81.86s | 0 提交/0受理、无 provider report | 快速操作后的 3D 预览稳定等待超时；这份真实红必须保留。 |
| c1440 | pytest.exit；0 passed、1 deselected、1 warning，84.33s | 0 提交/0受理、无 provider report | 静态轴文字/重复标签的驱动定位假设导致 stableCaptions 超时，未调用模型。 |
| d1440 | pytest.exit；0 passed、1 deselected、1 warning，121.63s | 一次 POST 200，task succeeded，2 完成回执 | fresh 后节点 420 尺寸的坐标被错误地同提交前 520 坐标比较；成功后的完整 Python 后置未执行，内容未验。 |
| e1440 | 1 passed、1 deselected、1 warning，128.35s | 一次 POST 200，2 正常动作+2终态探针，4完成回执 | 全部 Python 后置 passed；旧驱动仅证明中间 420 状态中心保持，没有证明终态解码后几何；人工内容失败。 |
| f1440 | 1 passed、1 deselected、1 warning，137.57s，exit0 | 一次 POST 200，2 正常动作+2终态探针，4完成回执 | 全部 Python 后置及分阶段 DOM/存储/恢复几何 passed；原头部朝向和目光未保持，人工内容失败。 |
| g390 | pytest.exit，exit1；0 passed、1 deselected、1 warning，183.77s | 一次 POST 200，task succeeded，2完成回执；无终态探针 | 终态fresh前视口strict比较因MySQL读回1ULP差异超时；第三次fresh尚未创建，全部Python后置未执行。已归档部分真实画面内容failed，不算完整技术或内容验收。 |
| h390 | 1 passed、1 deselected、1 warning，134.66s，exit0 | 一次 POST 200，2 正常动作+2终态探针，4完成回执 | Python后置及完整分阶段几何通过；关闭前记录1ULP等价、恢复精确等于持久值。root实看原图/派生/face及390fresh后，头部朝向和抬眼仍导致内容failed。 |

`deselected` 是本批仅选择一个宽度、另一宽度未执行，不是 skipped 或另一宽度通过。
A–C 的零调用结论综合 submissions/admissions 为空、没有 provider checkpoint，以及当时
observer 构造即写 checkpoint 的源码合同；这不是供应商拒绝的证据。D 的模型成功也不能
补齐未执行的后置。E/F/G/H 是同一输入的新明确任务，各一次 POST，不覆盖任何旧红。
I尚无批次、进程或供应商提交：root报告创建进程前的两次自动审批拒绝了特定人像外传，
当前等待已发出的具体授权问题答复。本记录不为I建立结果条目，不能把未执行算作拒绝模型
请求或任何测试结果。

关键异常及复现阶段：

```text
A：打开原表情编辑器后等待手工框选
locator.waitFor: Timeout 60000ms exceeded.
  waiting for getByRole('button', { name: '手动框选', exact: true }) to be visible
  at openEditor (.../verify-agnes-emotion-python.mjs:199:18)
  at .../verify-agnes-emotion-python.mjs:265:5

B：原25预设、键盘和鼠标拖拽场景，等待实际预览稳定
page.waitForFunction: Timeout 20000ms exceeded.
  at stablePreview (.../verify-agnes-emotion-python.mjs:223:16)
  at .../verify-agnes-emotion-python.mjs:294:11

C：选择标签后等待说明文字稳定
page.waitForFunction: Timeout 20000ms exceeded.
  at stableCaptions (.../verify-agnes-emotion-python.mjs:224:16)
  at selected (.../verify-agnes-emotion-python.mjs:220:11)
  at .../verify-agnes-emotion-python.mjs:280:9

D：真实模型终态与原局部羽化合成，沿用提交前坐标误判
AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal:
  actual   { x: 406, y: -210 }
  expected { x: 356, y: -260 }
  at .../verify-agnes-emotion-python.mjs:404:12

G：终态图已解码，第三次fresh关闭旧窗口前严格比较视口
AssertionError [ERR_ASSERTION]: 表情编辑未达到预期：...
  持久化 k=0.18043844856661043
  live     k=0.18043844856661045
  at eventually (.../verify-agnes-emotion-python.mjs:58:12)
  at async fresh (.../verify-agnes-emotion-python.mjs:167:26)
  at .../verify-agnes-emotion-python.mjs:479:13
```

后续驱动按实际标签/阶段建立断言，不修改生产预设、原图片参数、局部合成算法、合法版本
规则或内容标准。B 的快速反馈红不由本片普通稳定操作通过关闭；固定源/目标快速成对
验证须另行记录实际结果。

## 原 UI 与分阶段恢复/几何

E/F 实际验证原取消重开、3×3 过小手工框不产生情绪 slider、正常手工框、25 标签选择、
方向键及边界钳制、鼠标沿 pad 拖拽，取消后源图/节点保持，恢复后预设重新回到中性克制。
facecap 模型加载、标签过渡稳定和人头截图非空像素已检查。这些只证明控件和本片实际
预览切片，不证明全部 blendshape、每个预设的真实作品或固定源完整像素一致。

E/F每次 fresh 都先等待关闭前 live viewport 与服务端 ACK 精确一致，再关闭旧 context，
只复制 cookies、空 origins；新窗口初始 live viewport 的核对在再次 fit 之前完成。
E/F 各有提交前、running、终态三个独立 fresh。Control+0 是 fitCanvasContent；在退出
专注按钮上 press 此组合键不代表点击该按钮或退出专注模式。

E 旧驱动 `result_geometry` 仅记录提交前 520 状态与 running 恢复后的 420 状态，二者
中心 `(616,0)`；不能把该字段名中的 final 写成最终图片解码后的完整几何证明。
F 增补以下阶段的实际 DOM、持久化数据、保存 ACK及图片解码尺寸：

| F 阶段 | x,y | 宽×高 | 中心 | 实际证据 |
| --- | --- | --- | --- | --- |
| pending | 356,-260 | 520×520 | 616,0 | DOM与存储一致，源节点右侧96px。 |
| running_fresh | 406,-210 | 420×420 | 616,0 | 仅cookies恢复同任务，DOM/存储和此窗口保存ACK一致。 |
| attached | 406,-210 | 420×420 | 616,0 | 合成结果刚提交后的中间存储状态。 |
| decoded | 406,-210 | 520×520 | 666,50 | 实际1024×1024图已解码，DOM/存储和保存ACK一致。 |
| terminal_fresh | 406,-210 | 520×520 | 666,50 | 终态仅cookies新context再次解码，完整几何与decoded一致。 |

这里明确记录实际终态中心发生 `(50,50)` 变化，没有将中间中心保持冒称为终态中心保持。
F 的三个 fresh 视口依次为 `(720,450,1)`、`(720,450,1)`、`(387,425,1)`，关闭前 ACK 和
新窗口初始 live 均一致。源图四阶段 SHA/1024尺寸均一致；页面、HTTP及只读观测错误均为0。
面板/head/pad 几何采集不等于固定源视觉通过，不追加无固定源依据的非溢出要求。

G在decoded之后、终态fresh之前遇到上述1ULP差异；仅有两个fresh的ACK，failure截图
属于第三个原窗口。不能把它说成终态fresh，或借E/F的像素/权限后置证明G。该失败日志、
provider原图、browser派生图及人工部分内容红保留。

H仅在driver的fresh函数内处理关闭前数值序列化等价：finite Float64的编码距离最多
1ULP，2ULP及非finite仍拒绝；不放宽节点几何。关闭前同时保存live/stored/difference/
ulp_distance，随后新context恢复必须严格等于数据库返回的stored值，比较在再次fit前。
H的pending/running_fresh/attached/decoded/terminal_fresh节点几何与上表F完全一致。
前三次关闭前k依次为`0.4115384615384615`、`0.4115384615384615`、
`0.18043844856661045`；对应持久值为前两次相同、末次`0.18043844856661043`，末次k差
`-2.7755575615628914e-17`/1ULP，x/y无差。三个新窗口实际live逐字段严格等于其持久值，
恢复差值/ULP均0。`restored_viewport_acks.live`是关闭前旧live，真正恢复的值是`actual`，
不得因两个字段同名误读结果。H的末次持久viewport为
`x=134.9139966273187,y=445.48903878583474,k=0.18043844856661043`。

## 正式队列、永久媒体及三方权限

E/F/H 各实际启动 API、Scheduler、text/image/video/audio/render/agent 八角色，正常 API
lifespan/readiness通过；真实 image Worker 的 Snowflake ID为983，执行PID与正式回执
一致。每场唯一任务/生成记录/绑定和一次供应商 POST；正常消息版本1、2及终态后重放的
1、0均完成，exchange/routing_key正确，无 redelivery，终态探针没有再次调用供应商。
八角色存活检查及各自 cleanup 已在 pytest 中执行；启动其他角色不代表其模型能力已验。

每场浏览器上传4次：源图、裁切PNG、身份JPEG、局部合成PNG；供应商原图由后端归档。
源图、两个参考、供应商原图、派生图有5个不同永久媒体身份，数据库摘要/尺寸、MinIO
原字节、资源API与fresh所读摘要互相一致。供应商结果保持私人 detached，
attachment_receipt_id/attached_at为空；原 `canvas.document.commit` 保存派生图，
`canvas.task.bind` 操作数为0。派生素材 confirmed，并保留同任务/effectKey/来源关系。

源图与派生图明确发布后供项目成员读取，两个参考和供应商原图仅本人读取；成员/外人
读取私人媒体、任务、模型配置均404，外人读取项目画布404。共享派生节点不含 taskId
和 model。源节点完整不变，终态仍是源/派生两节点、一条源→派生连线；永久身份没有用
签名URL或BlobURL替代。共用助手没有执行工具，本片不证明助手图操作。

## 局部像素及人工内容结论

Python 对保守矩形外的全部 RGBA 逐字节核对，矩形为
`[floor(x-.25w),floor(y-.3h),ceil(x+1.25w),ceil(y+1.25h)]` 裁至源图范围。
E/F/H 实际矩形为 `[447,123,739,474]`。派生图1024×1024、PNG且不同于源图及供应商原图；
矩形外全部原像素一致，脸框内实际有变化。

| 批次 | 实际变化RGB包围盒 | 派生PNG SHA256 |
| --- | --- | --- |
| E | `[458,132,729,460]` | `a5bcc43727ad1d71fa192316158e40857b74933a73c451014817744f49b0ca60` |
| F | `[456,133,728,459]` | `47d867ec45099aa3267d6c687f9d74412fa4bd67783a19b5659fec5fd0c29c90` |
| H | `[456,131,729,460]` | `9d19615ff3b7195b712699a3402afaf54b4a65e2b1b4911635b531b6c0d676c7` |

上述像素断言不等于严格椭圆外逐像素证明，也不证明表情内容正确或人物身份客观相同。
root 已实际查看 E 的原脸裁切/供应商原图/完整派生/fresh，以及 F 的供应商1024原图、
完整1024派生、原脸/派生脸裁切及1440fresh。H的原PNG、supplier原PNG、derived原PNG、
face/全图比较及390fresh也已由root实际查看。E/F人工记录为
`.runtime/agnes-emotion-draft/content-e1440.json`、`content-f1440.json`；G部分人工记录为
`content-g390.json`、`content-h390.json`。H人工JSON也已实际读取：

- E/F呈明亮露齿笑，未见明显重复五官或口腔断裂；人物视觉仍可辨识，不作为生物识别
  或质量评分结论；完整图未见明显矩形硬边。
- 原脸明显侧向且目光偏下，供应商/派生脸转向更正面并抬眼；头部朝向和眼睛方向保持
  的冻结要求未满足，E/F/H内容判定均failed，G的部分实际画面也failed。局部融合保护
  周围图像并没有修复这一失败。G的Python局部像素后置未执行，不能借其他批次证明。
- F 是同一输入的新任务，不覆盖 E 内容失败。后续应继续保持此内容标准，核对源合同
  后再调整；此稿不宣称当前作品满足最终验收，也不删除技术通过的有效证据。

## 快速反馈的源/目标/隔离候选对照

原供应商前的B1440红在独立组件对照B中实际复现：aria/头模已中性，footer停留绝望。
A部分入口/导航失败、C单次未复现都保留；C外部stderr包装退出不能当Node实际exit0。
14个相关组件和Motion实现两端字节相同，整个依赖树有版本差异，不能直接归因于依赖升级。

另在忽略目录执行一次有界36场：源/目标/UUID候选 ×1440/390 ×普通/减少动画
×原快速/逐步等待/折返重复；另有target启动前置。直接导入原panel/pad/head/GLB和CSS，
保留StrictMode；阻断全部API及非本机请求，零供应商调用。它不覆盖正式Provider、Portal、
人脸检测、保存、任务、画布原窗口或最终完整视觉。候选只改两处说明的child key，保留
原wait、时长、blur/y、样式、pad、3D与事件。

| 实际场 | 20秒后aria | 卡住的可见文字 |
| --- | --- | --- |
| source-1440-reduce-rapid-b | 中性克制 | footer浅然莞尔 |
| target-1440-reduce-rapid-b | 中性克制 | footer浅然莞尔 |
| source-390-no-preference-rapid-b | 中性克制 | head浅然莞尔 |
| target-390-no-preference-rapid-b | 中性克制 | head隐忍 |

源/目标各10/12终态文字准确，候选12/12准确；重复同preset与无关父重渲染不额外换key，
逐步场的中间动画状态也有实际记录。四个原红和两个对应候选终图已实际查看。逐帧和
普通animation start/complete记录支持关注同key折返路径，但没有捕获内部presence回执，
不能宣称具体race根因已证实；减少动画的红也不能替代此前默认动画B红的修复证明。

**UUID候选不应用**：当前AnimatePresence的Map/Set只在同key再次present时清理该key；
持续使用不复用UUID会引入内部状态增长风险。useMemo缓存也不提供任意挂起/HMR下的
语义身份保证。12场准确不是可用生产修复，原快速反馈红仍开放。

证据在`.runtime/agnes-emotion-rapid-candidate/batch-a/report.json`与逐场JSON/PNG；驱动SHA
`12222b304fe4abd0ce8c330cdb7e7803ae418b2cd273b797edf6e78ba255e040`，候选SHA
`bff72c359411a1adfcca1b2c8fd162edc1b27a079d2ef61818057c8c3b76b2df`。
报告自记node_exit_code=0；外部进程Refresh后ExitCode为null，shell exit0不能替代Node
操作系统退出码。本记录只陈述已归档逐场结果，不把外部退出码缺口写成整批exit0。

独立后置`batch-a/independent-cleanup.json`已确认本批11个已记录PID不存在，4277/4278
无listener，固定源HEAD及干净状态保持，正式panel摘要未变。正常200ms对照图也已查看，
实际blur/wait中间状态有记录；它不证明像素一比一，也没有进行heap增长测量。

## 证据、清理及本片文件边界

原日志：`backend/.runtime/agnes-emotion-broker-live-{a,b,c,d,e,f}1440.log`、
`agnes-emotion-broker-live-{g,h}390.log`；原 basetemp为同名`-temp/`。
E/F/H成功case为 `test_original_emotion_real_agn0/`，保留 acceptance/provider/Worker/stack/
browser报告及原图/派生图/face裁切/fresh截图。

root 已实际运行 A–E collector，输出新目录
`backend/.runtime/agnes-emotion-evidence-a-through-e/`，保留 B 红、各部分证据及 E 三图。
原矩阵漏识别 PowerShell UTF-16 的 CR行尾摘要，E错误显示no_pass_result/counts空；
原日志明确E为1 passed/1 deselected/128.35s。该旧矩阵保持原样，不作为测试总判定。
忽略 helper 的 test_log 已改为解码后 splitlines 独立扫描；A–F实际只读回归红转绿，
A–D interrupted且deselected/warning各1，E/Fpassed且passed/deselected/warning各1。
修复仅用于后续新output；没有改原log/原matrix。两个helper AST、Ruff check/format通过。
随后root已实际新采集A–H至
`backend/.runtime/agnes-emotion-evidence-a-through-h-b/matrix.json`，逐批读取确认：
A/B/C为0POST，D/E/F/G/H各1POST；E/F/H后置passed与人工content failed同时保留，
G后置not_completed而部分content failed；E/F/H各4完成/2终态探针，D/G各2完成且无探针。
新矩阵已正确识别原UTF-16日志的摘要/计数，旧错误目录保持。

E环境已实际复核：`backend/.runtime/agnes-emotion-broker-post-e-environment.json`、
`backend/.runtime/agnes-emotion-broker-e-cleanup.json`；11队列/2交换机不存在，API57297和
浏览器4199释放，10角色/执行PID不存活且不复用，task容器/测试桶0，原用户进程和两个
源HEAD保持。F/H pytest自身stack.cleanup全true；不将E的独立环境报告复用于F/H或后续批次。
H独立检查已实际完成并读取
`backend/.runtime/agnes-emotion-broker-post-h-environment.json`：检查10个角色/执行PID，
旧PID存活0/reused_pids空、test listener0、测试桶0（MinIO总桶3）、task容器0；原用户
PID28808/28840及启动时间保持，两源仓库干净且HEAD分别为
`ef65732bff5ee99019664ca8689608b1c7831cbb`、
`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。
F/G/H另外的只读被动AMQP检查
`backend/.runtime/agnes-emotion-broker-fgh-cleanup.json`为passed，三批各11queue/2exchange
均确认不存在，共33queue/6exchange；没有创建/删除消息队列或复用E报告。

本次新增专项测试/driver，不修改生产界面、ORM/表结构、HTTP字段、依赖或加密主密钥，
没有DDL。A–F稿核对时Python SHA256为
`b307af5c310ad5675482c1023b4585aafce6190e66cdff4bb8e01969fb2557b6`，
JavaScript SHA256为
`980b88201f0a394bc4d2485888d32ccd3ee836abbcd34cdc6a3957c72ca23cd3`。
H的driver摘要已实际计算为
`2341192a39c2471db805ccc13e9480dcfc872a6368666d1830169c6e27c995c5`，
Python摘要保持上述值；F/G归档的980b版本不得标成H的最终版本。基线八文件允许范围和
33个当前curated旧冻结文件及其来源清单保持。
本片专项测试与四份忽略helper的Ruff/format、最终driver JS语法已实际通过；cleanup helper的行长
红仅作格式修正，没有因此重复模型/内容验收。本记录没有新执行全量unit/API、Node、
统一build、source或完整E2E；旧结果保持各自版本和范围。全仓format仍有既有迁移脚本
混合换行缺口，本片未修改该脚本。

本片独立文件/链接检查通过：2857文件基线仅八个允许文件变化，33个当前旧冻结源码
保持，六文档308个本地链接/锚点无缺失。八文件凭据检查通过且未输出凭据值。
全仓`git diff --check`仍有既有两份迁移SQL的225条尾部空格失败；本片八文件另行
检查通过，无尾部空格。既有失败文件与本片起始基线字节相同，没有顺手修改迁移SQL。
原检查日志和独立报告在`agnes-emotion-diff-check-all.log`、
`agnes-emotion-diff-check-boundary.json`，均位于忽略的`backend/.runtime/`。

R09/R23整体及完整M0–M5未关闭；其他表情、多人、mask渠道、角色参考、全部异常、
完整源视觉/快速反馈修复、真实音频、企业、32ms、其他实际媒体/首次创建/并发/生命周期及
同版用户部署仍按原台账继续。整体goal保持active。


## 第二字幕候选 B/C/D：本地受控证据，旧红保持

以下为独立组件 harness 的本地浏览器证据，未调用 API 或供应商，没有新生成确认，不能作为真实 Agnes 生成验收。第二候选只存在于忽略目录 `.runtime/agnes-emotion-bounded-candidate/`，未正式应用；正式/source panel SHA 均保持 `4cbb4e6a9e70a003cfb9b789aee9328a70d89906e41c9e0e565c5bba603728f0`。候选使用持续 span、纯 reducer 与当前 round，移除首 UUID 候选自身的历史 key 累积路径；这仍不证明第三方对象或 heap 严格有界。

B 只运行一次固定 36 主矩阵和 12 边界场，无缺场、driver/page/HTTP 错误。主矩阵每实现 12 场，source 准确终态 11/12、target 10/12、candidate 12/12；边界每实现 4 场均为 4/4。准确终态只指最终 aria/head/footer 为中性克制且可见，不代表动画或完整交互一致。当前三红原 JSON/PNG 均保留：`source-1440-reduce-rapid-b` 与 `target-1440-reduce-rapid-b` 在 20009/20014ms 时 aria/head 已中性，footer 仍为浅然莞尔 / `emotion-1-0`；`target-390-reduce-rapid-b` 在 20014ms 时 footer 仍为温柔 / `emotion-2-0`。三处 footer 均 opacity=1、blur=0。首批四红与本批三红不完全同场，不能以时序波动称确定根因或以新绿关闭旧红；正式应用 b、组件 B 与首 UUID 候选红均保持。B 详细记录、汇总和旧红引用为 `browser-notes.md`、`browser-b/summary.json`、`browser-b/previous-red-reference.json`。

B 的 label 场名虽有 during enter，但采样时两处已 idle，只验证显示后的同 ID 文字更新。另行授权的 C 只运行 source/target/candidate 各一场；三场均因观察器门槛等待 10000ms 超时，`label_action=null`，实际入场提交覆盖 0/3，不能记产品行为红或 label 分支通过。entry 模板的正则转义在生成实际源码时丢失，使 `blur(3.53037px)` 解析成 0。原生成 entry 的离线回归 exit=1 复现此错；仅改 CSS blur 字符串边界与切片、none=0、未知 NaN 的修正后实际生成 entry 12 个真/假样例 exit=0。C 的原 JSON/PNG/冻结 driver 与错误均保持，见 `browser-c-notes.md`、`browser-c/summary.json`、`observer-c-d.diff`、`gate-parser-{original,fixed}-result.json`。

父代理实际审读修正后另行授权 D，D 只有新的三场，不是 48 场矩阵。source/target/candidate 的 **first actual label commit 入场覆盖 3/3，准确终态 3/3**。以各场首欣喜 preset 已提交时间为锚，label 动作与实际提交如下，不能当成精确同帧：

| 实现 | label 动作 ms | head first commit ms | footer first commit ms | 实际提交观察 |
| --- | ---: | ---: | ---: | --- |
| source | 339.9 | 360.9 | 360.9 | 同动作前 DOM；head opacity=0.101342/blur=5.39195px，footer 0.112217/3.55113px；incoming start 未 complete |
| target | 214.5 | 229.7 | 229.7 | 同动作前 DOM；head 0.092613/5.44432px，footer 0.245695/3.01722px；incoming start 未 complete |
| candidate | 220.8 | 229.4 | 230.7 | head DOM1/round6/enter/head，0.106307/5.36216px；footer DOM2/round5/enter/footer，0.260454/2.95819px/y2.95819px；同动作前 DOM 与 round |

source/target 没有内部 reducer phase/round 观测，保留空值，只按真实 CSS、新 key incoming 事件与实际文案提交判定。候选实际文案已提交时仍是 enter，避免把动作前门槛成立当作覆盖。D 最终三场 aria/head/footer 均中性且 opacity=1、blur=0、transform=none；候选两处 idle，head round10/footer round9，DOM1/2。D 原图只是索引，入场触达以逐场 JSON 为准，全部六张原 PNG 已由子任务实际查看。root 可查三张终态图：

- `.runtime/agnes-emotion-bounded-candidate/browser-d/source-1440-no-preference-edge-label-enter-final.png`
- `.runtime/agnes-emotion-bounded-candidate/browser-d/target-1440-no-preference-edge-label-enter-final.png`
- `.runtime/agnes-emotion-bounded-candidate/browser-d/candidate-1440-no-preference-edge-label-enter-final.png`

对应原逐场 JSON 与 `-label-index.png` 同目录同 stem；`browser-d/summary.json`、`browser-d/report.json` 与 `browser-d-notes.md` 留完整 gate、first commit、round/DOM/CSS、事件和终态。D 冻结 driver SHA 为 `b1a70b53dbefb2411a3f22ceab332a1bfa25264a7564109fedc656fb2c0d226b`；候选 SHA=`995d9cbcf0581b2fbeb665d7dec1854612394db886573c3b6f249dc45d12eeac`，helper SHA=`35679cf1b0313c0c542320eacdd8223d3bace7de626be0742e842c2c1c886e7f`。

正常动画时序仍有差异。B 普通偏好 footer exit complete 相对 preset commit 为 source/target/candidate 183.9/181.2/183.4ms，enter complete 383.5/381.1/366.5ms；head 为 200.1/197.4/200.9ms 与 416.6/413.9/416.6ms。source/target 新字幕各有两次 start，candidate 各一次，未确定原因。footer 0.18s/head 0.20s 及原 blur/y 参数保留，安装的 Motion duration-only 默认 easeOut 是静态实现证据，不是实测浏览器曲线。D 同 preset commit 锚的既有 rAF 样式中，source 253.9ms 仍旧中性 opacity0，334.8ms 才新欣喜入场；target 208.4ms 新 head opacity0；candidate 200.3ms 旧 head exit、新 footer enter。时间与样式不是同帧，不能称完整动画一比一或已确定延迟根因。正常时间证据在 `browser-b/normal-transition-timing.{json,md}` 和 `browser-d/normal-frame-samples.{json,md}`。

B/C/D 的真实 Node 操作系统退出码均由持有原 Process 的 wrapper 在 Dispose 前读取；与首 UUID 批次的外部 ExitCode=null 缺口不同，不能追改首批 OS 证据。执行身份与独立只读后置如下：

| 批次 | Node PID / creation_ms | 外部完成 UTC / 真实 OS code | 后置身份与归属 |
| --- | --- | --- | --- |
| B | 5696 / 1791666854691 | 2026-10-10 21:24:17.1291970 / 0 | 原历史149条，148条在 Node 后创建的已记录身份退出；误收的 MarvisAssistant.exe PID46004 / 1791512014232 早于批次，因旧 ParentProcessId 复用被遍历收录，排除本批归属且未停止 |
| C | 40360 / 1791668229563 | 2026-10-10 21:38:27.4853842 / 0 | 19条已记录身份退出，无前置误收身份；0触达错误仍保留 |
| D | 14232 / 1791668768379 | 2026-10-10 21:46:58.1922401 / 0 | 19条已记录身份退出，无前置误收身份 |

各批 `browser-{b,c,d}.execution-result.json` 和对应 `independent-postcheck.json` 均独立归档，不复用 E/H 真实供应商环境报告；4277/4278 无 listener，未触碰主任务4199，未停止用户服务。三批正式/source/candidate/helper/driver、两端锁及已读实现 SHA 均匹配，固定源 HEAD=`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c` 且 clean。5秒采样可能漏短命后代；晚于批次创建本身也不能单独证明进程归属，只证明已记录 PID+creation_ms 身份退出。B 的漏 web/ 路径与数组未展开核验错误均留原文件，修正只读核验后才生成最终记录，没有因此重跑浏览器。

B 初始 reduce 主矩阵真实覆盖；动态 media query 虽改变，当前已安装 `useReducedMotion` 只从全局 current 初始化 useState，没有 setter/运行中订阅，候选 footer 始终保留 footer profile，动态 footer-instant 分支未触达。未换 hook 伪造产品覆盖。同 preset 3次点击和3次父 rerender 的 DOM/key/round 不变且没有新动画；非 Strict 候选采样 DOM1/2，Strict 实际卸载重挂载后3/4，采样同时最多两个字幕。这些是身份与 DOM 采样，不是 heap、detached DOM、subscriber、任意第三方 pending Promise、Offscreen/Suspense 或完整正式 Provider 生命周期证明。B 候选 start736/accepted615、D start8/accepted8，invalid accepted 均0而 ignored 均0；被 stop 的 Promise 可能不回调，未实测陈旧回执真实到达后被忽略。C/D 的 parser 与 driver/transpile/PowerShell 语法验证、B/D 离线摘要整理均实际执行，不能补出未覆盖分支。

本片只新增本地证据与说明，无生产 panel、依赖/锁、适配账本或 DDL 修改。D 受控 label 分支通过不等于正式应用快速反馈修复，不关闭 R09/R23、原 b/B/首批红，也不宣称完整迁移、完整一比一、无泄漏、动态 profile 或真实供应商验收。

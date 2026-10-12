# 原 Skill / Agnes Chat 正式队列验收

记录日期：2026-10-11。本片 C1440 的真实供应商、完整 Python 后置及人工正文/画布
内容检查通过；A 的测试断言失败和 B 的长度内容失败完整保留。本片只关闭已有私人
Skill snapshot → 原文字节点生成这一明确组合，不关闭 R09 或完整 M0–M5。

## 输入与实际批次

三批均为 1440×900 Chromium、snapshot、Chat、`agnes-3.0-flash`、单份人工纯文字，
没有媒体输入或上传。本人保存的 `chat-completion` 配置使用 `openai_chat.v1`，实际
请求为 `POST https://api.agnes-ai.cn/v1/chat/completions`，model正确、stream=true；
冻结配置与凭据保持服务端加密，不传到浏览器。旧 observer 只记录字段名/路径等摘要，
不将应用冻结 prompt 冒称完整 wire messages 已归档。

原私人 Skill v3 名称“私人站台镜头模板”，模板要求正文逐字包含“清晨车站”和
“红色礼盒”，输出约束为 30–80 字中文镜头正文，不加标题、解释、列表或代码块。
原提示词为“请按所连接的私人模板写一段短剧镜头正文。”；按原 Skill 输入编译拼接，
冻结完整旧场景 prompt。受理后新 context 通过既有 HTTP commit 修改作者当前私人
模板为 v4 的“深夜机场与蓝色行李箱”；原任务仍必须使用已受理旧模板提示词。

| 批次 | 原 pytest 摘要 | 真实供应商/任务 | 实际结论 |
| --- | --- | --- | --- |
| a1440 | exit1；1 failed、1 warning，82.41s | 1 POST200、task succeeded；3 completed delivery | browser.complete，37字正文及 generated/final-restored 两原图人工小场通过；Python line406错误排除resolved_parameters导致红，完整Python后置未完成。 |
| b1440 | pytest Exit/exit1；1 warning，64.43s，无pass | 1 POST200、task succeeded；1 completed delivery | canonical仅14字，JS line282长度门禁失败；bind/final/member驱动步骤、重复AMQP探针和完整Python后置未执行；内容failed。 |
| c1440 | exit0；1 passed、1 warning，78.40s，0skip | 1 POST200、task succeeded；1正常+2重复/陈旧消息全部完成 | 完整Python后置passed，browser.complete且页面/HTTP错误为空；61字真实正文及generated/final-restored/member三原PNG人工小场内容passed。 |

三批是相同输入与标准的独立新任务，不重提旧任务，也不删除 A/B 原红。供应商成功
不能代替后置或内容；C 的单次合格输出不使 B 的实际长度失败变为通过。

关键异常与复现阶段：

```text
A：浏览器终态/成员步骤及真实重复消息之后，Python冻结快照后置
tests/integration/test_canvas_agnes_skill_broker_live.py:406
assert binding.source_snapshot["request"] == { ... if key != "resolved_parameters" }
AssertionError: Left contains 1 more item:
{'resolved_parameters': {'stream': True, 'stream_options': {'include_usage': True}}}

B：同任务running恢复并放行真实POST，读取succeeded真实终文后
AssertionError [ERR_ASSERTION]: The expression evaluated to a falsy value:
  assert.ok(Object.values(report.content_automated_checks).every(Boolean))
  at .../verify-agnes-skill-python.mjs:282:12
canonical：清晨车站，旅客轻启红色礼盒。
within_requested_length=false；14字未满足原30–80字约束
```

A 是测试合同误判：`AiGenerationService._insert` 在 create_locked 准入阶段已经写入
resolved_parameters，Canvas register 完整 deepcopy。正式 B/C 修正为 binding request
与 record.request_data 冻结整对象严格相同，并显式核对
`{"stream":true,"stream_options":{"include_usage":true}}`。没有删除该字段、放宽
冻结合同或修改生产调用链。B/C源码相同，B长度失败后C仍沿用相同标准。

## 原 UI、保存、来源与恢复

入口为原 Skill 右侧连接点 → 原“文本”菜单创建节点/连接 → 原文本提示词 → 原生成
按钮，测试代码不直接创建任务。检查 Skill → text 连接及 text 的 x 为 Skill x +
Skill宽度 + 96。旧模板只以已编译 prompt 冻结；`sourceNodeId == nodeId == 新text.id`，
generation与binding source node均为text。完整Skill对象、ID、version、tags没有作为
独立generation source snapshot保存，不能宣称完整Skill版本冻结。

沿用既有 LiveGateway/正式 observer，在真实 SafeTransport 供应商POST前hold，
上限90秒，release精确为`real-provider-submit-allowed`。A/B/C各有三次独立
checkpoint：旧窗口running；旧context关闭、仅cookies新context完成作者私人v4更新；
新页恢复同一running task。每次submissions=0、requests/results为空，第三次后才
release。不制造供应商内容、固定分片或自然首段后中断。

C原Ctrl+S等待保存成功/“已保存”ACK；关闭前各finite viewport分量允许live与GET
相差最多1ULP，2ULP/NaN/Infinity拒绝，保存live/stored/difference/ulp_distance。
新context仅cookies、初始origins为空，在fit前逐字段严格等于持久stored值；running
和终态恢复仍为原任务、没有新增POST。该序列化等价不放宽节点几何。

私人v4更新、旧prompt task与终态Skill metadata互相核对。此场在保存ACK后关闭旧
context，修改走原API commit，不覆盖未保存模板UI输入、并发旧响应覆盖或原Skill
编辑控件的真实UI编辑。POST前hold没有真实delta，不称自然首段到达后UI仍loading。

## raw / canonical 与真实增量

原始供应商终文归档为raw_supplier_text，共享正文为canonical_text，动态读取实际
输出，不使用固定FIRST/SECOND或固定SSE分片数。

| 实际字段 | 核对合同 |
| --- | --- |
| provider result、AIGenerationRecord.text_content | raw逐字相同 |
| response_data.canvas_text_draft、数据库/API delta拼接 | raw逐字相同 |
| succeeded task.resultJson.text/textDraft、replay.finalText/textDraft | canonical逐字相同 |
| CanvasResult、bind响应、作者/成员text node | canonical逐字相同 |

Python精确断言canonical == raw.strip()，与durable_output一致；JS不将trim当作Python
strip的通用等价，也不对所有字段strip。本次三批raw/canonical各自相同，没有首尾
空白；该实际现象不替代带空白返回的独立真实场景。

C真实正文为：

> 清晨车站晨光微熹，旅客低头紧握红色礼盒，指节泛白。列车进站的气流吹乱发丝，他深吸一口气，迈步走向月台尽头，身影被拉得细长。

C为61个Unicode码点/183UTF-8字节，6条delta，sequence严格为1..6；数据库精确核对
task_binding_id、generation_record_id、作者及每段UTF-8 byte_count。两份正文摘要
与delta拼接摘要均为
`3f3139fe5e41b1677c448abf0706532d34a603f6ac4b005397b33a209cbad443`。
CanvasTextStreamWriter合并真实回调，6条delta不等于6个原始SSE event。

root已实际查看C原generated/final-restored/member三PNG与全文：握礼盒、深吸气、
迈步动作具体，中文镜头正文，无标题/解释/列表/代码块；两关键词、30–80字及v4新
场景词不出现的自动条件满足。内容结论仅此作品与三视图passed。
A的37字正文与generated/final-restored两PNG也由root实看，小场内容passed但pytest
整体failed；B实际14字内容failed保持。人工JSON明确reviewer=root、实看依据和范围，
不是collector自行决定内容通过。

## 正式栈、重复消息与权限

C实际启动API/Scheduler/text/image/video/audio/render/agent八角色，正常API lifespan
与readiness、独立Snowflake ID及正式text Worker执行PID核对通过。正常任务消息
版本1，终态task.message_version=2；随后同任务版本1与0两条真实AMQP消息均完成，
exchange/routing_key正确，provider report不变且record.call_no=1，没有二次供应商
提交。八角色存活与自身cleanup检查执行；空闲媒体Worker不证明其业务路径。

作者原消费者canvas.task.bind把同text ID的真实canonical正文直接回填。作者只有
私人Skill与任务过程，成员只见空Skill metadata与共享正文；成员/外人任务、增量、
私人模型配置均404，外人画布404。Skill公共ORM metadata为空，只有作者
CanvasNodeUserState保存v4；无MediaFile、资源上传/下载或供应商媒体结果。没有用
项目权限代替作者候选权限，也没有执行共用助手图工具。

旧observer未采集实际wire messages、响应MIME或原始SSE，因此冻结输入一致、实际
POST200/stream=true/供应商终文与增量归档不能升级为wire内容级证明。
database_errors仅监听fixture进程engine，不是独立Worker全部SQL错误监控。
B退出后reconciliation保存原task succeeded、canRetry/canResume false及
new_submission_performed=false，按原身份核对，没有自动重提。

## 证据、清理与验证边界

原日志为`backend/.runtime/agnes-skill-broker-live-{a,b,c}1440.log`，各自同名-temp
中的`test_original_skill_real_agnes0/`保留browser/provider/Worker/stack及截图；
C另有acceptance、delta摘要和database-errors。首次A源码另存
`backend/.runtime/agnes-skill-broker-a-python-source.py`。

新独立collector已实际采集至
`backend/.runtime/agnes-skill-evidence-a-through-c-a/matrix.json`及逐批evidence JSON；
UTF-16/CRLF原日志摘要正确识别为A failed82.41s、B interrupted64.43s、C passed78.40s。
A/B以preserve-red保留，raw/canonical分列并保留各自bytes/hash，缺失acceptance不
判Python后置通过；净化UTF-8日志另存，不重写原log/report或旧collector。
人工依据在`.runtime/agnes-skill-draft/content-{a,b,c}1440.json`。

三批各自独立环境报告`agnes-skill-broker-post-{a,b,c}-environment.json`均已实际
读取：各检查10个角色/执行PID，原PID存活0、reused_pids空，4199/API测试listener0，
测试桶0（MinIO总桶3）、task容器0；用户PID28808/28840与启动时间保持。两源树干净，
HEAD保持`ef65732bff5ee99019664ca8689608b1c7831cbb`和
`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`。
被动AMQP报告`agnes-skill-broker-ab-cleanup.json`实际核对A/B共22queue/4exchange不存在；
`agnes-skill-broker-c-cleanup.json`核对C的11queue/2exchange不存在，没有创建/删除资源。
上述报告均在backend/.runtime，各批不借旧表情或其他环境报告作为证明。

本片两新源码及专项helper已实际通过AST、Ruff/format，driver Node语法通过；本地
视口1ULP/2ULP/nonfinite/严格fresh及collector日志/脱敏自检属于本地验证，不是模型
验收。root新跑全仓Ruff check exit0；全仓format仍在旧
backend/scripts/apply_assembly_migration.py的混合换行1文件失败、768已格式化，
该文件与本片基线字节相同。没有顺手修旧脚本或重跑无关unit/API、Node、build、source
及完整E2E，旧结果保持各自版本/范围。

A Python SHA256为
`6d05a3a70b39be4df759091b72635eb79eb235c33779f389030f745a557a39f8`；正式B/C为
`ca61d6a340bc78eabebc97519af5d48cd6c3de99d6f80809dd18b37c240477c2`；driver三批相同为
`8b98e0fda8bd709668f683181deac6cb144d97dbc97d1bf8abecf00b93bc1f9e`。

本片允许新test/driver/本审计与同五维护共八文件，基线2860文件，curated增补
agnes-emotion-broker后35个旧冻结源码保持；专项artifact/link/凭据/边界diff检查另存
实际报告。旧Skill test/driver、正式observer、LiveGateway、生产调用链、标准模式、
依赖、ORM/表结构均不修改，没有DDL。全仓diff--check既有两迁移SQL的225条尾空格
保持原基线；本片八文件范围单独检查，不将旧缺口冒称已修。

实际artifact报告`backend/.runtime/agnes-skill-artifacts-final-a.json`通过：2860→2863
文件，仅本片八文件变化，35份旧冻结源码保持，六文档317个本地链接/锚点有效，
errors为空。root独立检查`agnes-skill-root-final-check-a.json`再次核对三次真实POST200、
C完整后置、33队列/6交换机不存在和旧格式/尾空格文件与基线字节相同；57份源码、
维护文档及原报告/日志文本凭据扫描零命中。新增两份验收源码另外冻结为
`backend/.runtime/agnes-skill-broker-final-source-manifest.json`；检查不发送新模型请求。

其他Skill分支/安装/正文/空模板、协议、窄屏、异常、未保存/并发/原编辑UI、完整固定
源目标视觉、真实音频、企业授权、32ms、首次创建/生命周期及同版用户部署继续原台账。
[生成队列总审计](2026-10-10-beeftv-agnes-generation-broker-audit.md)、
[真实应用总审计](2026-10-10-beeftv-agnes-live-audit.md)、
[剩余工作](2026-10-08-beeftv-remaining-work.md)继续保持整体goal active。

Skill 索引只提供有界的名称、用途、排除条件和冻结版本，不含正文与资源。需要更完整的目录信息时调用 runtime__search_skills 分页检索。
手选方法在执行对应任务前按冻结版本加载；需要自动 SOP 才能可靠执行时调用 runtime__load_skill(name, content_version, purpose)。普通讨论无需自动方法。
加载返回完整正文、排除条件与资源 manifest。正文只以本 scope 中匹配的 AI tool_call 和 ToolMessage 为依据，不能把 loaded 标记当作已在上下文；恢复沿原 canonical 正文回执，不自行改用最新版本。
资料、模板、图片与 scripts/ 下的脚本继续按需读取。runtime__read_skill_resource 只读取；脚本物化后还需要显式执行。requirements.txt、package.json 等依赖清单是普通文本资料。
方法不能扩大用户目标、权限、读取范围、媒体能力或预算。未知工具、模式不支持或外部来源尚未接入时说明具体缺口。
阶段完成后可用 runtime__release_skill 撤去自动方法的正文保留资格；手选方法由用户管理。用户移除或遗忘来源后，旧摘要也不能重新注入其正文。
每个 scope 独立加载、释放和压缩；主助手不能自动继承子任务正文，子任务也不能读写主助手的活动数组。

"""AI 创作助手 Agent 子系统。

子包结构：
  core/         基础机制（锁、状态机、事件）
  gateway/      模型 HTTP 协议
  memory/       记忆子系统
  skill/        Skill 管理
  task/         任务状态机
  context/      上下文组装
  tools/        V3 工具注册与执行
  native/       原生配音媒体
  session/      会话生命周期
  coordinator/  顶层执行协调
  legacy/       V2 遗留路径（只维护不演化）
"""

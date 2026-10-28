// 模拟后端返回的展示元数据；生产界面的权限与工具展示规则仍由后端决定。
const presentation: Record<string, [string, string]> = {
  describe_creation_target: ['step', '核对写入目标'],
  read_current_script: ['step', '读取当前剧本'],
  propose_script_patch: ['artifact', '准备剧本修改建议'],
  read_selected_subjects: ['step', '读取选定内容'],
  read_task_results: ['step', '读取作品内容'],
  read_image_group: ['step', '读取图片组'],
  read_image_content: ['step', '读取图片'],
  get_generation_status: ['step', '查询生成进度'],
  apply_standard_patch: ['artifact', '保存到正文'],
  apply_canvas_patch: ['artifact', '更新画布'],
  submit_generation_batch: ['artifact', '生成媒体'],
  set_image_group_primary: ['artifact', '设置主图'],
  load_skill: ['step', '读取创作方法'],
  read_skill_resource: ['step', '读取创作方法'],
  search_skills: ['silent', '执行创作操作'],
  read_operation_receipt: ['silent', '执行创作操作'],
};

export function operationView<T extends { tool_name: string }>(operation: T) {
  const [visibility, label] = presentation[operation.tool_name] ?? ['step', '执行创作操作'];
  return { visibility, label, ...operation };
}

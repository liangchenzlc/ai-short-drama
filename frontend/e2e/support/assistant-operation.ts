// 模拟后端返回的展示元数据；生产界面的权限与工具展示规则仍由后端决定。
const presentation: Record<string, [string, string]> = {
  describe_creation_target: ['visible', '核对写入目标'],
  read_current_script: ['visible', '读取当前剧本'],
  propose_script_patch: ['visible', '准备剧本修改建议'],
  read_selected_subjects: ['visible', '读取选定内容'],
  read_task_results: ['visible', '读取作品内容'],
  read_image_group: ['visible', '读取图片组'],
  read_image_content: ['visible', '读取图片'],
  get_generation_status: ['visible', '查询生成进度'],
  workbench__apply_standard_patch: ['visible', '保存到正文'],
  workbench__apply_canvas_patch: ['visible', '更新画布'],
  workbench__submit_generation_batch: ['visible', '生成媒体'],
  workbench__set_image_group_primary: ['visible', '设置主图'],
  load_skill: ['visible', '读取创作方法'],
  read_skill_resource: ['visible', '读取创作方法'],
  search_skills: ['hidden', '执行创作操作'],
  read_operation_receipt: ['hidden', '执行创作操作'],
};

export function operationView<T extends { tool_name: string }>(operation: T) {
  const [trace_visibility, label] = presentation[operation.tool_name] ?? ['visible', '执行创作操作'];
  return { trace_visibility, label, ...operation };
}

export function taskOrigin(task: { display_context?: { project?: string | null; episode?: string | null; subject: string; scope: string }; source?: { scene: string } | null }): string {
  const context = task.display_context;
  if (context) return [context.project || context.scope, context.episode, context.subject].filter(Boolean).join(' / ');
  return ({ novel_script: '小说改编', script_assets: '素材提取', script_shots: '分镜脚本', shot_image: '分镜生图', asset_image: '素材生图' } as Record<string, string>)[task.source?.scene ?? ''] || '通用任务';
}

export function taskPrompts(detail: { input: Record<string, unknown>; effective_prompt?: string | null }): { label: string; content: string }[] {
  const prompt = detail.effective_prompt || detail.input.prompt;
  if (typeof prompt === 'string' && prompt.trim()) return [{ label: '生成提示词', content: prompt }];
  if (!Array.isArray(detail.input.messages)) return [];
  return detail.input.messages.flatMap(message => {
    if (!message || typeof message !== 'object' || typeof message.content !== 'string') return [];
    return [{ label: ({ system: '系统提示词', user: '用户提示词', assistant: '上下文提示词' } as Record<string, string>)[message.role] || '提示词', content: message.content }];
  });
}

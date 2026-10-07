/** View state travels with stage navigation; it never changes the underlying episode. */
export function withEpisodeView(path: string, search: string): string {
  const parameters = new URLSearchParams(search);
  const query = parameters.toString();
  return query ? `${path}?${query}` : path;
}

export function isAgentRunActive(status: string | null | undefined): boolean {
  return !!status && ['queued', 'running', 'waiting_generation', 'waiting_review'].includes(status);
}

export function agentRunLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    queued: '排队中', running: '运行中', waiting_generation: '等待生成结果', waiting_review: '等待确认',
    succeeded: '已完成', failed: '运行失败', cancelled: '已停止',
  };
  return status ? labels[status] ?? '状态待核对' : '尚无运行';
}

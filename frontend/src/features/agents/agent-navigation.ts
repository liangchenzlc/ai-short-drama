export type CreationMode = 'prompt' | 'agent';

export function creationMode(search: string): CreationMode {
  return new URLSearchParams(search).get('mode') === 'agent' ? 'agent' : 'prompt';
}

/** View state travels with stage navigation; it never changes the underlying episode. */
export function withEpisodeView(path: string, search: string): string {
  const parameters = new URLSearchParams(search);
  const query = parameters.toString();
  return query ? `${path}?${query}` : path;
}

export function changeCreationMode(search: string, mode: CreationMode): URLSearchParams {
  const parameters = new URLSearchParams(search);
  if (mode === 'agent') parameters.set('mode', 'agent');
  else parameters.delete('mode');
  return parameters;
}

export function changeConversation(search: string, id?: string): URLSearchParams {
  const parameters = new URLSearchParams(search);
  parameters.set('mode', 'agent');
  if (id) parameters.set('conversation', id);
  else parameters.delete('conversation');
  return parameters;
}

export function stageConversation(search: string, stage: string): string | undefined {
  const parameters = new URLSearchParams(search);
  return parameters.get(`conversation_${stage}`) ||
    ((!parameters.has('conversation_stage') || parameters.get('conversation_stage') === stage)
      ? parameters.get('conversation') || undefined : undefined);
}

export function selectStageConversation(search: string, stage: string, id?: string): URLSearchParams {
  const parameters = changeConversation(search, id);
  parameters.set('conversation_stage', stage);
  if (id) parameters.set(`conversation_${stage}`, id);
  else parameters.delete(`conversation_${stage}`);
  return parameters;
}

export function withStageConversation(path: string, search: string, current: string, next: string): string {
  const parameters = new URLSearchParams(search);
  const id = stageConversation(search, current);
  if (id) parameters.set(`conversation_${current}`, id);
  parameters.set('conversation_stage', next);
  const nextId = parameters.get(`conversation_${next}`);
  if (nextId) parameters.set('conversation', nextId);
  else parameters.delete('conversation');
  return withEpisodeView(path, parameters.toString());
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

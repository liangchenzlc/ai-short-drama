/** Project conversation and editing selection travel together, without object chats. */
export function assistantView(search: string): URLSearchParams {
  const parameters = new URLSearchParams(search);
  const legacy = parameters.get('mode') === 'agent';
  const id = parameters.get('conversation');
  if (legacy) {
    parameters.set('assistant', 'open');
    if (id && /^\d+$/.test(id)) parameters.set('legacy_conversation', id);
    parameters.delete('conversation');
  }
  for (const [key, oldKey] of [['asset', 'agent_subject_assets'], ['shot', 'agent_subject_storyboard']]) {
    const id = parameters.get(key) ?? (!legacy ? parameters.get(oldKey) : null);
    if (id && /^\d+$/.test(id)) parameters.set(key, id);
    else parameters.delete(key);
  }
  for (const key of [...parameters.keys()]) {
    if (key === 'mode' || key === 'conversation_stage' || key.startsWith('conversation_') || key.startsWith('agent_subject_')) parameters.delete(key);
  }
  return parameters;
}

export function selectEpisodeObject(search: string, stage: string, id: string | null): URLSearchParams {
  const parameters = assistantView(search);
  const key = stage === 'assets' ? 'asset' : stage === 'storyboard' ? 'shot' : null;
  if (key) {
    if (id) parameters.set(key, id);
    else parameters.delete(key);
  }
  return parameters;
}

/** Query updates must merge the live URL, even while Router renders a transition. */
export function replaceEpisodeView(
  episodeRoot: string,
  navigate: (to: { pathname: string; search: string; hash: string }, options: { replace: true }) => unknown,
  update: (parameters: URLSearchParams) => URLSearchParams,
  current: Pick<Location, 'pathname' | 'search' | 'hash'> = window.location,
): boolean {
  if (current.pathname !== episodeRoot && !current.pathname.startsWith(`${episodeRoot}/`)) return false;
  const next = update(new URLSearchParams(current.search)).toString();
  if (next === new URLSearchParams(current.search).toString()) return false;
  navigate({ pathname: current.pathname, search: next ? `?${next}` : '', hash: current.hash }, { replace: true });
  return true;
}

export function setAssistantOpen(search: string, open: boolean): URLSearchParams {
  const parameters = assistantView(search);
  if (open) parameters.set('assistant', 'open');
  else parameters.delete('assistant');
  return parameters;
}

export function selectAssistantConversation(search: string, id: string): URLSearchParams {
  const parameters = setAssistantOpen(search, true);
  parameters.set('conversation', id);
  parameters.delete('legacy_conversation');
  return parameters;
}

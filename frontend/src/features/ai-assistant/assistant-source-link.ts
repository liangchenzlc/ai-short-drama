import type { AssistantSelectedSubject } from '../../api/types/assistant';

export function assistantSourceLink(subject: AssistantSelectedSubject): string | null {
  const source = subject.source_location ?? (['asset', 'shot', 'node', 'sound_line'].includes(subject.kind)
    ? { project_id: subject.project_id, kind: subject.kind, id: subject.id, canvas_id: subject.source_id } : null);
  if (!source) return null;
  if (source.kind === 'node') return `/canvas-app/canvas/${encodeURIComponent(source.canvas_id!)}?node=${encodeURIComponent(source.id)}`;
  if (source.kind === 'sound_line') return `/projects/${encodeURIComponent(source.project_id)}/assembly?source_line=${encodeURIComponent(source.id)}`;
  const query = new URLSearchParams({ [source.kind]: source.id });
  if ('image_group_id' in source && source.image_group_id) query.set('source_image_group', source.image_group_id);
  if ('image_slot_id' in source && source.image_slot_id) query.set('source_image_slot', source.image_slot_id);
  return `/projects/${encodeURIComponent(source.project_id)}/${source.kind === 'asset' ? 'assets' : 'storyboard'}?${query}`;
}

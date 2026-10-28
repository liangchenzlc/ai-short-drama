import type { TextGenerationRequest } from '../../../api/types/generations';
import type { WritingSnapshot } from '../writing-session';

export type WritingTarget = 'outline' | 'script';
export interface ScriptSelection { start: number; end: number; text: string; source: string; top: number; left: number; bottom?: number }

/** DOM textarea offsets are UTF-16; the API's half-open interval counts code points. */
export function codePointSelection(selection: ScriptSelection) {
  const { source, start, end, text } = selection;
  if (start < 0 || end <= start || end > source.length || source.slice(start, end) !== text) throw new Error('选中文字已变化，请重新选择。');
  const points = Array.from(source);
  const pointStart = Array.from(source.slice(0, start)).length;
  const pointEnd = Array.from(source.slice(0, end)).length;
  if (points.slice(pointStart, pointEnd).join('') !== text) throw new Error('选区包含不完整字符，请重新选择。');
  return { start: pointStart, end: pointEnd, selected_text: text };
}

export function writingRewriteRequest(projectId: string, state: WritingSnapshot, selection: ScriptSelection, instructions: string, model: string): TextGenerationRequest {
  if (!state.scriptId || state.script !== selection.source) throw new Error('原文已变化，请重新选择需要修改的文字。');
  if (!instructions.trim()) throw new Error('请填写修改要求。');
  return {
    ...(model ? { config_id: model } : {}), parameters: {}, instructions,
    source: { scene: 'script_rewrite', project_id: projectId, content_version: state.contentVersion, script_id: state.scriptId, ...codePointSelection(selection) },
  };
}

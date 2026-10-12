import { http } from '../http';

export type CanvasEditorToolId = 'prompt-optimizer' | 'ai-art-critique' | 'runninghub-workflow-provider';
export type CanvasEditorToolState = {
  pluginId: CanvasEditorToolId;
  platformAvailable: boolean;
  userEnabled: boolean;
  userConfigured: boolean;
  effectiveEnabled: boolean;
  canToggle: boolean;
  canConfigure: boolean;
};
export type CanvasEditorToolStatuses = {
  statuses: Record<CanvasEditorToolId, 'enabled' | 'disabled'>;
  states: Record<CanvasEditorToolId, CanvasEditorToolState>;
};
const root = '/canvas-runtime/plugins';
export const canvasEditorTools = {
  async read(signal?: AbortSignal) {
    return (await http.get<CanvasEditorToolStatuses>(`${root}/status`, { signal })).data;
  },
  async setEnabled(id: CanvasEditorToolId, enabled: boolean, signal?: AbortSignal) {
    return (await http.put<{ state: CanvasEditorToolState }>(`${root}/${id}/activation`, { enabled }, { signal })).data.state;
  },
};

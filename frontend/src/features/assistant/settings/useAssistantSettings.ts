import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { editSettings, receiveSettings, settingsDirty, settingsPatch, settingsState } from '@host/features/assistant/settings/assistant-settings-state';
import { useStudioMessage } from '@host/shared/ui/StudioFeedback';
import type { AssistantSettingsPatch } from '@host/types/assistant/api';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useAssistantSettings(active: boolean) {
  const [state, setState] = useState(settingsState);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [issue, setIssue] = useState('');
  const [blocked, setBlocked] = useState(false);
  const latest = useRef(state); latest.current = state;
  const live = useRef(true), request = useRef(0), lock = useRef(false);
  const message = useStudioMessage();
  useEffect(() => { live.current = true; return () => { live.current = false; request.current++; }; }, []);
  const read = useCallback(async (signal?: AbortSignal) => {
    const id = ++request.current, revision = latest.current.revision;
    setLoading(true);
    try {
      const value = await assistantApi.settings(signal);
      if (!live.current || signal?.aborted || id !== request.current) return;
      setState(old => receiveSettings(old, value, revision));
      setBlocked(false); setIssue('');
    } catch (cause) {
      if (live.current && !signal?.aborted && id === request.current) setIssue(`读取助手设置失败：${errorMessage(cause)}`);
    } finally { if (live.current && id === request.current) setLoading(false); }
  }, []);
  useEffect(() => {
    if (!active || latest.current.saved) return;
    const controller = new AbortController(); void read(controller.signal);
    return () => controller.abort();
  }, [active, read]);
  async function save() {
    if (lock.current || loading || blocked || !state.saved) return;
    lock.current = true; setSaving(true); setIssue('');
    const revision = state.revision, body = settingsPatch(state);
    try {
      const value = await assistantApi.updateSettings(body);
      if (!live.current) return;
      setState(old => receiveSettings(old, value, revision, true));
      message.success('助手运行设置已保存。', 'assistant-runtime-settings');
    } catch (cause) {
      if (!live.current) return;
      const conflict = cause instanceof ApiError && cause.status === 409;
      setBlocked(true);
      setIssue(conflict ? '设置已在其他窗口变化，草稿已保留。请读取最新版本后核对并保存。' : `保存未确认，草稿已保留。请读取最新版本核对：${errorMessage(cause)}`);
    } finally { lock.current = false; if (live.current) setSaving(false); }
  }
  return { ...state, dirty: settingsDirty(state), loading, saving, blocked, issue, read, save,
    edit: (patch: Partial<AssistantSettingsPatch>) => setState(old => editSettings(old, patch)) };
}

import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { editSettings, receiveSettings, settingFields, settingsConflicts, settingsDirty, settingsPatch, settingsState } from '@host/features/assistant/settings/assistant-settings-state';
import type { AssistantPreferences, AssistantSettings } from '@host/types/assistant/api';
import type { AssistantSettingsState } from '@host/types/assistant/settings/editor';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useAssistantSettings(active: boolean) {
  const [state, setState] = useState(settingsState);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [issue, setIssue] = useState('');
  const [blocked, setBlocked] = useState(false);
  const [conflict, setConflict] = useState<AssistantSettings | null>(null);
  const [conflictFields, setConflictFields] = useState<(keyof AssistantPreferences)[]>([]);
  const latest = useRef(state);
  const live = useRef(true), lock = useRef(false), delay = useRef(600);
  const update = useCallback((change: (old: AssistantSettingsState) => AssistantSettingsState) => {
    setState(old => { const next = change(old); latest.current = next; return next; });
  }, []);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  const reconcile = useCallback((value: AssistantSettings) => {
    const fields = settingsConflicts(latest.current, value);
    if (fields.length) {
      setConflict(value); setConflictFields(fields); setBlocked(true);
      setIssue('其他窗口修改了相同设置，请核对后选择保留的内容。');
      return false;
    }
    update(old => receiveSettings(old, value, old.revision));
    setBlocked(false); setConflict(null); setIssue('');
    return true;
  }, [update]);

  const read = useCallback(async () => {
    setLoading(true);
    try {
      const value = await assistantApi.settings();
      if (live.current) reconcile(value);
    } catch (cause) {
      if (live.current) { setIssue(`读取设置失败：${errorMessage(cause)}`); setBlocked(true); }
    } finally { if (live.current) setLoading(false); }
  }, [reconcile]);
  useEffect(() => { if (active && !latest.current.saved) void read(); }, [active, read]);

  const flush = useCallback(async () => {
    if (lock.current || !settingsDirty(latest.current)) return;
    lock.current = true; setSaving(true); setIssue('');
    const submitted = settingsPatch(latest.current), revision = latest.current.revision;
    try {
      const value = await assistantApi.updateSettings(submitted);
      if (live.current) update(old => receiveSettings(old, value, revision, true, submitted));
    } catch (cause) {
      if (!live.current) return;
      // 断连后先核对原字段，不能把已成功的写入当作未受理再发送。
      setBlocked(true);
      try {
        const value = await assistantApi.settings();
        if (!live.current) return;
        if (settingFields.every(key => !(key in submitted) || submitted[key] === value[key])) {
          update(old => receiveSettings(old, value, revision, true, submitted));
          setBlocked(false);
        } else if (reconcile(value) && !(cause instanceof ApiError && cause.status === 409)) {
          setBlocked(true); setIssue(`未能同步，输入已保留：${errorMessage(cause)}`);
        }
      } catch {
        if (live.current) setIssue(`未能确认保存结果，输入已保留：${errorMessage(cause)}`);
      }
    } finally {
      lock.current = false;
      if (live.current) setSaving(false);
    }
  }, [reconcile, update]);

  const dirty = settingsDirty(state);
  useEffect(() => {
    if (!dirty || loading || saving || blocked) return;
    const timer = window.setTimeout(() => void flush(), delay.current);
    return () => window.clearTimeout(timer);
  }, [dirty, state.revision, state.saved, loading, saving, blocked, flush]);
  useEffect(() => {
    if (!dirty && !saving) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty, saving]);

  function resolve(key: keyof AssistantPreferences, useServer: boolean) {
    if (!conflict) return;
    if (useServer) update(old => editSettings(old, { [key]: conflict[key] }));
    const remaining = conflictFields.filter(field => field !== key);
    setConflictFields(remaining);
    if (!remaining.length) {
      update(old => receiveSettings(old, conflict, old.revision));
      setConflict(null); setBlocked(false); setIssue(''); delay.current = 0;
    }
  }
  return { ...state, dirty, loading, saving, blocked, issue, conflict, conflictFields, read, resolve,
    edit(patch: Partial<AssistantPreferences>, immediate = false) {
      delay.current = immediate ? 0 : 600;
      update(old => editSettings(old, patch));
    },
  };
}

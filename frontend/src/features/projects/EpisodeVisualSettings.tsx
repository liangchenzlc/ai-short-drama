import { useRef, useState } from 'react';
import { Alert, AutoComplete, Select } from 'antd';
import { projectsApi, projectError } from '../../api/modules/projects';
import type { EpisodeWorkflow } from './episode-workflow';

export function EpisodeVisualSettings({ projectId, episodeId, value, disabled, onChange }: {
  projectId: string; episodeId: string; value: EpisodeWorkflow; disabled: boolean;
  onChange: (value: EpisodeWorkflow) => void;
}) {
  const [style, setStyle] = useState(value.style);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const latest = useRef(value); latest.current = value;
  const saving = useRef(false);
  async function save(patch: { aspect?: '16:9' | '9:16'; style?: string }) {
    if (saving.current || disabled) return;
    saving.current = true;
    setBusy(true); setError('');
    try {
      const saved = await projectsApi.updateEpisode(projectId, episodeId, patch);
      onChange({ ...latest.current, aspect: saved.aspect, style: saved.style }); setStyle(saved.style);
    } catch (cause) { setError(projectError(cause)); }
    finally { saving.current = false; setBusy(false); }
  }
  return <>
    <label className="writing-control"><span>本集画幅</span><Select aria-label="本集画幅" value={value.aspect} disabled={disabled || busy} options={[{ value: '16:9', label: '横屏 16:9' }, { value: '9:16', label: '竖屏 9:16' }]} onChange={aspect => void save({ aspect })}/></label>
    <label className="writing-control"><span>视觉风格</span><AutoComplete aria-label="视觉风格" value={style} disabled={disabled || busy} maxLength={255} options={Array.from(new Set([value.style, '都市写实', '电影质感', '国风水墨', '清透水彩', '日系动画', '三维动画'].filter(Boolean))).map(value => ({ value }))} onChange={setStyle} onSelect={style => void save({ style })} onBlur={() => { if (style !== value.style) void save({ style: style.trim() }); }} placeholder="选择风格或输入自定义风格"/></label>
    {error && <Alert type="error" message={error}/>}
    <p className="episode-help" role="status">{busy ? '正在保存本集设置…' : '本集设置保存到服务端。'}</p>
  </>;
}

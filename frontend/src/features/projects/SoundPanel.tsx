import { confirmAction } from '../../components/ui/confirm';
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams } from 'react-router-dom';
import { Button, Checkbox, ConfigProvider, Drawer, Dropdown, Input, InputNumber, Select, Slider, Spin, Tabs, Tooltip } from 'antd';
import { Captions, Check, CircleHelp, Download, Ellipsis, Mic, Plus, Save, Trash2, Upload, Volume2, X } from 'lucide-react';
import { AssemblyAction } from './AssemblyAction';
import { StudioFeedback, StudioFeedbackIndicator, StudioMessageProvider } from '../../components/ui/StudioFeedback';
import { errorMessage } from '../../api/http';
import { soundApi, type AudioCandidate, type Dialogue, type Extraction, type SoundDocument, type SoundState } from '../../api/modules/sound';
import { generations } from '../../api/modules/generations';
import { ConfigSelect } from '../generations/ConfigSelect';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import type { NavigationBarrier } from './writing-navigation';
import { useEpisodeCreation } from './EpisodeCreationWorkspace';
import { CreationRecoveryError, creationScope, readRecovery, readRecoveryText, saveRecovery, clearRecovery, isStoredDraft, type StoredDraft } from './creation-recovery';
import { useAuth } from '../auth/AuthSession';
import { ResourceEditingSession } from './resource-editing-session';
import { retainStandardSession, releaseStandardSession } from './standard-session-registry';
import '../../app/sound.css';

const newLine = (): Dialogue => ({ id: crypto.randomUUID(), character: '旁白', text: '', voice: '', config_id: null, start_ms: 0, media_id: null, adopted_hash: null });
function SoundHelp({ title, label = '声音编辑说明' }: { title: ReactNode; label?: string }) {
  return <Tooltip title={title} trigger={['hover', 'focus']}><Button className="sound-help" type="text" size="small" icon={<CircleHelp size={15}/>} aria-label={label}/></Tooltip>;
}
function SoundVolume({ label, value, disabled, onChange }: { label: string; value: number; disabled: boolean; onChange: (value: number) => void }) {
  return <div className="sound-volume"><span>{label}</span><Slider aria-label={`${label}滑条`} disabled={disabled} min={0} max={2} step={0.05} value={value} onChange={onChange}/><InputNumber aria-label={label} disabled={disabled} min={0} max={2} step={0.05} value={value} onChange={next => onChange(next ?? 0)}/></div>;
}
export function SoundPanel({ projectId, disabled, readOnly = disabled, flushVideo, onSaved, registerBarrier, renderToolbar, panelContainer, onOpenChange }: {
  projectId: string; disabled: boolean; readOnly?: boolean; flushVideo: () => Promise<boolean>; onSaved: () => Promise<unknown>; registerBarrier: (barrier: NavigationBarrier | null) => void;
  renderToolbar?: (trigger: ReactNode) => ReactNode;
  panelContainer?: HTMLElement | null; onOpenChange?: (open: boolean) => void;
}) {
  const inline = useEpisodeCreation();
  const panelId = useId();
  const srtUpload = useRef<HTMLInputElement>(null);
  const musicUpload = useRef<HTMLInputElement>(null);
  const railSurface = useRef<HTMLElement>(null);
  const drawerSurface = useRef<HTMLDivElement>(null);
  const api = useMemo(() => soundApi(projectId), [projectId]);
  const [enabled, setEnabled] = useState(false), [open, setOpen] = useState(false);
  useLayoutEffect(() => { onOpenChange?.(inline && open); return () => onOpenChange?.(false); }, [inline, open, onOpenChange]);
  const [capabilityStatus, setCapabilityStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [capabilityError, setCapabilityError] = useState('');
  const [audioError, setAudioError] = useState('');
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous', sessionKey = `sound:${projectId}`;
  const recoveryScope = useMemo(() => creationScope(`sound:${projectId}:draft`, account), [account, projectId]);
  type SoundEdit = { document: SoundDocument; reviewed: boolean; timeline_hash?: string };
  const session = useMemo(() => retainStandardSession(account, sessionKey, () => new ResourceEditingSession<SoundState, SoundEdit>({
    read: () => api.get(),
    save: async (base, value, assertActive) => {
      const body = { row_version: base.row_version, timeline_hash: base.timeline_hash, document: value.document, reviewed: value.reviewed && value.timeline_hash === base.timeline_hash };
      const request_id = await requestAttempt(`${sessionKey}:save`, body, attemptStorage());
      assertActive();
      const saved = await api.save({ ...body, request_id }); assertActive(); clearAttempt(`${sessionKey}:save`, attemptStorage()); return saved;
    },
    version: value => value.row_version, document: value => ({ document: value.document, reviewed: !value.needs_review, timeline_hash: value.timeline_hash }),
    apply: (value, edit) => ({ ...value, document: edit.document, needs_review: !edit.reviewed || edit.timeline_hash !== value.timeline_hash }),
    recovery: {
      read: () => {
        const stored = readRecovery(recoveryScope, (value): value is StoredDraft<SoundEdit> => isStoredDraft(value) && !!value.document && typeof value.document === 'object' && !!(value.document as SoundEdit).document && Array.isArray((value.document as SoundEdit).document.dialogue) && Array.isArray((value.document as SoundEdit).document.subtitles) && typeof (value.document as SoundEdit).reviewed === 'boolean');
        return stored ? { ...stored, document: { document: stored.document.document, reviewed: stored.document.reviewed, timeline_hash: stored.document.timeline_hash } } : null;
      },
      save: value => saveRecovery(recoveryScope, value), clear: () => clearRecovery(recoveryScope), export: () => readRecoveryText(recoveryScope), previous: () => readRecoveryText(`${recoveryScope}:previous`), backup: raw => localStorage.setItem(`${recoveryScope}:previous`, raw),
    },
  }), { label: '声音与字幕', projectId }), [account, sessionKey, api, recoveryScope, projectId]);
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const state = snapshot.value, doc = state?.document ?? null, reviewed = !!state && !state.needs_review;
  const recoveredDraft = snapshot.recovered;
  const recoveryError = snapshot.recoveryBlocked ? snapshot.message : '';
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
  const [selected, setSelected] = useState('');
  const [searchParams] = useSearchParams();
  const requestedLine = searchParams.get('source_line');
  const locatedSource = useRef('');
  function setReviewed(value: boolean) { const current = session.getSnapshot().value; if (current) session.edit({ document: current.document, reviewed: value, timeline_hash: current.timeline_hash }); }
  function setDoc(value: SoundDocument) { const current = session.getSnapshot().value; if (current) session.edit({ document: value, reviewed: false, timeline_hash: current.timeline_hash }); }
  function setState(value: SoundState | ((previous: SoundState | null) => SoundState | null)) {
    session.updateMetadata(current => {
      const next = typeof value === 'function' ? value(current) : value;
      return next ? { ...current, media: next.media, uploads: next.uploads, voice_defaults: next.voice_defaults } : current;
    });
  }
  useEffect(() => () => releaseStandardSession(account, sessionKey), [account, sessionKey]);
  const [candidates, setCandidates] = useState<AudioCandidate[]>([]), [pollVersion, setPollVersion] = useState(0);
  const [textConfig, setTextConfig] = useState<string>(), [extractId, setExtractId] = useState(''), [extraction, setExtraction] = useState<Extraction | null>(null);
  const [voiceCharacter, setVoiceCharacter] = useState(''), [voiceName, setVoiceName] = useState('');
  const lock = useRef(false), mounted = useRef(true);
  const entry = useRef<HTMLButtonElement | null>(null);
  const restoreFocus = useRef(false);
  useLayoutEffect(() => {
    if (inline && !open && restoreFocus.current) {
      const frame = requestAnimationFrame(() => {
        entry.current?.focus();
        restoreFocus.current = false;
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [inline, open]);
  useLayoutEffect(() => {
    if (!inline || !open || !panelContainer) return;
    const rail = railSurface.current;
    const controls = () => [...rail?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]):not([type=hidden]), textarea:not([disabled]), [tabindex="0"]') ?? []].filter(node => node.getClientRects().length > 0);
    const handle = (event: KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector('dialog[open], .ant-select-open, .ant-dropdown:not(.ant-dropdown-hidden), .ant-popover:not(.ant-popover-hidden)')) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); void close(); }
    };
    const focus = requestAnimationFrame(() => (controls()[0] ?? rail)?.focus());
    rail?.addEventListener('keydown', handle);
    return () => { cancelAnimationFrame(focus); rail?.removeEventListener('keydown', handle); };
  }, [inline, open, panelContainer]);
  const dirty = snapshot.dirty;
  const live = useRef({ dirty, busy, save: async () => false });
  const scope = `sound:${projectId}`;
  const line = doc?.dialogue.find(l => l.id === selected);
  const capabilityEpoch = useRef(0);
  const loadCapability = useCallback(async () => {
    const epoch = ++capabilityEpoch.current;
    setCapabilityStatus('loading'); setCapabilityError('');
    try { const next = await api.enabled(); if (mounted.current && epoch === capabilityEpoch.current) { setEnabled(next); setCapabilityStatus('ready'); } }
    catch (cause) { if (mounted.current && epoch === capabilityEpoch.current) { setCapabilityError(errorMessage(cause)); setCapabilityStatus('error'); } }
  }, [api]);
  useEffect(() => { mounted.current = true; void loadCapability(); return () => { mounted.current = false; capabilityEpoch.current++; }; }, [loadCapability]);
  useEffect(() => { registerBarrier({ hasUnsettled: () => session.hasUnsettled() || live.current.busy, flush: () => live.current.save() }); return () => registerBarrier(null); }, [registerBarrier, session]);
  useEffect(() => {
    if (!open || !selected) return;
    const controller = new AbortController();
    const poll = async () => { try { const next = await api.candidates(selected, controller.signal); if (!controller.signal.aborted) setCandidates(next); } catch (e) { if (!controller.signal.aborted) setNotice(errorMessage(e)); } };
    setCandidates([]); void poll(); const timer = setInterval(() => void poll(), 2500);
    return () => { controller.abort(); clearInterval(timer); };
  }, [api, open, selected, pollVersion]);
  useEffect(() => {
    if (!open || !extractId) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { try { const result = await api.extraction(extractId, controller.signal); if (controller.signal.aborted) return; setExtraction(result); if (['queued', 'running'].includes(result.status)) timer = setTimeout(() => void poll(), 2500); } catch (e) { if (!controller.signal.aborted) setNotice(errorMessage(e)); } };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [api, open, extractId]);
  function replace(next: SoundState) { session.accept(next); }
  function edit(next: SoundDocument) { if (disabled || lock.current) return; setDoc(next); }
  function replaceDocument(next: SoundDocument) {
    const current = session.getSnapshot().value;
    if (!current || readOnly) return;
    saveRecovery(`${recoveryScope}:previous`, { format: 1, base_version: current.row_version, document: { document: current.document, reviewed: !current.needs_review, timeline_hash: current.timeline_hash }, previous_recovery: readRecoveryText(recoveryScope) });
    setDoc(next);
  }
  function editLine(change: Partial<Dialogue>) { if (doc && line) edit({ ...doc, dialogue: doc.dialogue.map(l => l.id === line.id ? { ...l, ...change } : l) }); }
  async function action(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setNotice('');
    try { await work(); } catch (e) { if (mounted.current) setNotice(e instanceof CreationRecoveryError ? e.message : errorMessage(e)); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  }
  async function saveDocument(markReviewed = reviewed): Promise<SoundState | null> {
    if (!state || !doc || readOnly || !await flushVideo()) return null;
    setReviewed(markReviewed);
    if (!await (session.getSnapshot().status === 'error' ? session.retry() : session.flush())) { setNotice(session.getSnapshot().message); return null; }
    await onSaved(); return session.getSnapshot().value;
  }
  live.current = { dirty: session.hasUnsettled(), busy, save: async () => !readOnly && await session.flush() };
  async function begin() { await action(async () => {
    if (!await flushVideo()) return;
    setOpen(true);
    await session.load();
    try { setExtractId(sessionStorage.getItem(`${scope}:extraction`) ?? ''); } catch { /* history remains in task center */ }
  }); }
  useEffect(() => {
    if (!requestedLine || !/^[a-zA-Z0-9_-]{1,64}$/.test(requestedLine) || !enabled
      || locatedSource.current === `${projectId}:${requestedLine}`) return;
    locatedSource.current = `${projectId}:${requestedLine}`;
    void begin().then(() => {
      const current = session.getSnapshot().value;
      if (!mounted.current || !current) return;
      if (current.document.dialogue.some(item => item.id === requestedLine)) setSelected(requestedLine);
      else setNotice('来源台词已删除或不再可用，请核对声音草稿。');
    });
  }, [projectId, requestedLine, enabled, session]);
  async function close() {
    try {
      if (!readOnly && !await session.flush()) { setNotice(session.getSnapshot().message); return; }
      await onSaved(); restoreFocus.current = true; setOpen(false);
    } catch (cause) { setNotice(errorMessage(cause)); }
  }
  async function restoreSound() { await session.reload(true); }
  function backup() { const url = URL.createObjectURL(new Blob([JSON.stringify({ ...state, document: doc }, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'sound-draft.json'; a.click(); URL.revokeObjectURL(url); }
  function backupRecovery() {
    try {
      const content = session.exportRecovered(); if (!content) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = 'sound-recovery.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : '无法下载声音恢复记录。'); }
  }
  async function refreshAudio() {
    const fresh = await api.get();
    if (!mounted.current) return;
    setState(previous => previous ? { ...previous,
      media: { ...previous.media, ...fresh.media },
      uploads: previous.uploads.map(item => { const updated = fresh.uploads.find(next => next.media_id === item.media_id); return updated ? { ...item, url: updated.url } : item; }),
    } : previous);
    setAudioError(''); setPollVersion(value => value + 1);
  }
  async function generate() {
    if (!line || !line.voice.trim() || !line.text.trim()) { setNotice('请填写台词和音色。'); return; }
    if (candidates.length && !await confirmAction('将按当前台词新建配音任务，可能产生费用。已有候选仍会保留，是否继续？', { className: inline ? 'standard-creation-dialog' : undefined })) return;
    await action(async () => {
      const saved = dirty || state?.row_version === '0' || !state ? await saveDocument(false) : state;
      if (!saved) return;
      const body = { ...(line.config_id ? { config_id: line.config_id } : {}), source: { scene: 'dialogue_audio' as const, project_id: projectId, row_version: String(saved.row_version), line_id: line.id } };
      await generations.generateAudio(body, await requestAttempt(`${scope}:${line.id}:speech`, body, attemptStorage()));
      clearAttempt(`${scope}:${line.id}:speech`, attemptStorage()); setPollVersion(n => n + 1);
    });
  }
  const active = candidates.some(c => ['queued', 'running'].includes(c.status) || c.can_resume || ['acceptance_unknown', 'provider_acceptance_unknown', 'provider_unknown', 'message_delivery_unknown'].includes(c.error?.code ?? ''));
  if (capabilityStatus === 'loading') { const trigger = <AssemblyAction label="声音、字幕和配乐" icon={<Volume2 size={16}/>} disabled loading/>; return renderToolbar ? renderToolbar(trigger) : trigger; }
  if (capabilityStatus === 'error') { const trigger = <AssemblyAction label="声音、字幕和配乐" icon={<Volume2 size={16}/>} onClick={() => void loadCapability()}/>; return <>{renderToolbar ? renderToolbar(trigger) : trigger}<StudioFeedback type="warning" noticeKey={`${scope}:capability`} message="声音功能读取失败" description={capabilityError} actions={<Button onClick={() => void loadCapability()}>重新读取声音功能</Button>}/></>; }
  if (!enabled) return renderToolbar?.(null) ?? null;
  const title = state?.mode === 'native' ? '原声、字幕和配乐' : '配音、字幕和配乐';
  const actions = <div className={`sound-actions${inline ? ' sound-save-actions' : ''}`}>
    {inline ? <Dropdown trigger={['click']} overlayClassName="standard-creation-menu standard-creation-popup" menu={{ items: [
      { key: 'download', label: '下载草稿', onClick: backup },
      ...(session.hasPrevious() ? [{ key: 'backup', label: '下载本机备份', onClick: backupRecovery }] : []),
    ] }}><Button type="text" className="assembly-icon-action" icon={<Ellipsis size={16}/>} aria-label="声音草稿操作" title="声音草稿操作"/></Dropdown> : <><AssemblyAction label="下载草稿" icon={<Download size={16}/>} onClick={backup}/>{session.hasPrevious() && <Button type="text" onClick={backupRecovery}>下载本机备份</Button>}</>}
    <Tooltip title="声音或剪辑变化后，请重新核对时间，再勾选确认。" trigger={['hover', 'focus']}><Checkbox aria-label="已核对当前剪辑的声音与字幕时间" disabled={disabled || busy} checked={reviewed} onChange={e => setReviewed(e.target.checked)}>时间已核对</Checkbox></Tooltip><AssemblyAction type="primary" label="保存声音草稿" icon={<Save size={16}/>} aria-busy={busy} disabled={disabled || !doc} loading={busy} onClick={() => void action(async () => { await saveDocument(); })}/>
  </div>;
  const content = <>
      {!inline && <SoundHelp title="时间均为成片中的绝对毫秒。修改视频后需重新核对；保存后通过“合成预览”检查混音与字幕。"/>}
      {audioError && <StudioFeedback type="warning" noticeKey={`${scope}:audio`} message={audioError} actions={<Button loading={busy} disabled={busy} onClick={() => void action(refreshAudio)}>刷新试听地址</Button>}/>}
      {recoveryError && <StudioFeedback type="warning" noticeKey={`${scope}:recovery`} message={recoveryError} actions={<div className="sound-actions"><Button onClick={backupRecovery}>下载完整恢复稿</Button><Button disabled={busy} onClick={() => void action(async () => { await session.reload(false); })}>使用服务端版本</Button></div>}/>}
      {recoveredDraft && <StudioFeedback type="warning" noticeKey={`${scope}:conflict`} message="声音存在不同版本，本机输入已保留" actions={<div className="sound-actions"><Button disabled={busy || readOnly} onClick={() => void action(restoreSound)}>以当前版本保留本机修改</Button><Button disabled={busy} onClick={() => void action(async () => { await session.reload(false); })}>使用服务端版本</Button></div>}/>}
      {(notice || snapshot.message) && !recoveryError && !recoveredDraft && <StudioFeedback type="error" noticeKey={`${scope}:editing`} message={notice || snapshot.message} actions={<><Button disabled={busy} onClick={() => void session.retry()}>重试保存</Button><Button disabled={busy} onClick={() => void action(async () => { await session.reload(false); })}>使用服务端版本</Button></>}/>}
      {state?.needs_review && <StudioFeedback type="warning" noticeKey={`${scope}:review`} quiet persistent message="声音或剪辑有变化，导出前请核对时间并勾选确认。"/>}
      {state?.mode === 'native' && doc && <section className="native-voice" aria-label="原生视频后期声音">
        <SoundHelp label="原声与配乐压低说明" title="对白来自视频原声，不叠加独立配音。字幕时间需逐句校对，视频内对白和环境音不能分别调节。压低区间内配乐降为设定音量的四分之一，仅在启用“对白期间自动降低配乐音量”时生效。"/>
        <AssemblyAction label="从采用视频的台词创建字幕草稿" icon={<Captions size={16}/>} disabled={busy || disabled} onClick={() => void action(async () => { const draft = await api.nativeSubtitles(); if (draft.timeline_hash !== state.timeline_hash) throw new Error('剪辑已变化，请重新载入声音面板。'); if (mounted.current) replaceDocument({ ...doc, subtitles: draft.subtitles }); })}/>
        <AssemblyAction label="用已校对字幕设置对白压低区间" icon={<Volume2 size={16}/>} disabled={busy || disabled || !doc.subtitles.length} onClick={() => edit({ ...doc, native_ducking: doc.subtitles.map(s => ({ ...s })) })}/>
        {(doc.native_ducking ?? []).map((range, i) => <div className="sound-subtitle" key={i}><label>对白开始毫秒<InputNumber min={0} max={3600000} precision={0} disabled={busy || disabled} value={range.start_ms} onChange={v => edit({ ...doc, native_ducking: doc.native_ducking!.map((r, n) => n === i ? { ...r, start_ms: v ?? 0 } : r) })}/></label><label>对白结束毫秒<InputNumber min={1} max={3600000} precision={0} disabled={busy || disabled} value={range.end_ms} onChange={v => edit({ ...doc, native_ducking: doc.native_ducking!.map((r, n) => n === i ? { ...r, end_ms: v ?? 1 } : r) })}/></label><span>{range.text}</span><Button disabled={busy || disabled} onClick={() => edit({ ...doc, native_ducking: doc.native_ducking!.filter((_, n) => n !== i) })}>移除区间</Button></div>)}
      </section>}
      {!doc || !state ? <Spin/> : <fieldset disabled={disabled || busy} className="sound-fieldset"><Tabs items={[
        { key: 'dialogue', label: `配音 · ${doc.dialogue.length}`, children: <>
          <div className="sound-actions"><ConfigSelect kind="text" label="台词提取模型" optionLayout={inline ? 'compact' : undefined} presentation={inline ? 'standard-creation' : undefined} value={textConfig} onChange={setTextConfig} disabled={busy || disabled}/><Button className="assembly-icon-action" icon={<Captions size={16}/>} aria-label="从已保存分镜提取台词" title="从已保存分镜提取台词" loading={busy || !!extraction && ['queued', 'running'].includes(extraction.status)} disabled={busy || disabled || !!extraction && ['queued', 'running'].includes(extraction.status)} onClick={() => void action(async () => {
            const body = { ...(textConfig ? { config_id: textConfig } : {}), source: { scene: 'dialogue_extract' as const, project_id: projectId }, parameters: {} };
            const receipt = await generations.generateText(body, await requestAttempt(`${scope}:extract`, body, attemptStorage()));
            clearAttempt(`${scope}:extract`, attemptStorage()); setExtractId(receipt.generation_id); setExtraction(null); try { sessionStorage.setItem(`${scope}:extraction`, receipt.generation_id); } catch { /* receipt is available in task center */ }
          })}/><AssemblyAction label="添加台词" icon={<Plus size={16}/>} disabled={busy || disabled || doc.dialogue.length >= 300} onClick={() => { const l = newLine(); edit({ ...doc, dialogue: [...doc.dialogue, l] }); setSelected(l.id); }}/></div>
          {extraction && <><StudioFeedback type={extraction.error ? 'warning' : 'info'} noticeKey={`${scope}:extraction:${extractId}`} quiet={!extraction.error} persistent message={extraction.error || `台词提取：${extraction.status}`} description="提取文本需人工校对，建议时间不代表准确对齐。"/>{(extraction.raw_text || extraction.dialogue) && <div className="sound-extraction"><pre>{extraction.raw_text}</pre>{extraction.dialogue && <Button disabled={busy || disabled} onClick={() => void action(async () => { replaceDocument({ ...doc, dialogue: extraction.dialogue!.map(l => ({ ...l, voice: state.voice_defaults.voices[l.character] ?? '' })) }); setSelected(extraction.dialogue![0]?.id ?? ''); setExtraction(null); })}>放入编辑区校对</Button>}</div>}</>}
          <div className="sound-editor"><nav aria-label="台词列表">{doc.dialogue.map((l, i) => <button type="button" key={l.id} aria-pressed={selected === l.id} onClick={() => setSelected(l.id)}><strong>{i + 1}. {l.character}</strong><span>{l.text || '待填写台词'}</span><small>{l.media_id ? state.stale_lines.includes(l.id) ? '配音已过期' : '已采用配音' : '待配音'}</small></button>)}</nav>
          {line ? <section className="sound-fields">
            <label>角色<Input value={line.character} maxLength={100} onChange={e => editLine({ character: e.target.value })}/></label>
            <label>台词<Input.TextArea value={line.text} rows={4} maxLength={4096} onChange={e => editLine({ text: e.target.value })}/></label>
            <label>配音模型<ConfigSelect kind="audio" optionLayout={inline ? 'compact' : undefined} presentation={inline ? 'standard-creation' : undefined} value={line.config_id ?? undefined} disabled={busy || disabled} autoDefault={false} onChange={v => editLine({ config_id: v ?? null })}/></label>
            <label>音色标识<Input value={line.voice} maxLength={128} placeholder="填写配音服务提供的音色标识" onChange={e => editLine({ voice: e.target.value })}/></label>
            <Button disabled={busy || disabled || !state.voice_defaults.voices[line.character]} onClick={() => editLine({ voice: state.voice_defaults.voices[line.character] })}>使用角色默认音色</Button>
            <label>开始时间（秒）<InputNumber min={0} max={3600} precision={3} step={0.001} value={line.start_ms / 1000} onChange={v => editLine({ start_ms: Math.round((v ?? 0) * 1000) })}/></label>
            {line.media_id && state.media[line.media_id] && <><audio controls preload="metadata" onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')} src={state.media[line.media_id].url}/><p>实际时长 {state.media[line.media_id].duration_ms} 毫秒</p><Button onClick={() => editLine({ media_id: null, adopted_hash: null })}>取消采用</Button></>}
            <div className="sound-actions"><AssemblyAction type="primary" label="生成此条配音" icon={<Mic size={16}/>} loading={busy || active} disabled={busy || disabled || active || !line.text.trim() || !line.voice.trim()} onClick={() => void generate()}/><AssemblyAction label="删除台词" icon={<Trash2 size={16}/>} danger disabled={busy || disabled} onClick={() => { edit({ ...doc, dialogue: doc.dialogue.filter(l => l.id !== line.id) }); setSelected(''); }}/></div>
            <h3>配音候选</h3>{!candidates.length && <p>还没有配音结果</p>}{candidates.map((c, i) => <article key={c.id} className="sound-candidate"><p title={`任务 ${c.id}`}>配音 {i + 1}<span>{({ queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[c.status] ?? c.status}</span></p>{(c.error || c.can_resume) && <StudioFeedback type={c.error ? 'error' : 'info'} noticeKey={`${scope}:candidate:${c.id}`} quiet={!c.error} persistent message={c.error?.message || '配音结果可恢复保存'} description={`配音任务 ${c.id}`} actions={c.can_resume && <Button disabled={busy || disabled} onClick={() => void action(async () => { await generations.resume(c.id); setPollVersion(n => n + 1); })}>恢复保存已有结果</Button>}/>}{c.outputs.map(o => <div className="sound-candidate-output" key={o.media_id}><audio controls preload="metadata" onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')} src={o.url}/><span>{(o.duration_ms / 1000).toFixed(2)} 秒</span><AssemblyAction label="采用此配音" icon={<Check size={16}/>} disabled={busy || disabled || dirty || o.media_id === line.media_id} onClick={() => void action(async () => { replace(await api.adopt(state.row_version, line.id, o.media_id)); await onSaved(); })}/></div>)}</article>)}
          </section> : <p>选择或添加一条台词，校对后生成配音。</p>}</div>
          <details><Tooltip title="默认音色只在手动应用时填入台词，不会覆盖已有配音。" trigger={['hover', 'focus']}><summary>项目角色默认音色</summary></Tooltip><div className="sound-actions"><Input aria-label="默认音色角色" placeholder="角色名" value={voiceCharacter} onChange={e => setVoiceCharacter(e.target.value)}/><Input aria-label="默认音色标识" placeholder="音色标识" value={voiceName} onChange={e => setVoiceName(e.target.value)}/><Button disabled={busy || disabled || !voiceCharacter.trim() || !voiceName.trim()} onClick={() => void action(async () => { const voices = await api.voices({ row_version: state.voice_defaults.row_version, voices: { ...state.voice_defaults.voices, [voiceCharacter.trim()]: voiceName.trim() } }); setState({ ...state, voice_defaults: voices }); })}>保存默认音色</Button></div>{Object.entries(state.voice_defaults.voices).map(([name, voice]) => <p key={name}>{name}：{voice}</p>)}</details>
        </> },
        { key: 'subtitles', label: `字幕 · ${doc.subtitles.length}`, children: <>
          <div className="sound-actions">
            <AssemblyAction label="添加字幕" icon={<Plus size={16}/>} disabled={busy || disabled || doc.subtitles.length >= 2000} onClick={() => edit({ ...doc, subtitles: [...doc.subtitles, { start_ms: doc.subtitles.at(-1)?.end_ms ?? 0, end_ms: (doc.subtitles.at(-1)?.end_ms ?? 0) + 1000, text: '' }] })}/>
            <label className="sound-file" hidden={inline}>导入 UTF-8 SRT<input ref={srtUpload} type="file" accept=".srt" aria-label="导入 UTF-8 SRT" disabled={busy || disabled} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; if (file.size > 2 * 1024**2) { setNotice('SRT 文件不能超过 2 MiB。'); return; } void action(async () => { const bytes = await file.arrayBuffer(); const subtitles = await api.importSrt(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); setDoc({ ...doc, subtitles }); setReviewed(false); }); }}/></label>
            {inline ? <Dropdown trigger={['click']} overlayClassName="standard-creation-menu standard-creation-popup" menu={{ items: [
              { key: 'import', label: '导入 UTF-8 SRT', disabled: busy || disabled, onClick: () => srtUpload.current?.click() },
              { key: 'export', label: <a href={api.srtUrl} download>导出已保存 SRT</a> },
              ...(state.mode !== 'native' ? [{ key: 'fill', label: '从采用的配音填入字幕', disabled: busy || disabled || !doc.dialogue.length, onClick: () => void action(async () => { replaceDocument({ ...doc, subtitles: doc.dialogue.filter(l => l.media_id && state.media[l.media_id]).map(l => ({ start_ms: l.start_ms, end_ms: l.start_ms + state.media[l.media_id!].duration_ms, text: l.text })).sort((a, b) => a.start_ms - b.start_ms) }); }) }] : []),
            ] }}><Button type="text" className="assembly-icon-action" icon={<Ellipsis size={16}/>} aria-label="字幕操作" title="字幕操作"/></Dropdown> : <><a href={api.srtUrl} download>导出已保存 SRT</a>{state.mode !== 'native' && <Button disabled={busy || disabled || !doc.dialogue.length} onClick={() => void action(async () => { replaceDocument({ ...doc, subtitles: doc.dialogue.filter(l => l.media_id && state.media[l.media_id]).map(l => ({ start_ms: l.start_ms, end_ms: l.start_ms + state.media[l.media_id!].duration_ms, text: l.text })).sort((a, b) => a.start_ms - b.start_ms) }); })}>从采用的配音填入字幕</Button>}</>}
          </div>
          <Checkbox checked={doc.burn_subtitles} disabled={busy || disabled} onChange={e => edit({ ...doc, burn_subtitles: e.target.checked })}>导出时烧录字幕</Checkbox><SoundHelp label="字幕时间说明" title="字幕按开始时间排列且不能重叠。配音可能有停顿，请人工调整每条字幕的起止时间。"/><label>字幕字号<InputNumber min={12} max={72} precision={0} value={doc.font_size} onChange={v => edit({ ...doc, font_size: v ?? 24 })}/></label>
          <div className="sound-subtitle-list">{doc.subtitles.map((s, i) => <div className="sound-subtitle" key={i}><span className="sound-subtitle-number">{i + 1}</span><label>开始（秒）<InputNumber min={0} max={3600} precision={3} step={0.001} value={s.start_ms / 1000} onChange={v => edit({ ...doc, subtitles: doc.subtitles.map((x, j) => j === i ? { ...x, start_ms: Math.round((v ?? 0) * 1000) } : x) })}/></label><label>结束（秒）<InputNumber min={0.001} max={3600} precision={3} step={0.001} value={s.end_ms / 1000} onChange={v => edit({ ...doc, subtitles: doc.subtitles.map((x, j) => j === i ? { ...x, end_ms: Math.round((v ?? 0.001) * 1000) } : x) })}/></label><label className="sound-subtitle-text"><span className="sound-field-label">字幕文本</span><Input.TextArea aria-label="字幕文本" autoSize={{ minRows: 1, maxRows: 4 }} value={s.text} maxLength={1000} onChange={e => edit({ ...doc, subtitles: doc.subtitles.map((x, j) => j === i ? { ...x, text: e.target.value } : x) })}/></label><AssemblyAction label={`删除字幕 ${i + 1}`} icon={<Trash2 size={15}/>} danger disabled={busy || disabled} onClick={() => edit({ ...doc, subtitles: doc.subtitles.filter((_, j) => i !== j) })}/></div>)}</div>
        </> },
        { key: 'music', label: '配乐与混音', children: <div className="sound-fields">
          {!!state.uploads?.length && <label>选择本集已上传配乐<Select classNames={inline ? { popup: { root: 'standard-creation-popup' } } : undefined} aria-label="选择本集已上传配乐" disabled={busy || disabled} value={doc.music?.media_id} options={state.uploads.map(m => ({ value: m.media_id, label: m.name }))} onChange={id => { const m = state.uploads.find(v => v.media_id === id)!; edit({ ...doc, music: { media_id: id, start_ms: 0, trim_in_ms: 0, trim_out_ms: m.duration_ms, volume: 0.2, loop: false, fade_in_ms: 500, fade_out_ms: 500, ducking: true } }); setState({ ...state, media: { ...state.media, [id]: m } }); }}/></label>}
          <div className="sound-music-upload"><AssemblyAction label={doc.music ? '替换配乐' : '上传一条配乐'} icon={<Upload size={16}/>} disabled={busy || disabled} loading={busy} onClick={() => musicUpload.current?.click()}/><span>{state.uploads.find(m => m.media_id === doc.music?.media_id)?.name ?? (doc.music ? '本集配乐' : '添加配乐')}</span><SoundHelp label="配乐文件要求" title="支持 MP3 / WAV / M4A，最多 100 MiB / 60 分钟。"/>{doc.music && <AssemblyAction label="移除配乐" icon={<Trash2 size={16}/>} disabled={busy || disabled} onClick={() => edit({ ...doc, music: null })}/>}</div>
          <input ref={musicUpload} type="file" hidden accept=".mp3,.wav,.m4a" disabled={busy || disabled} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; if (file.size > 100 * 1024**2) { setNotice('配乐文件不能超过 100 MiB。'); return; } void action(async () => { const music = await api.upload(file); setDoc({ ...doc, music: { media_id: music.media_id, start_ms: 0, trim_in_ms: 0, trim_out_ms: music.duration_ms, volume: 0.2, loop: false, fade_in_ms: 500, fade_out_ms: 500, ducking: true } }); setState({ ...state, media: { ...state.media, [music.media_id]: music } }); setReviewed(false); }); }}/>
          {doc.music && <><audio controls preload="metadata" onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')} src={state.media[doc.music.media_id]?.url}/><SoundVolume label="配乐音量" disabled={busy || disabled} value={doc.music.volume} onChange={value => edit({ ...doc, music: { ...doc.music!, volume: value } })}/><Checkbox checked={doc.music.loop} disabled={busy || disabled} onChange={e => edit({ ...doc, music: { ...doc.music!, loop: e.target.checked } })}>循环配乐</Checkbox><Checkbox checked={doc.music.ducking} disabled={busy || disabled} onChange={e => edit({ ...doc, music: { ...doc.music!, ducking: e.target.checked } })}>对白期间自动降低配乐音量</Checkbox><details className="sound-music-advanced"><summary>时间与淡入淡出</summary><div className="sound-grid">{(['start_ms', 'trim_in_ms', 'trim_out_ms', 'fade_in_ms', 'fade_out_ms'] as const).map(k => <label key={k}>{{ start_ms: '开始（秒）', trim_in_ms: '裁剪起点（秒）', trim_out_ms: '裁剪终点（秒）', fade_in_ms: '淡入（秒）', fade_out_ms: '淡出（秒）' }[k]}<InputNumber min={0} max={3600} precision={3} step={0.001} value={doc.music![k] / 1000} onChange={v => edit({ ...doc, music: { ...doc.music!, [k]: Math.round((v ?? 0) * 1000) } })}/></label>)}</div></details></>}
          <SoundVolume label="原视频声音音量" disabled={busy || disabled} value={doc.original_volume} onChange={value => edit({ ...doc, original_volume: value })}/>{state.mode !== 'native' && <SoundVolume label="配音音量" disabled={busy || disabled} value={doc.dialogue_volume} onChange={value => edit({ ...doc, dialogue_volume: value })}/>}
        </div> },
      ].filter(item => (state.mode !== 'native' || item.key !== 'dialogue') && (doc.schema_version !== 2 || item.key === 'dialogue'))}/></fieldset>}
    </>;
  const trigger = <Tooltip title={inline && open ? '收起声音编辑' : '声音、字幕和配乐'} trigger={['hover', 'focus']}><Button ref={entry} type="text" className="assembly-icon-action" icon={<Volume2 size={16}/>} aria-label={inline && open ? '收起声音编辑' : '声音、字幕和配乐'} disabled={inline && open ? false : disabled} loading={inline && open ? false : busy} aria-expanded={inline ? open : undefined} aria-controls={inline ? panelId : undefined} onClick={() => { if (inline && open) void close(); else void begin(); }}/></Tooltip>;
  const rail = open && <>
    <section ref={railSurface} id={panelId} className={`sound-inline${panelContainer ? ' sound-rail' : ''}`} aria-label={title} tabIndex={-1}>
      <StudioMessageProvider local getContainer={() => railSurface.current ?? document.body}>
      <ConfigProvider getPopupContainer={() => railSurface.current ?? document.body}>
      <header><h3>{title}</h3><div className="sound-header-actions"><SoundHelp title="修改剪辑后需重新核对声音与字幕时间；合成预览可检查实际混音与字幕。"/><StudioFeedbackIndicator/><AssemblyAction label="收起声音编辑" icon={<X size={16}/>} onClick={() => void close()}/></div></header>
      <div className="sound-rail-content">{content}</div>{actions}
      </ConfigProvider>
      </StudioMessageProvider>
    </section>
  </>;
  return <>
    {renderToolbar ? renderToolbar(trigger) : trigger}
    {!open && notice && <StudioFeedback type="error" noticeKey={`${scope}:closed`} message={notice}/>}
    {inline ? panelContainer ? createPortal(rail, panelContainer) : rail : <StudioMessageProvider local getContainer={() => drawerSurface.current ?? document.body}><Drawer open={open} width={960} title={title} extra={<StudioFeedbackIndicator/>} onClose={close} maskClosable destroyOnHidden rootClassName="sound-drawer" footer={actions}><div className="sound-drawer-body" ref={drawerSurface}>{content}</div></Drawer></StudioMessageProvider>}
  </>;
}

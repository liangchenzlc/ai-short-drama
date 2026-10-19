import { confirmAction } from '../../components/ui/confirm';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Checkbox, Drawer, Input, InputNumber, Select, Spin, Tabs } from 'antd';
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
export function SoundPanel({ projectId, episodeId, disabled, readOnly = disabled, flushVideo, onSaved, registerBarrier }: {
  projectId: string; episodeId: string; disabled: boolean; readOnly?: boolean; flushVideo: () => Promise<boolean>; onSaved: () => Promise<unknown>; registerBarrier: (barrier: NavigationBarrier | null) => void;
}) {
  const inline = useEpisodeCreation();
  const api = useMemo(() => soundApi(projectId, episodeId), [projectId, episodeId]);
  const [enabled, setEnabled] = useState(false), [open, setOpen] = useState(false);
  const [capabilityStatus, setCapabilityStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [capabilityError, setCapabilityError] = useState('');
  const [audioError, setAudioError] = useState('');
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous', sessionKey = `sound:${projectId}:${episodeId}`;
  const recoveryScope = useMemo(() => creationScope(`sound:${projectId}:${episodeId}:draft`, account), [account, projectId, episodeId]);
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
  }), { label: '声音与字幕', projectId, episodeId }), [account, sessionKey, api, recoveryScope, projectId, episodeId]);
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const state = snapshot.value, doc = state?.document ?? null, reviewed = !!state && !state.needs_review;
  const recoveredDraft = snapshot.recovered;
  const recoveryError = snapshot.recoveryBlocked ? snapshot.message : '';
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
  const [selected, setSelected] = useState('');
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
      entry.current?.focus();
      restoreFocus.current = false;
    }
  }, [inline, open]);
  const dirty = snapshot.dirty;
  const live = useRef({ dirty, busy, save: async () => false });
  const scope = `sound:${projectId}:${episodeId}`;
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
    setOpen(true);
    await session.load();
    try { setExtractId(sessionStorage.getItem(`${scope}:extraction`) ?? ''); } catch { /* history remains in task center */ }
  }); }
  async function close() {
    restoreFocus.current = true; setOpen(false); void session.flush();
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
    if (candidates.length && !await confirmAction('将按当前台词新建配音任务，可能产生费用。已有候选仍会保留，是否继续？')) return;
    await action(async () => {
      const saved = dirty || state?.row_version === '0' || !state ? await saveDocument(false) : state;
      if (!saved) return;
      const body = { ...(line.config_id ? { config_id: line.config_id } : {}), source: { scene: 'dialogue_audio' as const, project_id: projectId, episode_id: episodeId, row_version: String(saved.row_version), line_id: line.id } };
      await generations.generateAudio(body, await requestAttempt(`${scope}:${line.id}:speech`, body, attemptStorage()));
      clearAttempt(`${scope}:${line.id}:speech`, attemptStorage()); setPollVersion(n => n + 1);
    });
  }
  const active = candidates.some(c => ['queued', 'running'].includes(c.status) || c.can_resume || ['acceptance_unknown', 'provider_acceptance_unknown', 'provider_unknown', 'message_delivery_unknown'].includes(c.error?.code ?? ''));
  if (capabilityStatus === 'loading') return <span role="status"><Spin size="small"/> 正在读取声音功能…</span>;
  if (capabilityStatus === 'error') return <Alert type="warning" showIcon message="声音功能读取失败" description={capabilityError} action={<Button onClick={() => void loadCapability()}>重新读取声音功能</Button>}/>;
  if (!enabled) return null;
  const title = state?.mode === 'native' ? '原声、字幕和配乐' : '配音、字幕和配乐';
  const actions = <div className="sound-actions"><Button onClick={backup}>下载草稿</Button>{session.hasPrevious() && <Button type="text" onClick={backupRecovery}>下载本机备份</Button>}<Checkbox disabled={disabled || busy} checked={reviewed} onChange={e => setReviewed(e.target.checked)}>已核对当前剪辑的声音与字幕时间</Checkbox><Button type="primary" disabled={disabled || !doc} loading={busy} onClick={() => void action(async () => { await saveDocument(); })}>保存声音草稿</Button></div>;
  const content = <>
      {audioError && <Alert type="warning" showIcon message={audioError} action={<Button loading={busy} disabled={busy} onClick={() => void action(refreshAudio)}>刷新试听地址</Button>}/>}
      {recoveryError && <Alert type="warning" showIcon message={recoveryError} action={<div className="sound-actions"><Button onClick={backupRecovery}>下载完整恢复稿</Button><Button disabled={busy} onClick={() => void action(async () => { await session.reload(false); })}>使用服务端版本</Button></div>}/>}
      {recoveredDraft && <Alert type="warning" showIcon message="声音存在不同版本，本机输入已保留" action={<div className="sound-actions"><Button disabled={busy || readOnly} onClick={() => void action(restoreSound)}>以当前版本保留本机修改</Button><Button disabled={busy} onClick={() => void action(async () => { await session.reload(false); })}>使用服务端版本</Button></div>}/>}
      <p>时间均为成片中的绝对毫秒。修改视频后需重新核对；保存后通过“合成预览”检查混音与字幕。</p>
      {(notice || snapshot.message) && !recoveryError && !recoveredDraft && <Alert type="error" showIcon message={notice || snapshot.message} action={<><Button disabled={busy} onClick={() => void session.retry()}>重试保存</Button><Button disabled={busy} onClick={() => void action(async () => { await session.reload(false); })}>使用服务端版本</Button></>}/>}
      {state?.needs_review && <Alert type="warning" showIcon message="声音或剪辑有变化，导出前请核对时间并勾选确认。"/>}
      {state?.mode === 'native' && doc && <section className="native-voice" aria-label="原生视频后期声音">
        <p>对白来自视频原声，不叠加独立配音。字幕时间需逐句校对，视频内对白和环境音不能分别调节。</p>
        <Button disabled={busy || disabled} onClick={() => void action(async () => { const draft = await api.nativeSubtitles(); if (draft.timeline_hash !== state.timeline_hash) throw new Error('剪辑已变化，请重新载入声音面板。'); if (mounted.current) replaceDocument({ ...doc, subtitles: draft.subtitles }); })}>从采用视频的台词创建字幕草稿</Button>
        <Button disabled={busy || disabled || !doc.subtitles.length} onClick={() => edit({ ...doc, native_ducking: doc.subtitles.map(s => ({ ...s })) })}>用已校对字幕设置对白压低区间</Button>
        <p>区间内配乐降为设定音量的四分之一；仅在启用“对白期间自动降低配乐音量”时生效。</p>
        {(doc.native_ducking ?? []).map((range, i) => <div className="sound-subtitle" key={i}><label>对白开始毫秒<InputNumber min={0} max={3600000} precision={0} disabled={busy || disabled} value={range.start_ms} onChange={v => edit({ ...doc, native_ducking: doc.native_ducking!.map((r, n) => n === i ? { ...r, start_ms: v ?? 0 } : r) })}/></label><label>对白结束毫秒<InputNumber min={1} max={3600000} precision={0} disabled={busy || disabled} value={range.end_ms} onChange={v => edit({ ...doc, native_ducking: doc.native_ducking!.map((r, n) => n === i ? { ...r, end_ms: v ?? 1 } : r) })}/></label><span>{range.text}</span><Button disabled={busy || disabled} onClick={() => edit({ ...doc, native_ducking: doc.native_ducking!.filter((_, n) => n !== i) })}>移除区间</Button></div>)}
      </section>}
      {!doc || !state ? <Spin/> : <fieldset disabled={disabled || busy} className="sound-fieldset"><Tabs items={[
        { key: 'dialogue', label: `配音 · ${doc.dialogue.length}`, children: <>
          <div className="sound-actions"><ConfigSelect kind="text" label="台词提取模型" value={textConfig} onChange={setTextConfig} disabled={busy || disabled}/><Button disabled={busy || disabled || !!extraction && ['queued', 'running'].includes(extraction.status)} onClick={() => void action(async () => {
            const body = { ...(textConfig ? { config_id: textConfig } : {}), source: { scene: 'dialogue_extract' as const, project_id: projectId, episode_id: episodeId }, parameters: {} };
            const receipt = await generations.generateText(body, await requestAttempt(`${scope}:extract`, body, attemptStorage()));
            clearAttempt(`${scope}:extract`, attemptStorage()); setExtractId(receipt.generation_id); setExtraction(null); try { sessionStorage.setItem(`${scope}:extraction`, receipt.generation_id); } catch { /* receipt is available in task center */ }
          })}>从已保存分镜提取台词</Button><Button disabled={busy || disabled || doc.dialogue.length >= 300} onClick={async () => { const l = newLine(); edit({ ...doc, dialogue: [...doc.dialogue, l] }); setSelected(l.id); }}>添加台词</Button></div>
          {extraction && <div className="sound-extraction"><p>提取状态：{extraction.status}。提取文本需人工校对，建议时间不代表准确对齐。</p>{extraction.error && <Alert type="warning" message={extraction.error}/>}<pre>{extraction.raw_text}</pre>{extraction.dialogue && <Button disabled={busy || disabled} onClick={() => void action(async () => { replaceDocument({ ...doc, dialogue: extraction.dialogue!.map(l => ({ ...l, voice: state.voice_defaults.voices[l.character] ?? '' })) }); setSelected(extraction.dialogue![0]?.id ?? ''); setExtraction(null); })}>放入编辑区校对</Button>}</div>}
          <div className="sound-editor"><nav aria-label="台词列表">{doc.dialogue.map((l, i) => <button type="button" key={l.id} aria-pressed={selected === l.id} onClick={() => setSelected(l.id)}><strong>{i + 1}. {l.character}</strong><span>{l.text || '待填写台词'}</span><small>{l.media_id ? state.stale_lines.includes(l.id) ? '配音已过期' : '已采用配音' : '待配音'}</small></button>)}</nav>
          {line ? <section className="sound-fields">
            <label>角色<Input value={line.character} maxLength={100} onChange={e => editLine({ character: e.target.value })}/></label>
            <label>台词<Input.TextArea value={line.text} rows={4} maxLength={4096} onChange={e => editLine({ text: e.target.value })}/></label>
            <label>配音模型<ConfigSelect kind="audio" value={line.config_id ?? undefined} disabled={busy || disabled} autoDefault={false} onChange={v => editLine({ config_id: v ?? null })}/></label>
            <label>音色标识<Input value={line.voice} maxLength={128} placeholder="填写配音服务提供的音色标识" onChange={e => editLine({ voice: e.target.value })}/></label>
            <Button disabled={busy || disabled || !state.voice_defaults.voices[line.character]} onClick={() => editLine({ voice: state.voice_defaults.voices[line.character] })}>使用角色默认音色</Button>
            <label>开始时间（毫秒）<InputNumber min={0} max={3600000} precision={0} value={line.start_ms} onChange={v => editLine({ start_ms: v ?? 0 })}/></label>
            {line.media_id && state.media[line.media_id] && <><audio controls preload="metadata" onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')} src={state.media[line.media_id].url}/><p>实际时长 {state.media[line.media_id].duration_ms} 毫秒</p><Button onClick={() => editLine({ media_id: null, adopted_hash: null })}>取消采用</Button></>}
            <div className="sound-actions"><Button type="primary" disabled={busy || disabled || active || !line.text.trim() || !line.voice.trim()} onClick={() => void generate()}>生成此条配音</Button><Button danger disabled={busy || disabled} onClick={() => { edit({ ...doc, dialogue: doc.dialogue.filter(l => l.id !== line.id) }); setSelected(''); }}>删除台词</Button></div>
            <h3>配音候选</h3>{!candidates.length && <p>生成结果将在这里保留，试听后手动采用。</p>}{candidates.map(c => <article key={c.id} className="sound-candidate"><p>任务 {c.id} · {c.status}</p>{c.error && <p role="alert">{c.error.message}</p>}{c.can_resume && <Button disabled={busy || disabled} onClick={() => void action(async () => { await generations.resume(c.id); setPollVersion(n => n + 1); })}>恢复保存已有结果</Button>}{c.outputs.map(o => <div key={o.media_id}><audio controls preload="metadata" onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')} src={o.url}/><span>{o.duration_ms} 毫秒</span><Button disabled={busy || disabled || dirty || o.media_id === line.media_id} onClick={() => void action(async () => { replace(await api.adopt(state.row_version, line.id, o.media_id)); await onSaved(); })}>采用此配音</Button></div>)}</article>)}
          </section> : <p>选择或添加一条台词，校对后生成配音。</p>}</div>
          <details><summary>项目角色默认音色</summary><p>默认音色只在手动应用时填入台词，不会覆盖已有配音。</p><div className="sound-actions"><Input aria-label="默认音色角色" placeholder="角色名" value={voiceCharacter} onChange={e => setVoiceCharacter(e.target.value)}/><Input aria-label="默认音色标识" placeholder="音色标识" value={voiceName} onChange={e => setVoiceName(e.target.value)}/><Button disabled={busy || disabled || !voiceCharacter.trim() || !voiceName.trim()} onClick={() => void action(async () => { const voices = await api.voices({ row_version: state.voice_defaults.row_version, voices: { ...state.voice_defaults.voices, [voiceCharacter.trim()]: voiceName.trim() } }); setState({ ...state, voice_defaults: voices }); })}>保存默认音色</Button></div>{Object.entries(state.voice_defaults.voices).map(([name, voice]) => <p key={name}>{name}：{voice}</p>)}</details>
        </> },
        { key: 'subtitles', label: `字幕 · ${doc.subtitles.length}`, children: <>
          <div className="sound-actions"><label className="sound-file">导入 UTF-8 SRT<input type="file" accept=".srt" disabled={busy || disabled} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; if (file.size > 2 * 1024**2) { setNotice('SRT 文件不能超过 2 MiB。'); return; } void action(async () => { const bytes = await file.arrayBuffer(); const subtitles = await api.importSrt(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); setDoc({ ...doc, subtitles }); setReviewed(false); }); }}/></label><a href={api.srtUrl} download>导出已保存 SRT</a><Button disabled={busy || disabled || doc.subtitles.length >= 2000} onClick={async () => edit({ ...doc, subtitles: [...doc.subtitles, { start_ms: doc.subtitles.at(-1)?.end_ms ?? 0, end_ms: (doc.subtitles.at(-1)?.end_ms ?? 0) + 1000, text: '' }] })}>添加字幕</Button>{state.mode !== 'native' && <Button disabled={busy || disabled || !doc.dialogue.length} onClick={() => void action(async () => { replaceDocument({ ...doc, subtitles: doc.dialogue.filter(l => l.media_id && state.media[l.media_id]).map(l => ({ start_ms: l.start_ms, end_ms: l.start_ms + state.media[l.media_id!].duration_ms, text: l.text })).sort((a, b) => a.start_ms - b.start_ms) }); })}>从采用的配音填入字幕</Button>}</div>
          <Checkbox checked={doc.burn_subtitles} disabled={busy || disabled} onChange={e => edit({ ...doc, burn_subtitles: e.target.checked })}>导出时烧录字幕</Checkbox><label>字幕字号<InputNumber min={12} max={72} precision={0} value={doc.font_size} onChange={v => edit({ ...doc, font_size: v ?? 24 })}/></label>
          <p>字幕按开始时间排列且不能重叠。配音可能有停顿，请人工调整每条字幕的起止时间。</p>
          {doc.subtitles.map((s, i) => <div className="sound-subtitle" key={i}><label>开始毫秒<InputNumber min={0} max={3600000} precision={0} value={s.start_ms} onChange={v => edit({ ...doc, subtitles: doc.subtitles.map((x, j) => j === i ? { ...x, start_ms: v ?? 0 } : x) })}/></label><label>结束毫秒<InputNumber min={1} max={3600000} precision={0} value={s.end_ms} onChange={v => edit({ ...doc, subtitles: doc.subtitles.map((x, j) => j === i ? { ...x, end_ms: v ?? 1 } : x) })}/></label><label>字幕文本<Input.TextArea value={s.text} maxLength={1000} onChange={e => edit({ ...doc, subtitles: doc.subtitles.map((x, j) => j === i ? { ...x, text: e.target.value } : x) })}/></label><Button danger disabled={busy || disabled} onClick={() => edit({ ...doc, subtitles: doc.subtitles.filter((_, j) => i !== j) })}>删除</Button></div>)}
        </> },
        { key: 'music', label: '配乐与混音', children: <div className="sound-fields">
          {!!state.uploads?.length && <label>选择本集已上传配乐<Select aria-label="选择本集已上传配乐" disabled={busy || disabled} value={doc.music?.media_id} options={state.uploads.map(m => ({ value: m.media_id, label: m.name }))} onChange={id => { const m = state.uploads.find(v => v.media_id === id)!; edit({ ...doc, music: { media_id: id, start_ms: 0, trim_in_ms: 0, trim_out_ms: m.duration_ms, volume: 0.2, loop: false, fade_in_ms: 500, fade_out_ms: 500, ducking: true } }); setState({ ...state, media: { ...state.media, [id]: m } }); }}/></label>}
          <label>上传一条配乐（MP3 / WAV / M4A，最多 100 MiB / 60 分钟）<input type="file" accept=".mp3,.wav,.m4a" disabled={busy || disabled} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; if (file.size > 100 * 1024**2) { setNotice('配乐文件不能超过 100 MiB。'); return; } void action(async () => { const music = await api.upload(file); setDoc({ ...doc, music: { media_id: music.media_id, start_ms: 0, trim_in_ms: 0, trim_out_ms: music.duration_ms, volume: 0.2, loop: false, fade_in_ms: 500, fade_out_ms: 500, ducking: true } }); setState({ ...state, media: { ...state.media, [music.media_id]: music } }); setReviewed(false); }); }}/></label>
          {doc.music && <><audio controls preload="metadata" onError={() => setAudioError('试听地址无法播放，请刷新地址后重试，当前编辑已保留。')} src={state.media[doc.music.media_id]?.url}/><div className="sound-grid">{(['start_ms', 'trim_in_ms', 'trim_out_ms', 'fade_in_ms', 'fade_out_ms'] as const).map(k => <label key={k}>{{ start_ms: '开始毫秒', trim_in_ms: '裁剪起点毫秒', trim_out_ms: '裁剪终点毫秒', fade_in_ms: '淡入毫秒', fade_out_ms: '淡出毫秒' }[k]}<InputNumber min={0} max={3600000} precision={0} value={doc.music![k]} onChange={v => edit({ ...doc, music: { ...doc.music!, [k]: v ?? 0 } })}/></label>)}</div><label>配乐音量<InputNumber min={0} max={2} step={0.05} value={doc.music.volume} onChange={v => edit({ ...doc, music: { ...doc.music!, volume: v ?? 0 } })}/></label><Checkbox checked={doc.music.loop} disabled={busy || disabled} onChange={e => edit({ ...doc, music: { ...doc.music!, loop: e.target.checked } })}>循环裁剪后的配乐，直到成片结束</Checkbox><Checkbox checked={doc.music.ducking} disabled={busy || disabled} onChange={e => edit({ ...doc, music: { ...doc.music!, ducking: e.target.checked } })}>对白期间自动降低配乐音量</Checkbox><Button disabled={busy || disabled} onClick={() => edit({ ...doc, music: null })}>移除配乐</Button></>}
          <label>原视频声音音量<InputNumber min={0} max={2} step={0.05} value={doc.original_volume} onChange={v => edit({ ...doc, original_volume: v ?? 0 })}/></label>{state.mode !== 'native' && <label>配音音量<InputNumber min={0} max={2} step={0.05} value={doc.dialogue_volume} onChange={v => edit({ ...doc, dialogue_volume: v ?? 0 })}/></label>}<p>0 为静音，1 为原始音量。片段原有静音设置仍然生效。</p>
        </div> },
      ].filter(item => state.mode !== 'native' || item.key !== 'dialogue')}/></fieldset>}
    </>;
  return <>
    {(!inline || !open) && <Button ref={entry} disabled={disabled} loading={busy} onClick={() => void begin()}>声音、字幕和配乐</Button>}
    {!open && notice && <Alert type="error" showIcon message={notice}/>}
    {inline ? open && <section className="sound-inline" aria-label={title}>
      <header><h3>{title}</h3><Button onClick={() => void close()}>收起声音编辑</Button></header>
      {content}{actions}
    </section> : <Drawer open={open} width={960} title={title} onClose={close} maskClosable destroyOnHidden rootClassName="sound-drawer" footer={actions}>{content}</Drawer>}
  </>;
}

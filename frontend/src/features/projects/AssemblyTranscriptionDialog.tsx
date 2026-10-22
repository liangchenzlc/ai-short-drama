import { useEffect, useRef, useState } from 'react';
import { Button, Select, Segmented } from 'antd';
import { AudioLines, Check, Download, RefreshCw, X } from 'lucide-react';
import type { MediaJob } from '../../api/modules/assembly';
import { ApiError, errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { clearRecovery, creationScope, readRecovery, saveRecovery, readRecoveryText } from './creation-recovery';
import { anchorAt, normalizeSound } from './assembly-document';
import { downloadText, exportSrt, validateSubtitles } from './assembly-subtitles';
import { AssemblyAction } from './AssemblyAction';
import { assemblyDraft } from './assembly-recovery';
import type { useAssembly } from './useAssembly';

type Attempt = { key: string; body: { row_version: string; clip_id?: string; language: string; model_config_id?: string } };
function validAttempt(value: unknown): value is Attempt {
  if (!value || typeof value !== 'object') return false;
  const v = value as Attempt; return typeof v.key === 'string' && !!v.body && typeof v.body.row_version === 'string' && typeof v.body.language === 'string';
}
export function AssemblyTranscriptionDialog({ editor, clipId, disabled, onClose }: { editor: ReturnType<typeof useAssembly>; clipId?: string; disabled: boolean; onClose: () => void }) {
  const scope = creationScope(`${editor.api.mediaScope}:recognition`);
  const [models, setModels] = useState<{ local_available: boolean; models: { id: string; name: string }[] } | null>(null), [model, setModel] = useState('local'), [language, setLanguage] = useState('');
  const [range, setRange] = useState(clipId ? 'clip' : 'episode'), [job, setJob] = useState<MediaJob | null>(null), [jobs, setJobs] = useState<MediaJob[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [pending, setPending] = useState<Attempt | null>(null), [damaged, setDamaged] = useState(false), [epoch, setEpoch] = useState(0);
  const mutation = useRef(false), live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    try { setPending(readRecovery(scope, validAttempt)); } catch (cause) { setDamaged(true); setError(cause instanceof Error ? cause.message : errorMessage(cause)); }
    void Promise.all([editor.api.transcriptionModels(), editor.api.mediaJobs()]).then(([config, history]) => {
      if (cancelled) return; setModels(config); const records = history.filter(j => j.kind === 'transcription'); setJobs(records); setJob(current => records.find(j => j.id === current?.id) ?? current ?? records[0] ?? null);
    }).catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : errorMessage(cause)); });
    return () => { cancelled = true; };
  }, [editor.api, scope, epoch]);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const next = await editor.api.mediaJob(job.id, controller.signal); if (!controller.signal.aborted) { setJob(next); setJobs(current => current.map(j => j.id === next.id ? next : j)); if (['queued', 'running'].includes(next.status)) timer = setTimeout(() => { void poll(); }, 2500); } }
      catch (cause) { if (!controller.signal.aborted) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); timer = setTimeout(() => { void poll(); }, 5000); } }
    };
    timer = setTimeout(() => { void poll(); }, 1500); return () => { controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status, editor.api]);
  async function start(recover = false) {
    if (mutation.current || disabled) return; mutation.current = true; setBusy(true); setError('');
    try {
      let attempt = recover ? pending : null;
      if (!attempt) {
        if (!await editor.flush()) throw new Error('请先处理保存问题，再识别字幕');
        await editor.load();
        attempt = { key: crypto.randomUUID(), body: { row_version: editor.latest()!.assembly!.row_version, ...(range === 'clip' && clipId ? { clip_id: clipId } : {}), language, ...(model === 'local' ? {} : { model_config_id: model }) } };
        saveRecovery(scope, attempt); setPending(attempt);
      }
      try { const result = await editor.api.transcribe(attempt.body, attempt.key); clearRecovery(scope); if (live.current) { setPending(null); setJob(result); setJobs(current => [result, ...current.filter(j => j.id !== result.id)]); } }
      catch (cause) {
        if (cause instanceof ApiError && cause.status !== undefined && cause.status >= 400 && cause.status < 500 && cause.status !== 408 && cause.status !== 429) { clearRecovery(scope); setPending(null); }
        throw cause;
      }
    } catch (cause) { if (live.current) setError(cause instanceof Error ? cause.message : errorMessage(cause)); } finally { mutation.current = false; if (live.current) setBusy(false); }
  }
  async function apply() {
    if (!job?.result?.subtitles || disabled || mutation.current) return;
    mutation.current = true; setBusy(true); setError('');
    try {
      if (!await editor.flush()) throw new Error('请先处理保存问题');
      const fresh = await editor.load();
      if (fresh.voice_context_hash !== job.result.voice_context_hash) throw new Error('画面或人声音轨已变化。原字幕和识别结果均已保留，请按当前来源重新识别。');
      const start = job.result.start_ms!, end = job.result.end_ms!, doc = normalizeSound(fresh.sound!.document, fresh.clips ?? [], fresh.sound!.media);
      const original = JSON.stringify(assemblyDraft(editor.latest()!).document);
      const removed = doc.subtitles.filter(s => s.start_ms < end && s.end_ms > start);
      if (removed.length && !await confirmAction(`将替换此范围内的 ${removed.length} 条字幕，范围外字幕保留。可撤销本次替换。`, { className: 'standard-creation-dialog' })) return;
      if (JSON.stringify(assemblyDraft(editor.latest()!).document) !== original) throw new Error('编辑内容已变化，识别结果已保留，请重新应用');
      if (doc.tracks.some(t => t.id === 'subtitles' && t.locked)) throw new Error('请先解锁字幕轨');
      const captions = validateSubtitles([...doc.subtitles.filter(s => s.end_ms <= start || s.start_ms >= end), ...job.result.subtitles.map(s => ({ ...s, ...anchorAt(fresh.clips ?? [], s.start_ms) }))]);
      editor.edit(state => ({ ...state, sound: { ...state.sound!, document: { ...doc, burn_subtitles: true, subtitles: captions } } })); onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); } finally { mutation.current = false; setBusy(false); }
  }
  const active = !!job && ['queued', 'running'].includes(job.status);
  const unresolved = jobs.find(j => ['failed', 'cancelled'].includes(j.status) && j.result?.submitted && !j.result?.reconciled);
  return <Dialog title="语音识别字幕" className="standard-creation-dialog assembly-transcription-dialog" onClose={onClose} canClose={!busy}>
    <div className="assembly-recognition-config">
      <Segmented aria-label="字幕识别范围" value={range} options={[{ label: '当前片段', value: 'clip', disabled: !clipId }, { label: '整集', value: 'episode' }]} onChange={setRange}/>
      <Select aria-label="语音识别模型" value={model} loading={!models} disabled={busy || active || !!pending} onChange={setModel} options={[{ value: 'local', label: `本地 Whisper${models && !models.local_available ? '（未配置）' : ''}` }, ...(models?.models ?? []).map(m => ({ value: m.id, label: m.name }))]}/>
      <Select aria-label="识别语言" value={language} onChange={setLanguage} options={[{ value: '', label: '自动检测语言' }, { value: 'zh', label: '中文' }, { value: 'en', label: '英文' }, { value: 'ja', label: '日文' }]}/>
      <Button icon={<AudioLines size={15}/>} loading={busy || active} disabled={disabled || !!pending || damaged || !models || model === 'local' && !models.local_available || model !== 'local' && !!unresolved} onClick={() => { void start(); }}>{active ? `识别中 ${job.progress}%` : '开始识别'}</Button>
      {active && <AssemblyAction label="取消识别" icon={<X size={15}/>} onClick={() => { void editor.api.cancelMediaJob(job.id).then(setJob).catch(cause => setError(cause instanceof Error ? cause.message : errorMessage(cause))); }}/>}
    </div>
    {pending && <div className="assembly-task-recovery"><span>识别提交结果待核实，原请求已保留。</span><Button loading={busy} onClick={() => { void start(true); }}>核实原请求</Button></div>}
    {damaged && <div className="assembly-task-recovery"><span>恢复记录损坏，请保留原文并核对任务。</span><AssemblyAction label="下载原请求记录" icon={<Download size={15}/>} onClick={() => downloadText(readRecoveryText(scope) ?? '', 'recognition-recovery.json')}/><Button disabled={busy} onClick={() => { void (async () => { if (await confirmAction('请先下载原记录，并在本人识别记录及云端渠道核对任务。确认清理损坏的本机记录？', { className: 'standard-creation-dialog' })) { clearRecovery(scope); setDamaged(false); setPending(null); setError(''); } })(); }}>已核实，清理记录</Button></div>}
    {unresolved && <div className="assembly-task-recovery"><span>云端受理结果待核实，原任务与输入已保留。</span><Button disabled={busy} onClick={() => { void (async () => {
      if (!await confirmAction('请先在云端渠道核实原任务。确认已核实并允许主动创建新的识别请求？新请求可能产生费用。', { className: 'standard-creation-dialog' })) return;
      try { const next = await editor.api.acknowledgeMediaJob(unresolved.id); setJobs(current => current.map(j => j.id === next.id ? next : j)); if (job?.id === next.id) setJob(next); } catch (cause) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); }
    })(); }}>已核实原任务</Button></div>}
    {(error || job?.error) && <StudioFeedback type="error" noticeKey="assembly-recognition" message={error || job?.error?.message} persistent/>}
    <div className="assembly-recognition-history"><Select aria-label="本人识别记录" placeholder="本人识别记录" value={job?.id} options={jobs.map(j => ({ value: j.id, label: `${j.status === 'succeeded' ? '已完成' : j.status === 'failed' ? '失败' : j.status === 'cancelled' ? '已取消' : '处理中'} · ${j.id.slice(-6)}` }))} onChange={id => { void editor.api.mediaJob(id).then(setJob).catch(cause => setError(cause instanceof Error ? cause.message : errorMessage(cause))); }}/><AssemblyAction label="刷新识别记录" icon={<RefreshCw size={15}/>} onClick={() => setEpoch(v => v + 1)}/></div>
    <div className="assembly-transcript-results">{job?.result?.subtitles?.length ? job.result.subtitles.map(s => <div key={s.id}><small>{(s.start_ms / 1000).toFixed(2)} — {(s.end_ms / 1000).toFixed(2)}</small><p>{s.text}</p></div>) : <div className="assembly-material-empty">{active ? '识别任务在后台继续，完成后在此校对' : '识别后预览结果，再应用到字幕轨'}</div>}</div>
    <footer><AssemblyAction label="导出识别结果 SRT" icon={<Download size={15}/>} disabled={!job?.result?.subtitles?.length} onClick={() => downloadText(exportSrt(job!.result!.subtitles!), 'recognition.srt')}/><Button onClick={onClose}>关闭</Button><Button type="primary" icon={<Check size={15}/>} loading={busy} disabled={disabled || !job?.result?.subtitles?.length} onClick={() => { void apply(); }}>应用字幕</Button></footer>
  </Dialog>;
}

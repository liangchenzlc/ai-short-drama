import { confirmAction } from '../../components/ui/confirm';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Select, Spin } from 'antd';
import { errorMessage } from '../../api/http';
import { nativeVoiceApi as api, type CharacterVoice, type NativeDialogue, type SoundMode } from '../../api/modules/native-voice';
import { generations } from '../../api/modules/generations';
import { ConfigSelect } from '../generations/ConfigSelect';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import type { NavigationBarrier } from './writing-navigation';
import { Dialog } from '../../components/ui/Dialog';
import '../../app/native-voice.css';

function useNativeEnabled() {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => { let gone = false; void api.enabled().then(value => { if (!gone) setEnabled(value); }).catch(() => {}); return () => { gone = true; }; }, []);
  return enabled;
}

export function NativeSoundMode({ projectId, disabled, onChanged }: { projectId: string; disabled: boolean; onChanged: () => void }) {
  const enabled = useNativeEnabled();
  const [mode, setMode] = useState<SoundMode | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const lock = useRef(false);
  useEffect(() => { let gone = false; if (enabled) void api.mode(projectId).then(v => { if (!gone) setMode(v); }).catch(e => { if (!gone) setError(errorMessage(e)); }); return () => { gone = true; }; }, [projectId, enabled]);
  if (!enabled) return null;
  return <div className="native-mode"><span>声音制作：{mode?.mode === 'native' ? '角色音色驱动视频' : '历史后期配音'}</span>
    <Button disabled={disabled || !mode} loading={busy} onClick={async () => {
      if (!mode || lock.current || !await confirmAction('切换制作模式会影响后续生成和导出。历史配音保留，新模式不叠加历史配音；请先保存分镜并在成片中重新核对声音。')) return;
      lock.current = true; setBusy(true); setError('');
      void api.setMode(projectId, { ...mode, mode: mode.mode === 'native' ? 'legacy' : 'native' }).then(v => { setMode(v); onChanged(); }).catch(e => setError(errorMessage(e))).finally(() => { lock.current = false; setBusy(false); });
    }}>切换制作模式</Button>{error && <Alert type="error" message={error}/>}</div>;
}

export function CharacterVoicePanel({ projectId, characterId, disabled }: { projectId: string; characterId: string; disabled: boolean }) {
  const enabled = useNativeEnabled(), scope = `character-voice:${projectId}:${characterId}`;
  const [state, setState] = useState<CharacterVoice | null>(null), [description, setDescription] = useState('');
  const [preview, setPreview] = useState('清晨的风吹过窗前，今天又是新的开始，我们一起出发吧。');
  const [config, setConfig] = useState<string>(), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const lock = useRef(false), alive = useRef(true);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(async (signal?: AbortSignal) => { const result = await api.voices(projectId, characterId, signal); if (!signal?.aborted && alive.current) setState(result); return result; }, [projectId, characterId]);
  const pending = !!state?.candidates.some(c => ['queued', 'running'].includes(c.status));
  useEffect(() => {
    alive.current = true; if (!enabled) return;
    const controller = new AbortController();
    const timer = setTimeout(() => { void refresh(controller.signal).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); }).finally(() => { if (!controller.signal.aborted && pending) setReload(n => n + 1); }); }, pending ? 3000 : 0);
    return () => { alive.current = false; controller.abort(); clearTimeout(timer); };
  }, [enabled, refresh, pending, reload]);
  async function action(work: () => Promise<void>) { if (lock.current) return; lock.current = true; setBusy(true); setError(''); try { await work(); } catch (e) { if (alive.current) setError(errorMessage(e)); } finally { lock.current = false; if (alive.current) setBusy(false); } }
  const active = state?.candidates.some(c => ['queued', 'running'].includes(c.status) || c.can_resume || ['provider_acceptance_unknown', 'acceptance_unknown', 'message_delivery_unknown'].includes(c.error?.code ?? ''));
  if (!enabled) return null;
  return <section className="native-voice" aria-label="角色声音"><h3>角色声音</h3><Button disabled={busy || disabled} onClick={() => void action(async () => { await refresh(); })}>刷新声音候选</Button><p>按声音描述设计音色，试听采用后，所有相关分镜复用这份样音。样音内容不会作为分镜台词。</p>
    {error && <Alert type="error" showIcon message={error}/>}
    {state?.current_voice && <section aria-label="当前采用音色"><strong>当前角色音色</strong>
      {state.current_voice.url ? <audio aria-label="试听当前角色音色" controls preload="none" src={state.current_voice.url}/> : <p>音色地址暂不可用，请刷新后重试。</p>}
      {state.current_voice.duration_ms != null && <p>样音 {(state.current_voice.duration_ms / 1000).toFixed(2)} 秒</p>}
    </section>}
    <fieldset disabled={disabled || busy}><ConfigSelect kind="audio" label="百炼音色设计模型" value={config} onChange={setConfig} onResolvedChange={setConfig} disabled={disabled || busy}/>
      <label>声音描述<Input.TextArea aria-label="声音描述" maxLength={500} value={description} onChange={e => setDescription(e.target.value)} placeholder="例如：青年男性，中低音，略带沙哑，语速平稳"/></label>
      <label>试音文本<Input.TextArea aria-label="试音文本" maxLength={200} value={preview} onChange={e => setPreview(e.target.value)}/></label><p>15～200 字符；建议简短自然，生成后须为 3～7.5 秒才可用于视频参考。</p>
      <Button disabled={disabled || busy || active || !state || !config || !description.trim() || preview.trim().length < 15} loading={busy} onClick={() => void action(async () => {
        if (!config) return; const body = { config, description, preview }; const key = await requestAttempt(scope, body, attemptStorage());
        await api.design(projectId, characterId, config, description, preview, key); clearAttempt(scope, attemptStorage()); await refresh();
      })}>设计一个音色候选</Button>
    </fieldset>
    {!state ? <Spin/> : !state.candidates.length ? <p>还没有音色候选。填写描述，生成后试听选择。</p> : <ul className="native-candidates">{state.candidates.map(c => <li key={c.record_id}>
      <strong>{state.record_id === c.record_id ? '当前角色音色' : c.status === 'succeeded' ? '待试听候选' : ['queued', 'running'].includes(c.status) ? '正在设计与保存' : '任务未完成'}</strong><p>{c.description}</p>
      {c.url && <audio aria-label="试听角色音色" controls preload="none" src={c.url}/>}
      {c.duration_ms != null && <p>样音 {(c.duration_ms / 1000).toFixed(2)} 秒{!c.adoptable && '，不满足参考要求，请调整试音文本后重新设计。'}</p>}
      {c.error && <Alert type="warning" message={c.error.message}/>}
      <div className="native-actions">{c.adoptable && <Button disabled={disabled || busy || state.record_id === c.record_id} onClick={() => void action(async () => setState(await api.adopt(projectId, characterId, state.row_version, c.record_id)))}>设为角色音色</Button>}
      {c.can_resume && <Button disabled={disabled || busy} onClick={() => void action(async () => { await generations.resume(c.task_id); await refresh(); })}>恢复已有样音保存</Button>}
      {['queued', 'running'].includes(c.status) && <Button disabled={disabled || busy} onClick={() => void action(async () => { await generations.cancel(c.task_id); await refresh(); })}>停止等待</Button>}</div>
    </li>)}</ul>}
  </section>;
}

export function NativeDialoguePanel({ projectId, episodeId, shotId, disabled, prepare, onChanged, revision, registerBarrier }: { projectId: string; episodeId: string; shotId: string; disabled: boolean; prepare: () => Promise<unknown>; onChanged: () => void; revision?: number; registerBarrier?: (barrier: NavigationBarrier | null) => void }) {
  const enabled = useNativeEnabled();
  const [state, setState] = useState<NativeDialogue | null>(null), [doc, setDoc] = useState<NativeDialogue['document'] | null>(null);
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const lock = useRef(false), scope = `native-dialogue:${shotId}`;
  useEffect(() => { if (!enabled || open) return; const controller = new AbortController(); void api.dialogue(projectId, episodeId, shotId, controller.signal).then(v => { if (!controller.signal.aborted) { setState(v); setDoc(v.document); } }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); }); return () => controller.abort(); }, [enabled, projectId, episodeId, shotId, revision, open]);
  const dirty = JSON.stringify(doc) !== JSON.stringify(state?.document);
  const live = useRef({ dirty, busy }); live.current = { dirty: open && dirty, busy };
  useEffect(() => { registerBarrier?.({ hasUnsettled: () => live.current.dirty || live.current.busy, flush: async () => !live.current.dirty && !live.current.busy }); return () => registerBarrier?.(null); }, [registerBarrier]);
  if (!enabled || state?.mode === 'legacy') return null;
  const edit = (next: NativeDialogue['document']) => setDoc({ ...next, reviewed: false });
  async function save() {
    if (!state || !doc || lock.current || disabled) return; lock.current = true; setBusy(true); setError('');
    try { if (!await prepare()) return; const body = { row_version: state.row_version, document: doc }; const request_id = await requestAttempt(scope, body, attemptStorage());
      const next = await api.saveDialogue(projectId, episodeId, shotId, { ...body, request_id }); clearAttempt(scope, attemptStorage()); setState(next); setDoc(next.document); setOpen(false); onChanged();
    } catch (e) { setError(errorMessage(e)); } finally { lock.current = false; setBusy(false); }
  }
  return <section className="native-dialogue" aria-label="分镜原生对白"><h4>原生对白</h4>
    <p>{state?.document.reviewed ? `${state.document.lines.length} 句已确认对白` : '生成视频前，请确认角色、台词与音色；无对白镜头也需确认。'}</p>
    {state?.document.lines.map((line, i) => <p key={i}>{i + 1}. {state.characters.find(c => c.id === line.character_id)?.name ?? '角色已移除'}：{line.text}</p>)}
    {state?.voices.map(v => <div key={v.character_id}><span>{v.name} · {v.url ? `音色版本 ${v.version}` : '未绑定音色，请到角色库设计'}</span>{v.url && <audio aria-label={`${v.name}参考音色`} controls preload="none" src={v.url}/>}</div>)}
    <Button disabled={disabled || !state} onClick={() => { setDoc(state!.document); setOpen(true); }}>编辑并确认对白</Button>
    {error && !open && <Alert type="error" message={error}/>}
    {open && doc && state && <Dialog className="native-dialogue-dialog" title="分镜对白与声音表演" canClose={!busy} onClose={async () => { if (!dirty || await confirmAction('对白尚未保存，放弃本次修改？')) { setOpen(false); setDoc(state.document); } }}>
      <div className="native-voice"><p>最多两位角色按列表顺序轮流说话。角色外观沿用分镜参考图，声音使用角色库中已确认的样音。</p>{error && <Alert type="error" message={error}/>}
        <fieldset disabled={disabled || busy}>{doc.lines.map((line, i) => <div className="native-line" key={i}>
          <label>说话角色<Select aria-label={`第${i + 1}句角色`} value={line.character_id || undefined} options={state.characters.map(c => ({ value: c.id, label: c.name }))} onChange={v => edit({ ...doc, lines: doc.lines.map((l, n) => n === i ? { ...l, character_id: v } : l) })}/></label>
          <label>台词<Input.TextArea aria-label={`第${i + 1}句台词`} value={line.text} maxLength={500} onChange={e => edit({ ...doc, lines: doc.lines.map((l, n) => n === i ? { ...l, text: e.target.value } : l) })}/></label>
          <label>语气<Input value={line.delivery} maxLength={200} onChange={e => edit({ ...doc, lines: doc.lines.map((l, n) => n === i ? { ...l, delivery: e.target.value } : l) })}/></label>
          <label>说话方式<Select value={line.speech} options={[{ value: 'onscreen', label: '画内说话，同步口型' }, { value: 'voiceover', label: '画外音' }]} onChange={v => edit({ ...doc, lines: doc.lines.map((l, n) => n === i ? { ...l, speech: v } : l) })}/></label>
          <div className="native-actions"><Button disabled={i === 0} onClick={() => { const lines = [...doc.lines]; [lines[i - 1], lines[i]] = [lines[i], lines[i - 1]]; edit({ ...doc, lines }); }}>上移</Button><Button danger onClick={() => edit({ ...doc, lines: doc.lines.filter((_, n) => n !== i) })}>删除台词</Button></div>
        </div>)}<Button disabled={!state.characters.length || doc.lines.length >= 20} onClick={() => edit({ ...doc, lines: [...doc.lines, { character_id: state.characters[0].id, text: '', delivery: '', speech: 'onscreen' }] })}>添加台词</Button>
        {!state.characters.length && <p>当前分镜没有关联角色；请先关联素材，或确认这是无对白镜头。</p>}
        <p>台词需适合视频时长；内容过长可能说不完，请手动缩短台词或调整分镜。</p>
        <Checkbox checked={doc.reviewed} onChange={e => setDoc({ ...doc, reviewed: e.target.checked })}>已确认对白顺序与角色；空列表表示无对白</Checkbox>
        <div className="native-actions"><Button type="primary" loading={busy} onClick={() => void save()}>保存分镜对白</Button></div></fieldset>
      </div>
    </Dialog>}
  </section>;
}

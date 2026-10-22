import { useEffect, useRef, useState } from 'react';
import { Button, Skeleton, Tag } from 'antd';
import { ThoughtChain } from '@ant-design/x';
import { BookOpen, Check, CircleAlert, Wrench } from 'lucide-react';
import type { AssistantOperation, AssistantTask } from '../../api/types/assistant';
import { assistantApi } from '../../api/modules/assistant';
import { errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { nativeVoiceApi, type VoiceCandidate } from '../../api/modules/native-voice';

const labels: Record<string, string> = {
  describe_creation_target: '核对写入目标', read_selected_subjects: '读取已选主体',
  read_operation_receipt: '核对操作回执', search_skills: '检索创作方法', load_skill: '加载创作方法', read_skill_resource: '读取方法资料',
  apply_standard_patch: '保存标准创作内容', apply_canvas_patch: '保存画布节点与连线', read_task_results: '读取本任务成果',
  read_image_group: '读取图片组目录', read_image_content: '读取指定图片', set_image_group_primary: '设置图片组主图',
  get_generation_policy: '核对媒体生成设置',
  submit_generation_batch: '生成并回填媒体', get_generation_status: '核对媒体批次',
};
const phases: Record<string, string> = {
  tool_running: '执行中', waiting_media: '生成中', settling_result: '回填中', reconciling: '正在核实结果', terminal: '已结束',
};
const internalTools = new Set(['search_memories', 'read_memories', 'memory_catalog', 'search_memory', 'read_memory', 'read_conversation_history', 'remember_memory', 'update_memory', 'forget_memory', 'restore_memory']);
const resultLabel = (ref: Record<string, unknown>) => typeof ref.name === 'string' && ref.name.trim() ? ref.name
  : ref.kind === 'asset' ? '查看素材' : ref.kind === 'shot' ? '查看分镜' : ref.kind === 'canvas_node' ? '定位节点' : '查看成果';
function resultHref(ref: Record<string, unknown>) {
  if (ref.status === 'deleted' || ref.status === 'source_unavailable') return null;
  if (typeof ref.project_id !== 'string' || !/^\d+$/.test(ref.project_id)) return null;
  if (ref.kind === 'sound_line' && typeof ref.id === 'string') return `/projects/${encodeURIComponent(ref.project_id)}/assembly?source_line=${encodeURIComponent(ref.id)}`;
  if ((ref.kind === 'asset' || ref.kind === 'shot') && typeof ref.id === 'string') {
    const query = new URLSearchParams({ [ref.kind]: ref.id });
    if (typeof ref.image_group_id === 'string') query.set('source_image_group', ref.image_group_id);
    if (typeof ref.image_slot_id === 'string') query.set('source_image_slot', ref.image_slot_id);
    if (ref.kind === 'shot' && ref.media_kind === 'video') query.set('source_media', 'video');
    return `/projects/${encodeURIComponent(ref.project_id)}/${ref.kind === 'asset' ? 'assets' : 'storyboard'}?${query}`;
  }
  if (ref.kind === 'canvas_node' && typeof ref.canvas_id === 'string' && typeof ref.id === 'string') return `/canvas-app/canvas/${encodeURIComponent(ref.canvas_id)}?node=${encodeURIComponent(ref.id)}`;
  return null;
}
const resultStates: Record<string, string> = { created: '已创建', updated: '已修改', deleted: '已删除', unchanged: '未变化', adopted: '已设为主图', bound: '已回填节点', source_unavailable: '来源不可用', adoption_conflict: '回填有冲突', generation_failed: '生成未全部完成' };
function VoicePreview({ result, onClose }: { result: Record<string, unknown>; onClose: () => void }) {
  const [sample, setSample] = useState<VoiceCandidate | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setSample(null);
    void nativeVoiceApi.voices(String(result.project_id), String(result.id), controller.signal).then(state => {
      if (controller.signal.aborted) return;
      const saved = state.candidates.find(candidate => candidate.task_id === result.generation_id && candidate.media_id === result.media_id);
      if (!saved) { setError('这段生成样音当前不可用，请核对原任务。'); return; }
      setSample(saved);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [result, revision]);
  return <Dialog title="试听生成音色" className="standard-creation-dialog standard-assistant-dialog" onClose={onClose}>
    <div className="agent-context-dialog-body">
      {loading ? <Skeleton active paragraph={{ rows: 3 }}/> : error ? <div role="alert"><p>{error}</p><Button onClick={() => setRevision(value => value + 1)}>重新读取样音</Button></div>
        : sample && <><p>{sample.description}</p><p>试音文本：{sample.preview_text}</p>
          {sample.url ? <audio aria-label="试听本次生成音色" controls preload="none" src={sample.url} style={{ width: '100%' }}/>
            : <div role="status"><p>样音地址暂不可用。</p><Button onClick={() => setRevision(value => value + 1)}>刷新样音地址</Button></div>}
          {sample.duration_ms != null && <p>样音 {(sample.duration_ms / 1000).toFixed(2)} 秒</p>}</>}
    </div>
    <footer className="ai-assistant-history-footer"><Button onClick={onClose}>关闭</Button></footer>
  </Dialog>;
}
function Results({ item }: { item: AssistantOperation }) {
  const [voice, setVoice] = useState<Record<string, unknown> | null>(null);
  return <div className="ai-assistant-result-list" aria-label={`批次 ${item.id} 的成果`}>
    {item.result_refs.map((original, index) => {
      const undoKind = original.media_role === 'character_voice' ? 'character_voice' : original.kind;
      const undo = item.undo_receipts?.find(receipt => receipt.selected_keys.includes(`${undoKind}:${original.id}`));
      const restored = undo?.result_refs.find(ref => ref.kind === original.kind && ref.id === original.id);
      const ref = restored ? { ...original, ...restored } : original;
      const href = resultHref(ref); const label = resultLabel(ref);
      if (ref.kind === 'skill' || ref.kind === 'skill_resource') return <article className="ai-assistant-result-card" aria-label={`创作方法：${label}`} key={`${ref.kind}:${ref.activation_id}:${ref.path ?? ''}`}>
        <strong>{label}</strong><span>{ref.source === 'manual' ? '手动指定' : '按需加载'} · 版本 {String(ref.content_version)}</span>
        {typeof ref.purpose === 'string' && <span>{ref.purpose}</span>}
        {typeof ref.path === 'string' && <span>{ref.path} · 从字符 {String(ref.offset)} 读取</span>}
      </article>;
      if (!['asset', 'shot', 'canvas_node', 'sound_line'].includes(String(ref.kind))) return href ? <a key={index} href={href}>{label}</a> : <span key={index}>{label}</span>;
      const kind = ref.kind === 'asset' ? '素材' : ref.kind === 'shot' ? '分镜' : ref.kind === 'sound_line' ? ref.media_role === 'dialogue_audio' ? '配音' : '台词' : '节点';
      return <article className="ai-assistant-result-card" aria-label={`${kind}成果：${label}`} key={`${ref.kind}:${ref.id}`}>
        <div className="ai-assistant-result-heading"><span>{kind}</span><span>{undo ? '已撤销本项变更' : ref.status === 'adopted' && (ref.media_kind === 'video' || ref.media_kind === 'audio') ? '已自动采用' : typeof ref.status === 'string' ? resultStates[ref.status] ?? '已保存' : '已保存'}</span></div>
        <strong>{href ? <a href={href}>{label || `查看${kind}`}</a> : label || kind}</strong>
        {typeof ref.row_version === 'string' && <span className="ai-assistant-result-version">保存版本 {ref.row_version}</span>}
        {ref.kind === 'sound_line' && typeof ref.assembly_version === 'string' && <span className="ai-assistant-result-version">画面版本 {ref.assembly_version}</span>}
        {ref.kind === 'canvas_node' && typeof ref.private_revision === 'string' && <span className="ai-assistant-result-version">参数版本 {ref.private_revision}</span>}
        {typeof ref.primary_version === 'string' && <span className="ai-assistant-result-version">主图版本 {ref.primary_version}</span>}
        {typeof ref.voice_version === 'string' && <span className="ai-assistant-result-version">音色版本 {ref.voice_version}</span>}
        {typeof ref.item_version === 'string' && <span className="ai-assistant-result-version">图位版本 {ref.item_version}</span>}
        {typeof ref.generated_count === 'number' && typeof ref.requested_count === 'number' && <span className="ai-assistant-result-version">已生成 {ref.generated_count} / {ref.requested_count} {ref.media_role === 'character_voice' ? '个音色' : ref.media_kind === 'video' || ref.media_kind === 'audio' ? '段' : '张'}，其他结果保留在{ref.kind === 'canvas_node' || ref.media_kind === 'video' || ref.media_kind === 'audio' ? '私人任务和素材' : '图片组'}</span>}
        {ref.media_role === 'character_voice' && !undo && ref.status !== 'source_unavailable' && typeof ref.media_id === 'string' && <Button size="small" onClick={() => setVoice(ref)}>试听生成音色</Button>}
      </article>;
    })}
    {item.result_refs.some(ref => ['asset', 'shot', 'canvas_node', 'sound_line'].includes(String(ref.kind))) && <p className="ai-assistant-result-batch">操作批次 {item.id}</p>}
    {!!item.undo_receipts?.length && <p className="ai-assistant-result-batch" aria-label="批次撤销状态">已明确撤销 {new Set(item.undo_receipts.flatMap(receipt => receipt.selected_keys)).size} 项变更 · 原操作回执保留</p>}
    {voice && <VoicePreview result={voice} onClose={() => setVoice(null)}/>}
  </div>;
}

export function AssistantTaskTrace({ task, operations, layout, onUndo, undoDisabled }: { task: AssistantTask; operations: AssistantOperation[]; layout?: 'standard'; onUndo?: (id: string) => void; undoDisabled?: boolean }) {
  const [expanded, setExpanded] = useState<string[]>([]);
  const [detail, setDetail] = useState<AssistantOperation | null>(null);
  const [receipt, setReceipt] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const receiptRequest = useRef(0);
  useEffect(() => { setDetail(null); return () => { receiptRequest.current += 1; }; }, [task.id]);
  const items = operations.filter(item => item.task_id === task.id && !internalTools.has(item.tool_name)).sort((a, b) => BigInt(a.id) < BigInt(b.id) ? -1 : 1);
  const manual = task.skill_activations?.filter(item => item.source === 'manual') ?? [];
  async function openReceipt(item: AssistantOperation) {
    const request = ++receiptRequest.current;
    setDetail(item); setLoading(true); setError(''); setReceipt(null);
    try { const next = await assistantApi.operation(item.id); if (request === receiptRequest.current) { setReceipt(next.result); setDetail(next); } }
    catch (cause) { if (request === receiptRequest.current) setError(errorMessage(cause)); }
    finally { if (request === receiptRequest.current) setLoading(false); }
  }
  function closeReceipt() { receiptRequest.current += 1; setDetail(null); }
  if (!items.length && !manual.length) return null;
  return <section className="ai-assistant-task-trace" aria-label={`任务过程：${task.goal}`}>
    <ThoughtChain size="small" collapsible={{ expandedKeys: expanded, onExpand: setExpanded }} items={[...manual.map(item => ({
      key: `skill:${item.activation_id}`, title: '加载手动创作方法', status: item.active && item.issue ? 'error' as const : 'success' as const, icon: <BookOpen size={14}/>,
      description: `${item.name} · 版本 ${item.content_version} · ${item.active ? item.issue ? '暂不可用' : '本任务持续使用' : '已移除'}`,
      content: <p>{item.active && item.issue ? item.issue : item.purpose}</p>,
    })), ...items.map(item => ({
      key: item.call_id, title: labels[item.tool_name] ?? '执行创作操作',
      status: item.outcome === 'succeeded' ? 'success' as const : ['failed', 'conflict'].includes(item.outcome) ? 'error' as const : 'pending' as const,
      icon: item.outcome === 'succeeded' ? <Check size={14}/> : ['failed', 'conflict'].includes(item.outcome) ? <CircleAlert size={14}/>
        : item.tool_name.startsWith('read_') ? <BookOpen size={14}/> : <Wrench size={14}/>,
      description: item.outcome === 'succeeded' ? '结果已保存' : item.outcome === 'conflict' ? ['apply_standard_patch', 'apply_canvas_patch', 'set_image_group_primary'].includes(item.tool_name) ? '内容有冲突，请核对后继续' : '回填有冲突，产物已保留'
        : item.outcome === 'failed' ? item.error?.message ?? '操作未完成' : phases[item.phase] ?? '处理中',
      extra: <Tag>计划 {item.plan_revision}</Tag>,
      content: <div className="ai-assistant-step-results">
        {!!item.skill_activations?.length && <p>使用方法：{item.skill_activations.map(skill => `${skill.name} · 版本 ${skill.content_version}`).join('、')}</p>}
        <Results item={item}/>
        <Button size="small" onClick={() => void openReceipt(item)}>查看回执</Button>
        {layout === 'standard' && onUndo && ['apply_standard_patch', 'apply_canvas_patch', 'set_image_group_primary', 'submit_generation_batch'].includes(item.tool_name) && ['succeeded', 'failed', 'conflict'].includes(item.outcome ?? '') && item.result_refs.length > 0 && <Button size="small" disabled={undoDisabled} onClick={() => onUndo(item.id)}>预览批次撤销</Button>}
      </div>,
    }))]}/>
    {detail && <Dialog title={labels[detail.tool_name] ?? '操作回执'} className={layout === 'standard' ? 'standard-creation-dialog standard-assistant-dialog' : undefined} onClose={closeReceipt}>
      <div className="agent-context-dialog-body">
        {loading ? <Skeleton active paragraph={{ rows: 3 }}/> : error ? <div role="alert"><p>{error}</p><Button onClick={() => void openReceipt(detail)}>重新读取</Button></div>
          : <><p>此回执记录本次操作的实际结果。计划版本 {detail.plan_revision}。</p>
            <Results item={detail}/>
            {detail.error && <p>{detail.error.message ?? `操作未完成（${detail.error.code}）`}</p>}
            {receipt && <pre className="ai-assistant-receipt">{JSON.stringify(receipt, null, 2)}</pre>}</>}
      </div>
      <footer className="ai-assistant-history-footer"><Button onClick={closeReceipt}>关闭</Button></footer>
    </Dialog>}
  </section>;
}

import { useEffect, useState } from 'react';
import { Button, Skeleton } from 'antd';
import { errorMessage } from '../../api/http';
import { nativeVoiceApi, type VoiceCandidate } from '../../api/modules/native-voice';
import type { AssistantOperation } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { AssistantScriptProposal } from './AssistantScriptProposal';

const resultStates: Record<string, string> = {
  created: '已创建', updated: '已修改', deleted: '已删除', unchanged: '未变化', adopted: '已设为主图',
  bound: '已回填节点', source_unavailable: '来源不可用', adoption_conflict: '回填有冲突',
  generation_failed: '生成未全部完成',
};

const UNDOABLE = new Set(['apply_standard_patch', 'apply_canvas_patch', 'set_image_group_primary', 'submit_generation_batch']);
const TERMINAL = new Set(['succeeded', 'failed', 'conflict']);

export const resultLabel = (ref: Record<string, unknown>) => typeof ref.name === 'string' && ref.name.trim() ? ref.name
  : ref.kind === 'asset' ? '查看素材' : ref.kind === 'shot' ? '查看分镜' : ref.kind === 'canvas_node' ? '定位节点' : '查看成果';

export function resultHref(ref: Record<string, unknown>) {
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

/** Produced work of one operation. Never renders arguments, receipts or raw payloads. */
export function OperationResults({ operation, layout, onUndo, undoDisabled }: {
  operation: AssistantOperation; layout?: 'standard'; onUndo?: (id: string) => void; undoDisabled?: boolean;
}) {
  const [voice, setVoice] = useState<Record<string, unknown> | null>(null);
  const refs = operation.result_refs.filter(ref => ref.kind !== 'skill' && ref.kind !== 'skill_resource');
  if (!refs.length) return null;
  return <div className="ai-assistant-result-list" aria-label="本步成果">
    {refs.map((original, index) => {
      if (original.kind === 'script_proposal') return <AssistantScriptProposal key={operation.id} operationId={operation.id} disabled={undoDisabled}/>;
      const undoKind = original.media_role === 'character_voice' ? 'character_voice' : original.kind;
      const undo = operation.undo_receipts?.find(receipt => receipt.selected_keys.includes(`${undoKind}:${original.id}`));
      const restored = undo?.result_refs.find(ref => ref.kind === original.kind && ref.id === original.id);
      const ref = restored ? { ...original, ...restored } : original;
      const href = resultHref(ref); const label = resultLabel(ref);
      if (!['asset', 'shot', 'canvas_node', 'sound_line'].includes(String(ref.kind))) {
        return href ? <a key={index} href={href}>{label}</a> : <span key={index}>{label}</span>;
      }
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
    {!!operation.undo_receipts?.length && <p className="ai-assistant-result-batch" aria-label="批次撤销状态">已明确撤销 {new Set(operation.undo_receipts.flatMap(receipt => receipt.selected_keys)).size} 项变更</p>}
    {layout === 'standard' && onUndo && UNDOABLE.has(operation.tool_name) && TERMINAL.has(operation.outcome ?? '') && operation.result_refs.length > 0 && <Button size="small" disabled={undoDisabled} onClick={() => onUndo(operation.id)}>预览批次撤销</Button>}
    {voice && <VoicePreview result={voice} onClose={() => setVoice(null)}/>}
  </div>;
}

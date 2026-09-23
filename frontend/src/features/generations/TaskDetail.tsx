import { PreviewImage } from '../../components/ui/ImagePreview';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, Modal, Skeleton, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { generations } from '../../api/modules/generations';
import type { GenerationDetail, GenerationReceipt, GenerationRecord } from '../../api/types/generations';
import { attemptStorage, clearAttempt, isServerId, requestAttempt } from './attempt';
import { ConfigSelect } from './ConfigSelect';
import { dateLabel, generationError, kindLabels, statusLabels, taskLabel } from './presentation';
import { taskOrigin, taskPrompts } from './task-content';
import { episodePath } from '../../app/paths';

type Action = 'cancel' | 'retry' | 'resume';

function BusinessResult({ detail }: { detail: GenerationDetail }) {
  const business = detail.result.business;
  const source = detail.source;
  if (!business && !detail.source_snapshot && !detail.effective_prompt) return null;
  const episodeSource = source?.scene === 'novel_script' || source?.scene === 'script_shots' || source?.scene === 'script_assets' ? source : null;
  return <div className="generation-business-result">
    {business?.kind === 'novel_script' && <p>已保存候选剧本 <strong>{business.script_id}</strong>，需在本集页面预览并明确设为当前编辑。</p>}
    {business?.kind === 'script_shots' && <p>结构化分镜候选共 <strong>{business.shots.length}</strong> 镜。{business.applied ? `已${business.applied.mode === 'append' ? '追加' : '替换'}应用。` : '尚未应用。'}</p>}
    {business?.kind === 'script_assets' && <p>已提取 <strong>{business.items.length}</strong> 项文字素材，已采用 {business.items.filter(item => item.applied).length} 项。请在素材准备中核对名称、描述与图片生成提示词。</p>}
    {episodeSource && <Link to={episodePath(episodeSource.project_id, episodeSource.episode_id, episodeSource.scene === 'novel_script' ? 'source' : episodeSource.scene === 'script_assets' ? 'assets' : 'storyboard')}>打开来源分集页面</Link>}
  </div>;
}
export function TaskDetail({ id, onClose, onChanged, onCreated }: { id: string; onClose: () => void; onChanged: () => void; onCreated: (value: GenerationReceipt) => void }) {
  const [detail, setDetail] = useState<GenerationDetail | null>(null);
  const [records, setRecords] = useState<GenerationRecord[]>([]);
  const [error, setError] = useState('');
  const [recordsError, setRecordsError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [configId, setConfigId] = useState<string>();
  const active = useRef(true);
  const mutation = useRef(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); request.current = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let shouldPoll = false;
    setLoading(true); setError('');
    if (!isServerId(id)) { setError('任务 ID 格式不正确。'); setLoading(false); return; }
    async function load() {
      const [task, calls] = await Promise.allSettled([generations.detail(id, controller.signal), generations.records(id, controller.signal)]);
      if (controller.signal.aborted) return;
      if (task.status === 'fulfilled') {
        setDetail(task.value); setError('');
        shouldPoll = task.value.status === 'queued' || task.value.status === 'running';
      } else setError(generationError(task.reason));
      if (calls.status === 'fulfilled') { setRecords(calls.value.items); setRecordsError(''); }
      else setRecordsError(generationError(calls.reason));
      setLoading(false);
      if (shouldPoll) timer = setTimeout(load, 4000);
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, revision]);
  function refresh() { request.current?.abort(); setRevision((value) => value + 1); }
  async function perform() {
    if (!detail || !action || mutation.current) return;
    mutation.current = true; setBusy(true); setActionError('');
    request.current?.abort();
    try {
      if (action === 'retry') {
        const scope = `retry:${id}`;
        const result = await generations.retry(id, await requestAttempt(scope, { config_id: configId }, attemptStorage()), configId);
        clearAttempt(scope, attemptStorage());
        if (active.current) { setAction(null); onChanged(); onCreated(result); }
      } else {
        if (action === 'cancel') await generations.cancel(id); else await generations.resume(id);
        if (active.current) { setAction(null); setNotice(action === 'cancel' ? '取消请求已提交，以任务最新状态为准。' : '已提交安全恢复请求，请查看后续状态。'); onChanged(); }
      }
    } catch (cause) { if (active.current) setActionError(generationError(cause)); }
    finally { mutation.current = false; if (active.current) { setBusy(false); refresh(); } }
  }
  return <>
    <Drawer open title="任务详情" width={760} onClose={() => !busy && onClose()} closable={!busy} keyboard={!busy} maskClosable={!busy} rootClassName="generation-drawer">
      <div className="generation-detail-toolbar"><span className="generation-id" title={id}>任务编号 {id}</span><Button onClick={refresh} disabled={busy} loading={loading}>刷新</Button></div>
      {error && <Alert type="error" showIcon message={error} description={detail ? '保留上次已知状态，请刷新核对。' : undefined} />}
      {notice && <Alert type="success" showIcon message={notice} />}
      {loading && !detail ? <div className="generation-loading"><Skeleton title paragraph={{ rows: 5 }}/></div> : detail && <>
        <div className="generation-task-heading"><h2>{taskOrigin(detail)}</h2><Tag className={`generation-status status-${detail.status}`}>{taskLabel(detail)}</Tag></div>
        <dl className="generation-facts"><div><dt>模型配置</dt><dd>{detail.config?.name ?? '—'} · {detail.config?.model_key ?? '—'}</dd></div><div><dt>创建时间</dt><dd>{dateLabel(detail.created_at)}</dd></div><div><dt>开始时间</dt><dd>{dateLabel(detail.started_at)}</dd></div><div><dt>完成时间</dt><dd>{dateLabel(detail.finished_at)}</dd></div></dl>
        {detail.error && <Alert type="warning" showIcon message={detail.error.message} description={`错误代码：${detail.error.code}`} />}
        <div className="generation-action-row">
          {detail.can_cancel && <Button disabled={busy} onClick={() => { setAction('cancel'); setActionError(''); }}>请求取消</Button>}
          {detail.can_resume && <Button type="primary" disabled={busy} onClick={() => { setAction('resume'); setActionError(''); }}>安全恢复</Button>}
          {detail.can_retry && <Button type="primary" disabled={busy} onClick={() => { setAction('retry'); setActionError(''); }}>重新生成</Button>}
        </div>
        <section className="generation-section"><h3>生成结果</h3>
          {detail.result.partial && <Alert type="warning" showIcon message={`未全部完成，已保留 ${detail.result.assets.length} 个媒体结果。`} />}
          {detail.result.text && <><pre className="generation-text-result">{detail.result.text.content}</pre>{detail.result.text.finish_reason === 'length' && <p className="generation-hint">正文已达到输出长度限制，以上内容已保留。</p>}</>}
          {!!detail.result.assets.length && <div className="generation-result-assets">{detail.result.assets.map((asset) => <article key={asset.asset_id} className="generation-result-asset">
            {asset.media_type === 'image' && asset.url ? <PreviewImage src={asset.url} alt={asset.name}/> : <span className="generation-media-placeholder">{asset.media_type === 'video' ? '视频' : '图片'}</span>}
            <span>{asset.name}</span><Link to={`/media-library/${asset.media_type}?asset=${asset.asset_id}`}>查看资产与确认采用</Link>
          </article>)}</div>}
          {!detail.result.text && !detail.result.assets.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={detail.status === 'queued' || detail.status === 'running' ? '结果将在完成后显示' : '暂无已保存结果'} />}
          <BusinessResult detail={detail}/>
        </section>
        <section className="generation-section"><h3>提示词</h3>{taskPrompts(detail).length ? taskPrompts(detail).map((prompt, index) => <div key={index}><h4>{prompt.label}</h4><pre className="generation-text-result">{prompt.content}</pre></div>) : <p className="generation-hint">此任务未保存提示词。</p>}</section>
        <details className="generation-section generation-record-details"><summary>调用记录（{records.length}）</summary>
          {recordsError && <Alert type="error" message={recordsError} action={<Button onClick={refresh}>重试</Button>} />}
          {!recordsError && !records.length && <p className="generation-hint">暂无调用记录。</p>}
          <ol className="generation-records">{records.map((record) => <li key={record.record_id}><div><strong>第 {record.call_no} 次调用</strong><span>{record.status === 'unknown' ? '状态待核实' : statusLabels[record.status as keyof typeof statusLabels] ?? record.status}</span></div><p>{dateLabel(record.started_at ?? record.created_at)}</p>{record.error && <p className="generation-record-error">{record.error.message}</p>}{record.finish_reason && <p>结束原因：{record.finish_reason}</p>}{record.usage && <details><summary>查看用量</summary><pre className="generation-json">{JSON.stringify(record.usage, null, 2)}</pre></details>}</li>)}</ol>
        </details>
      </>}
    </Drawer>
    <Modal open={!!action} title={action === 'retry' ? '确认重新生成' : action === 'cancel' ? '确认请求取消' : '确认安全恢复'} onCancel={() => !busy && setAction(null)} onOk={perform} confirmLoading={busy} closable={!busy} maskClosable={!busy} keyboard={!busy} cancelButtonProps={{ disabled: busy }} okText={action === 'retry' ? '新建生成任务' : action === 'cancel' ? '提交取消请求' : '恢复任务'}>
      <p>{action === 'retry' ? '将创建一个新任务，重新执行整次生成，可能再次产生费用。原任务和已保存资产会保留。' : action === 'cancel' ? '已发送给模型服务的请求可能无法取消，也可能已产生费用。任务状态会在确认后更新。' : '服务端将根据已保存的调用状态恢复查询、提交或结果保存。受理不明的请求不会被盲目重新提交。'}</p>
      {action === 'retry' && detail && <div className="generation-retry-config"><label>重新选择模型配置（选填）</label><ConfigSelect kind={detail.service_type} autoDefault={false} value={configId} onChange={(value) => { setConfigId(value); clearAttempt(`retry:${id}`, attemptStorage()); }} disabled={busy} /><p className="generation-hint">留空沿用原任务配置；配置已更改或停用时请选择可用配置。</p></div>}
      {actionError && <Alert type="error" message={actionError} />}
    </Modal>
  </>;
}

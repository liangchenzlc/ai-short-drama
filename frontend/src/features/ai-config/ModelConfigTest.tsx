import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { Dialog } from '../../components/ui/Dialog';
import { aiConfigTools } from '../../api/modules/ai-config-tools';
import { ApiError, errorMessage } from '../../api/http';
import type { ModelTestDto } from '../../api/types/ai-config-tools';
import type { AiConfig } from './config-model';

const stateLabel: Record<ModelTestDto['status'], string> = { queued: '排队中', running: '测试中', succeeded: '模型已返回真实结果', failed: '测试失败', cancelled: '测试已取消' };
const unresolvedAttempts = new Map<string, string>();
export function ModelConfigTest({ config, onClose }: { config: AiConfig; onClose: () => void }) {
  const identity = `${config.id}:${config.rowVersion}`;
  const [result, setResult] = useState<ModelTestDto | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(() => unresolvedAttempts.has(identity));
  const operation = useRef<string | null>(null);
  const active = useRef(true);
  const revision = useRef(0);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function submit() {
    if (busy || result) return;
    operation.current ??= unresolvedAttempts.get(identity) ?? crypto.randomUUID();
    unresolvedAttempts.set(identity, operation.current);
    setBusy(true); setError('');
    try { const next = await aiConfigTools.startTest(config, operation.current); unresolvedAttempts.delete(identity); if (active.current) { setResult(next); setUncertain(false); } }
    catch (cause) { const unknown = !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;
      if (!unknown) { unresolvedAttempts.delete(identity); operation.current = null; }
      if (active.current) { setError(errorMessage(cause)); setUncertain(unknown); }
    }
    finally { if (active.current) setBusy(false); }
  }
  useEffect(() => {
    if (busy || !result || !['queued', 'running'].includes(result.status)) return;
    const requested = revision.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const next = await aiConfigTools.readTest(result.id, controller.signal); if (!controller.signal.aborted && requested === revision.current) { setResult(next); setError(''); if (['queued', 'running'].includes(next.status)) timer = setTimeout(() => void poll(), 1500); } }
      catch (cause) { if (!controller.signal.aborted && requested === revision.current) setError(errorMessage(cause)); }
    };
    timer = setTimeout(() => void poll(), 500);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [result?.id, result?.status, busy]);
  async function read() {
    if (!result || busy) return;
    revision.current += 1; setBusy(true);
    try { const next = await aiConfigTools.readTest(result.id); if (active.current) { setResult(next); setError(''); } }
    catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { if (active.current) setBusy(false); }
  }
  async function cancel() {
    if (!result || busy || !result.canCancel) return;
    revision.current += 1; setBusy(true);
    try { const next = await aiConfigTools.cancelTest(result.id); if (active.current) { setResult(next); setError(''); } }
    catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { if (active.current) setBusy(false); }
  }
  return <Dialog title={`测试模型 · ${config.name}`} canClose={!busy} onClose={onClose}>
    <p>使用已保存的模型配置发起一次真实调用，可能产生费用。测试结果仅本人可见，不创建项目或画布节点。</p>
    {result && <p role="status">{stateLabel[result.status]}</p>}
    {result?.result?.text && <pre className="config-test-output">{result.result.text}</pre>}
    {result?.result?.images?.map((image, index) => <img key={index} className="config-test-media" src={image.url || image.dataUrl} alt={`模型测试图片 ${index + 1}`}/>)}
    {result?.result?.video && <video className="config-test-media" src={result.result.video.url || result.result.video.dataUrl} controls/>}
    {result?.result?.audio && <audio src={result.result.audio.url || result.result.audio.dataUrl} controls/>}
    {result?.status === 'succeeded' && !result.result?.text && <p>媒体结果已保存，可在生成历史中查看。</p>}
    {(error || result?.error) && <p role="alert" className="form-error">{error || result?.error}</p>}
    {uncertain && <p>受理结果尚未确认；核对时会复用同一请求身份，不会新建测试。</p>}
    <div className="dialog-actions"><Button onClick={onClose} disabled={busy}>{result ? '关闭观察' : '取消'}</Button>
      {!result && <Button type="primary" onClick={() => void submit()} aria-label={uncertain ? '核对原测试' : '开始模型测试'} aria-busy={busy} loading={busy} disabled={!config.enabled}>{uncertain ? '核对原测试' : '开始模型测试'}</Button>}
      {result && error && <Button onClick={() => void read()} aria-label="重新读取测试" aria-busy={busy} loading={busy}>重新读取测试</Button>}
      {result?.canCancel && <Button onClick={() => void cancel()} disabled={busy}>取消测试任务</Button>}
    </div>
  </Dialog>;
}

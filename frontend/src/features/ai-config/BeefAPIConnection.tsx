import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { aiConfigTools } from '../../api/modules/ai-config-tools';
import { ApiError, errorMessage } from '../../api/http';
import type { BeefAPIConnectionDto } from '../../api/types/ai-config-tools';
import { trustedBeefAPIPage } from './config-form-model';
import { notifyAiConfigsChanged } from './config-events';
import { confirmAction } from '../../components/ui/confirm';

declare const __HOST_BEEFAPI_TEST_ORIGIN__: string;
const browserTest = import.meta.env.MODE === 'test' && typeof __HOST_BEEFAPI_TEST_ORIGIN__ !== 'undefined' && __HOST_BEEFAPI_TEST_ORIGIN__
  ? { mode: 'test', origin: __HOST_BEEFAPI_TEST_ORIGIN__ } : undefined;

const labels: Record<BeefAPIConnectionDto['state'], string> = {
  disconnected: '未连接', pending: '等待浏览器授权', connected: '已连接', expired: '授权已过期',
  cancelled: '已取消授权', rejected: '授权被拒绝', store_error: '连接保存失败', catalog_failed: '模型目录读取失败', revoked: '连接已失效',
};
export function BeefAPIConnection({ onChanged }: { onChanged: () => void }) {
  const [opened, setOpened] = useState(false);
  const [summary, setSummary] = useState<BeefAPIConnectionDto | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  const revision = useRef(0);
  const actionPending = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  function adopt(value: BeefAPIConnectionDto) {
    if (!active.current) return;
    setSummary(value);
    if (value.state === 'connected' || value.state === 'disconnected') { notifyAiConfigsChanged(); onChanged(); }
  }
  async function reload() {
    if (loading || actionPending.current) return;
    const requested = ++revision.current;
    setLoading(true); setError('');
    try { const value = await aiConfigTools.connection(); if (active.current && requested === revision.current) adopt(value); }
    catch (cause) { if (active.current && requested === revision.current) setError(errorMessage(cause)); }
    finally { if (active.current && requested === revision.current) setLoading(false); }
  }
  useEffect(() => {
    if (!opened || busy || loading || summary?.state !== 'pending') return;
    const requested = revision.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const value = await aiConfigTools.connection(controller.signal); if (!controller.signal.aborted && requested === revision.current) { adopt(value); if (value.state === 'pending') timer = setTimeout(() => void poll(), 3000); } }
      catch (cause) { if (!controller.signal.aborted && requested === revision.current) setError(errorMessage(cause)); }
    };
    timer = setTimeout(() => void poll(), 3000);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [opened, summary?.state, busy, loading]);
  async function openEnterprise(kind: 'authorization' | 'wallet') {
    if (actionPending.current || loading) return;
    const popup = window.open('about:blank', '_blank');
    if (!popup) { setError('浏览器阻止了新页面，请允许弹出窗口后重试。'); return; }
    popup.opener = null;
    if (popup.opener !== null) { popup.close(); setError('无法安全打开企业页面，请重试。'); return; }
    actionPending.current = true; revision.current += 1;
    setBusy(true); setError('');
    try {
      if (kind === 'wallet') {
        const wallet = await aiConfigTools.wallet();
        if (!active.current || popup.closed) throw new Error('企业页面已关闭，请重新打开。');
        popup.location.replace(trustedBeefAPIPage(wallet.walletUrl, 'wallet', browserTest));
      } else {
        if (summary?.hasCredential) adopt(await aiConfigTools.disconnect());
        const next = await aiConfigTools.connect();
        if (!active.current || popup.closed) throw new Error('授权页面已关闭，请重新连接。');
        adopt(next);
        if (next.state === 'pending' && next.verificationUri) popup.location.replace(trustedBeefAPIPage(next.verificationUri, 'authorization', browserTest));
        else popup.close();
      }
    } catch (cause) { popup.close(); if (active.current) setError(cause instanceof ApiError ? errorMessage(cause) : cause instanceof Error ? cause.message : '企业页面打开失败，请重试。'); }
    finally { actionPending.current = false; if (active.current) setBusy(false); }
  }
  async function disconnect() {
    if (actionPending.current || loading || !await confirmAction('断开 BeefAPI 官方连接后，此连接的模型将无法继续发起新任务。已创建任务仍保留。', { title: '断开官方连接', confirmText: '断开连接' }) || actionPending.current || !active.current) return;
    actionPending.current = true; revision.current += 1;
    setBusy(true); setError('');
    try { adopt(await aiConfigTools.disconnect()); }
    catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { actionPending.current = false; if (active.current) setBusy(false); }
  }
  async function cancel() {
    if (actionPending.current || loading) return;
    actionPending.current = true; revision.current += 1;
    setBusy(true); setError('');
    try { adopt(await aiConfigTools.cancelConnection()); }
    catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { actionPending.current = false; if (active.current) setBusy(false); }
  }
  const account = summary?.account?.display_name || summary?.account?.username || summary?.account?.email;
  return <details className="config-advanced config-beefapi" onToggle={event => {
    setOpened(event.currentTarget.open); if (event.currentTarget.open && !summary && !loading) void reload();
  }}><summary>BeefAPI 官方连接</summary><div className="config-connection">
    <div className="config-connection-info">
      <strong>{loading ? '正在读取连接状态…' : summary ? `${labels[summary.state]}${account ? ` · ${account}` : ''}` : '读取官方连接状态'}</strong>
      {summary?.state === 'pending' && <p role="status">在企业授权页面确认{summary.userCode ? `代码 ${summary.userCode}` : '本次授权'}。关闭此页面后服务端仍会完成授权。</p>}
      {summary?.state === 'connected' && <p className="config-default-hint">{summary.balance === 'zero' ? '当前余额为 0，请在钱包中核对。' : '余额请在企业钱包中核对。'}官方密钥不会回传浏览器。</p>}
      {error && <p role="alert" className="form-error">{error}</p>}
      {summary?.catalogFailed && <p role="alert" className="form-error">连接已保存，模型目录读取失败，请重新连接后核对。</p>}
    </div>
    <div className="config-connection-actions">
      {summary && <Button onClick={() => void openEnterprise('authorization')} aria-label={summary.hasCredential ? '重新连接' : '连接 BeefAPI'} aria-busy={busy} loading={busy} disabled={loading || busy}>{summary.hasCredential ? '重新连接' : '连接 BeefAPI'}</Button>}
      {summary?.state === 'pending' && <Button onClick={() => void cancel()} disabled={busy}>取消授权</Button>}
      {summary?.hasCredential && <><Button onClick={() => void openEnterprise('wallet')} disabled={busy}>打开钱包</Button><Button onClick={() => void disconnect()} disabled={busy}>断开连接</Button></>}
      <Button onClick={() => void reload()} disabled={busy || loading}>重新读取状态</Button>
    </div>
  </div></details>;
}

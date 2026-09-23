import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Skeleton } from 'antd';
import { storyboardApi, type StoryboardResultPage } from '../../api/modules/storyboard';
import { errorMessage } from '../../api/http';
import { LazyLoadMore } from '../../components/ui/LazyLoadMore';

export function StoryboardResultPreview({ projectId, episodeId, generationId, busy, disabled = false, error, onApply }: {
  projectId: string; episodeId: string; generationId: string;
  busy: boolean; disabled?: boolean; error?: string;
  onApply: (mode: 'append' | 'replace') => void;
}) {
  const [page, setPage] = useState<StoryboardResultPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState('');
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function load(append = false) {
    if (lock.current) return;
    lock.current = true; setLoading(true); setFailure('');
    try {
      const next = await storyboardApi(projectId, episodeId).resultShots(generationId, append ? page?.items.length : 0);
      if (alive.current) setPage(previous => ({ ...next, items: append ? [...(previous?.items ?? []), ...next.items] : next.items }));
    } catch (cause) { if (alive.current) setFailure(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return <section className="storyboard-result-preview">
    <header className="storyboard-result-heading"><h3>分镜预览{page ? `（共 ${page.total} 镜）` : ''}</h3></header>
    {page?.applied && <Alert type="success" message={`已${page.applied.mode === 'append' ? '追加' : '替换'}到当前分镜`}/>}
    {loading && !page && <Skeleton active paragraph={{ rows: 5 }}/>}
    <div className="lazy-scroll storyboard-result-scroll"><ol className="compact-shot-list">{page?.items.map(shot => <li key={shot.position}><strong>分镜 {String(shot.position).padStart(2, '0')}</strong><p>{shot.script}</p></li>)}</ol>
      <LazyLoadMore hasMore={!page || page.items.length < page.total} loading={loading} error={failure} onLoad={() => void load(!!page)}/>
    </div>
    {error && <Alert type="error" message={error}/>}
    <div className="dialog-actions"><Button type="primary" loading={busy} disabled={disabled || !page || !!page.applied} onClick={() => onApply('append')}>追加到现有分镜</Button><Button danger loading={busy} disabled={disabled || !page || !!page.applied} onClick={() => onApply('replace')}>替换当前分镜</Button></div>
  </section>;
}

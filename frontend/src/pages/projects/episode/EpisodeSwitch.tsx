import { useEffect, useState } from 'react';
import { Button, Popover, Spin } from 'antd';
import { DownOutlined } from '@ant-design/icons';
import { projectsApi, type RemoteEpisode } from '../../../api/modules/projects';
import { errorMessage } from '../../../api/http';

export function EpisodeSwitch({ projectId, episodeId, number, title, onSelect }: {
  projectId: string; episodeId: string; number: number; title: string; onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<RemoteEpisode[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setLoading(true); setError('');
    projectsApi.listEpisodes(projectId, offset, 20, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setItems(previous => offset ? [...previous, ...page.items.filter(item => !previous.some(old => old.id === item.id))] : page.items);
      setTotal(page.total);
    }).catch(cause => { if (!controller.signal.aborted) setError(`分集列表读取失败：${errorMessage(cause)}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, open, offset, revision]);
  return <Popover trigger="click" placement="bottomLeft" open={open} onOpenChange={value => { setOpen(value); if (value) setOffset(0); }} content={<div className="episode-switch-list" aria-label="选择分集">
    {items.map((item, index) => <button type="button" key={item.id} aria-current={item.id === episodeId ? 'true' : undefined} onClick={() => { setOpen(false); if (item.id !== episodeId) onSelect(item.id); }}>第 {String(item.number ?? item.position ?? index + 1).padStart(2, '0')} 集 · {item.title}</button>)}
    {loading && <div role="status"><Spin size="small"/> 正在读取分集…</div>}
    {error && <div role="alert"><p>{error}</p><Button size="small" onClick={() => setRevision(value => value + 1)}>重新读取分集</Button></div>}
    {!loading && !error && !items.length && <p>暂无分集。</p>}
    {!loading && !error && items.length < total && <Button size="small" onClick={() => setOffset(items.length)}>加载更多分集</Button>}
  </div>}>
    <Button type="text" className="episode-switch" aria-label={`选择分集：第 ${number} 集 · ${title}`} aria-expanded={open}><span>第 {String(number).padStart(2, '0')} 集 · {title}</span><DownOutlined/></Button>
  </Popover>;
}

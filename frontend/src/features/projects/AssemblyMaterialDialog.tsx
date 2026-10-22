import { useEffect, useRef, useState } from 'react';
import { Button, Input, Segmented, Spin } from 'antd';
import { FileAudio, Plus, RefreshCw, Upload } from 'lucide-react';
import type { AssemblyMaterial } from '../../api/modules/assembly';
import { mediaLibrary } from '../../api/modules/media-library';
import { errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { AssemblyAction } from './AssemblyAction';
import type { useAssembly } from './useAssembly';

export function AssemblyMaterialDialog({ editor, audio, replacing, onClose, onChoose }: {
  editor: ReturnType<typeof useAssembly>; audio: boolean; replacing: boolean; onClose: () => void; onChoose: (material: AssemblyMaterial) => void;
}) {
  const [tab, setTab] = useState('project'), [kind, setKind] = useState<'video' | 'image' | 'audio'>(audio ? 'audio' : 'video');
  const [items, setItems] = useState<AssemblyMaterial[]>([]), [search, setSearch] = useState(''), [error, setError] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [page, setPage] = useState(0), [more, setMore] = useState(false), [epoch, setEpoch] = useState(0);
  const upload = useRef<HTMLInputElement>(null), mutation = useRef(false);
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('');
    const load = tab === 'project' ? editor.api.materials().then(value => ({ items: value.filter(m => m.kind === kind), more: false })) : mediaLibrary.list({ media_type: kind, resource_scope: 'personal', offset: page * 40, limit: 40 }).then(value => ({ items: value.items.map(m => ({ media_id: m.media_id, kind: m.media_type, name: m.name, url: m.url ?? '', poster: m.media_type === 'image' ? m.url : null, duration_ms: m.duration_ms ?? null, width: m.width ?? null, height: m.height ?? null, has_audio: m.media_type !== 'image' })), more: value.items.length === 40 }));
    void load.then(value => { if (!cancelled) { setItems(value.items); setMore(value.more); } }).catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : errorMessage(cause)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tab, kind, page, epoch, editor.api]);
  async function choose(material?: AssemblyMaterial, file?: File) {
    if (mutation.current) return; mutation.current = true; setBusy(true); setError('');
    try {
      const result = file ? await editor.api.uploadMaterial(file) : await editor.api.importMaterial(material!.media_id);
      editor.registerMaterial(result); onChoose(result); onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : errorMessage(cause)); } finally { mutation.current = false; setBusy(false); }
  }
  return <Dialog title={replacing ? '替换画面素材' : audio ? '添加声音素材' : '添加画面素材'} className="standard-creation-dialog assembly-material-dialog" onClose={onClose} canClose={!busy}>
    <div className="assembly-material-tools">
      <Segmented aria-label="素材来源" options={[{ label: '项目素材', value: 'project' }, { label: '我的媒体库', value: 'personal' }]} value={tab} onChange={value => { setTab(value); setPage(0); }}/>
      {!audio && <Segmented aria-label="素材类型" value={kind} options={[{ label: '视频', value: 'video' }, { label: '图片', value: 'image' }]} onChange={value => { setKind(value as 'video' | 'image'); setPage(0); }}/>}
      <AssemblyAction label="刷新素材" icon={<RefreshCw size={15}/>} disabled={busy || loading} onClick={() => setEpoch(v => v + 1)}/>
      <Button icon={<Upload size={15}/>} loading={busy} onClick={() => upload.current?.click()}>本地导入</Button>
      <input ref={upload} type="file" hidden accept={audio ? 'audio/*' : 'video/*,image/png,image/jpeg,image/webp,image/gif'} aria-label="本地导入成片素材" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void choose(undefined, file); }}/>
    </div>
    <Input aria-label="搜索成片素材" placeholder="搜索素材" value={search} onChange={event => setSearch(event.target.value)}/>
    {error && <StudioFeedback type="error" noticeKey="assembly-material" message={error} actions={<Button onClick={() => setEpoch(v => v + 1)}>重新读取</Button>}/>}
    <div className="assembly-material-grid" aria-busy={loading}>
      {loading ? <Spin/> : !items.length ? <div className="assembly-material-empty">{error ? '素材读取失败' : '暂无素材，导入本地文件开始编辑'}</div> : items.filter(m => m.name.toLowerCase().includes(search.toLowerCase())).map(item => <button key={item.media_id} disabled={busy} aria-label={`${replacing ? '替换为' : '添加'} ${item.name}`} onClick={() => { void choose(item); }}>
        <div>{item.kind === 'audio' ? <FileAudio size={28}/> : item.kind === 'image' || item.poster ? <img src={item.poster || item.url} alt="" loading="lazy"/> : <video src={item.url} muted preload="metadata"/>}<Plus size={17}/></div>
        <strong title={item.name}>{item.name}</strong><small>{item.kind === 'image' ? '图片 · 默认 5 秒' : `${((item.duration_ms ?? 0) / 1000).toFixed(1)} 秒`}</small>
      </button>)}
    </div>
    {tab === 'personal' && <footer><Button disabled={page === 0 || loading} onClick={() => setPage(p => p - 1)}>上一页</Button><span>{page + 1}</span><Button disabled={!more || loading} onClick={() => setPage(p => p + 1)}>下一页</Button></footer>}
  </Dialog>;
}

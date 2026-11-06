import type { EpisodeView } from '@host/types/creation/workspace/workbenchView';
import { Button, Input, Popconfirm, Popover } from 'antd';
import { ArrowDown, ArrowUp, ChevronDown, Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

export function episodeLabel(index: number) {
  return '第 ' + String(index + 1).padStart(2, '0') + ' 集';
}

export function EpisodeSelector({ episodes, selectedId, onSelect, onAdd, onRename, onMove, onDelete }: {
  episodes: EpisodeView[];
  selectedId: string;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onRename: (id: string, title: string) => void;
  onMove: (id: string, direction: -1 | 1) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const index = episodes.findIndex((episode) => episode.id === selectedId);
  if (episodes.length < 2) return null;

  return (
    <Popover trigger="click" open={open} onOpenChange={setOpen} placement="bottomLeft" content={
      <div className="episodeMenu" role="menu" aria-label="分集列表">
        {episodes.map((episode, position) => (
          <div className="episodeMenuRow" key={episode.id}>
            {editing === episode.id ? (
              <Input size="small" autoFocus aria-label="分集名称" value={title} onChange={(event) => setTitle(event.target.value)}
                onPressEnter={() => { onRename(episode.id, title.trim()); setEditing(null); }}
                onBlur={() => { onRename(episode.id, title.trim()); setEditing(null); }} />
            ) : (
              <button type="button" role="menuitem" onClick={() => { onSelect(episode.id); setOpen(false); }}>
                {episodeLabel(position)}{episode.title && <span> · {episode.title}</span>}
              </button>
            )}
            <div className="episodeMenuActions">
              <Button type="text" size="small" aria-label={'重命名' + episodeLabel(position)} icon={<Pencil size={13} />}
                onClick={() => { setTitle(episode.title); setEditing(episode.id); }} />
              <Button type="text" size="small" aria-label={'上移' + episodeLabel(position)} icon={<ArrowUp size={13} />}
                disabled={position === 0} onClick={() => onMove(episode.id, -1)} />
              <Button type="text" size="small" aria-label={'下移' + episodeLabel(position)} icon={<ArrowDown size={13} />}
                disabled={position === episodes.length - 1} onClick={() => onMove(episode.id, 1)} />
              <Popconfirm title="删除分集？" description="该集概要、剧本、分镜和成片数据都会删除，无法撤销；共享资产保留。"
                okText="删除" cancelText="取消" onConfirm={() => onDelete(episode.id)}>
                <Button type="text" size="small" danger aria-label={'删除' + episodeLabel(position)} icon={<Trash2 size={13} />} />
              </Popconfirm>
            </div>
          </div>
        ))}
        <Button type="text" icon={<Plus size={14} />} onClick={() => { onAdd(); setOpen(false); }}>添加分集</Button>
      </div>
    }>
      <Button className="episodeSelector" aria-label={'当前分集：' + episodeLabel(index)} icon={<ChevronDown size={14} />} iconPosition="end">
        {episodeLabel(index)}{episodes[index]?.title && ' · ' + episodes[index].title}
      </Button>
    </Popover>
  );
}
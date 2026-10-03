import { useEffect, useRef, useState } from 'react';
import { Button, Dropdown } from 'antd';
import { Link } from 'react-router-dom';
import { projectsApi, type RemoteEpisode } from '../../api/modules/projects';
import { episodePath } from '../../app/paths';
import { Icon } from '../../components/ui/Icon';

function EpisodeCover({ episode }: { episode: RemoteEpisode }) {
  const [url, setUrl] = useState(episode.coverUrl);
  const [failed, setFailed] = useState(false);
  const retried = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function recoverImage() {
    setFailed(true);
    if (retried.current) return;
    retried.current = true;
    controller.current = new AbortController();
    try {
      const current = await projectsApi.getEpisode(episode.projectId, episode.id, controller.current.signal);
      if (!controller.current.signal.aborted && current.coverUrl && current.coverUrl !== url) {
        setUrl(current.coverUrl); setFailed(false);
      }
    } catch { /* 图片不可用时保留默认封面，不阻止打开分集。 */ }
  }
  return <div className="episode-cover">{url && !failed ? <img src={url} alt="" loading="lazy" width={episode.aspect === '9:16' ? 180 : 240} height={episode.aspect === '9:16' ? 320 : 135} onError={() => void recoverImage()} /> : <div className="episode-default-cover"><Icon name="film" size={36} /><span>{failed ? '封面暂不可用' : '暂无封面'}</span></div>}</div>;
}

export function EpisodeCard({ episode, number, disabled, onEdit, onDelete }: {
  episode: RemoteEpisode;
  number: number;
  disabled: boolean;
  onEdit: (episode: RemoteEpisode) => void;
  onDelete: (episode: RemoteEpisode) => void;
}) {
  const menuTrigger = useRef<HTMLButtonElement>(null);
  return <article className={`episode-media-card${episode.aspect === '9:16' ? ' is-portrait' : ''}`}>
    <Link className="episode-media-link" to={episodePath(episode.projectId, episode.id)} aria-label={`进入第 ${number} 集：${episode.title}`}>
      <EpisodeCover key={episode.coverUrl ?? 'default'} episode={episode} />
      <strong title={episode.title}>{episode.title}</strong>
    </Link>
    <Dropdown trigger={['click']} menu={{ items: [{ key: 'edit', label: '编辑分集', disabled }, { key: 'delete', label: '删除分集', danger: true, disabled }], onClick: ({ key }) => { menuTrigger.current?.focus({ preventScroll: true }); if (key === 'edit') onEdit(episode); else if (key === 'delete') onDelete(episode); } }}>
      <Button ref={menuTrigger} type="text" className="episode-more" icon={<Icon name="more" size={18} />} aria-label={`第 ${number} 集更多操作`} disabled={disabled} />
    </Dropdown>
  </article>;
}

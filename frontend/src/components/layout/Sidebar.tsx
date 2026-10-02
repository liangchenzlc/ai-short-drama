import { useEffect, useState } from "react";
import { Link } from 'react-router-dom';
import { Icon } from '../ui/Icon';
import type { AssetKind } from "../../features/assets/asset-model";

export type MainPage = "projects" | "assets" | "tasks" | "media-library" | "ai";
export function Sidebar({
  page,
  kind,
  onSelect,
}: {
  page: MainPage;
  kind: AssetKind;
  onSelect: (page: MainPage, kind?: AssetKind) => void;
}) {
  const [expanded, setExpanded] = useState(page === "assets");
  useEffect(() => { setExpanded(page === "assets"); }, [page]);
  return (
    <aside className="studio-sidebar">
      <Link className="studio-brand" to="/projects" aria-label="短剧工作台首页">
        <span className="studio-brand-mark" aria-hidden="true">
          <Icon name="film" size={24} />
        </span>
        <span>
          短剧工作台<small>AI 短剧创作空间</small>
        </span>
      </Link>
      <nav aria-label="主导航">
        <Link to="/projects"
          className={page === "projects" ? "studio-nav active" : "studio-nav"}
          aria-current={page === "projects" ? "page" : undefined}
        >
          <Icon name="folder" />项目管理
        </Link>
        <button
          type="button"
          className={
            page === "assets"
              ? "studio-nav studio-nav-assets active"
              : "studio-nav studio-nav-assets"
          }
          aria-controls="asset-subnav"
          aria-expanded={expanded}
          onClick={() => {
            if (!expanded) onSelect("assets", kind);
            setExpanded(!expanded);
          }}
        >
          <Icon name="library" />素材库
          <span
            className={expanded ? "chevron expanded" : "chevron"}
            aria-hidden="true"
          />
        </button>
        <div id="asset-subnav" className="studio-subnav" hidden={!expanded}>
          {(
            [
              ["character", "角色"],
              ["scene", "场景"],
              ["prop", "道具"],
            ] as const
          ).map(([value, label]) => (
            <Link to={`/assets/${value}`}
              key={value}
              className={
                page === "assets" && kind === value
                  ? "studio-subnav-item active"
                  : "studio-subnav-item"
              }
              aria-current={
                page === "assets" && kind === value ? "page" : undefined
              }
            >
              {label}
            </Link>
          ))}
        </div>
        <Link to="/tasks/text" className={page === 'tasks' ? 'studio-nav active' : 'studio-nav'} aria-current={page === 'tasks' ? 'page' : undefined}>
          <Icon name="tasks" />任务管理
        </Link>
        <Link to="/media-library/image" className={page === 'media-library' ? 'studio-nav active' : 'studio-nav'} aria-current={page === 'media-library' ? 'page' : undefined}>
          <Icon name="scene" />资产库
        </Link>
        <Link to="/ai"
          className={page === "ai" ? "studio-nav active" : "studio-nav"}
          aria-current={page === "ai" ? "page" : undefined}
        >
          <Icon name="settings" />AI 配置
        </Link>
      </nav>
      <p className="studio-sidebar-foot">
        从故事出发，逐镜打磨。
      </p>
    </aside>
  );
}

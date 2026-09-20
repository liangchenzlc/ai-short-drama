import { useState } from "react";
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
  const [expanded, setExpanded] = useState(false);
  return (
    <aside className="studio-sidebar">
      <div className="studio-brand">
        <span className="studio-brand-mark" aria-hidden="true">
          <Icon name="film" size={24} />
        </span>
        <span>
          短剧工作台<small>故事，从这里成片</small>
        </span>
      </div>
      <nav aria-label="主导航">
        <button
          className={page === "projects" ? "studio-nav active" : "studio-nav"}
          onClick={() => {
            setExpanded(false);
            onSelect("projects");
          }}
          aria-current={page === "projects" ? "page" : undefined}
        >
          <Icon name="folder" />项目管理
        </button>
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
            <button
              key={value}
              className={
                page === "assets" && kind === value
                  ? "studio-subnav-item active"
                  : "studio-subnav-item"
              }
              onClick={() => onSelect("assets", value)}
              aria-current={
                page === "assets" && kind === value ? "page" : undefined
              }
            >
              {label}
            </button>
          ))}
        </div>
        <button className={page === 'tasks' ? 'studio-nav active' : 'studio-nav'}
          onClick={() => { setExpanded(false); onSelect('tasks'); }} aria-current={page === 'tasks' ? 'page' : undefined}>
          <Icon name="tasks" />任务管理
        </button>
        <button className={page === 'media-library' ? 'studio-nav active' : 'studio-nav'}
          onClick={() => { setExpanded(false); onSelect('media-library'); }} aria-current={page === 'media-library' ? 'page' : undefined}>
          <Icon name="scene" />资产库
        </button>
        <button
          className={page === "ai" ? "studio-nav active" : "studio-nav"}
          onClick={() => {
            setExpanded(false);
            onSelect("ai");
          }}
          aria-current={page === "ai" ? "page" : undefined}
        >
          <Icon name="settings" />AI 配置
        </button>
      </nav>
      <p className="studio-sidebar-foot">{page === 'ai' || page === 'tasks' || page === 'media-library' ? '配置、任务与资产保存在服务端' : '内容保存在当前浏览器'}</p>
    </aside>
  );
}

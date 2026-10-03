import { Link } from 'react-router-dom';
import { Icon } from '../ui/Icon';
import { aiConfigPath } from '../../app/paths';

export type MainPage = "projects" | "assets" | "tasks" | "media-library" | "ai" | "account";
export function Sidebar({ page }: { page: MainPage }) {
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
        <Link to="/assets/character"
          className={page === "assets" ? "studio-nav active" : "studio-nav"}
          aria-current={page === "assets" ? "page" : undefined}>
          <Icon name="library" />素材库
        </Link>
        <Link to="/tasks/text" className={page === 'tasks' ? 'studio-nav active' : 'studio-nav'} aria-current={page === 'tasks' ? 'page' : undefined}>
          <Icon name="tasks" />任务管理
        </Link>
        <Link to="/media-library/image" className={page === 'media-library' ? 'studio-nav active' : 'studio-nav'} aria-current={page === 'media-library' ? 'page' : undefined}>
          <Icon name="scene" />资产库
        </Link>
        <Link to={aiConfigPath}
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

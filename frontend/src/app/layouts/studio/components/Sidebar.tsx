import { brand } from '@host/shared/branding';
import { settingsPath, skillsPath } from '@host/shared/navigation/paths';
import { Icon } from '@host/shared/ui/Icon';
import type { MainPage } from '@host/types/app/layouts/studio/components/Sidebar';
import { Link } from 'react-router-dom';
export function Sidebar({ page }: { page: MainPage }) {
  return (
    <aside className="studio-sidebar">
      <Link className="studio-brand" to="/projects" aria-label={`${brand.name} 首页`}>
        <img className="studio-brand-mark" src={brand.assets.mark} alt="" aria-hidden="true" />
        <span className="studio-brand-copy">
          <span title={brand.name}>{brand.name}</span>
          <small title={brand.tagline}>{brand.tagline}</small>
        </span>
      </Link>
      <nav aria-label="主导航">
        <Link
          to="/projects"
          className={page === 'projects' ? 'studio-nav active' : 'studio-nav'}
          aria-current={page === 'projects' ? 'page' : undefined}
        >
          <Icon name="folder" />
          项目管理
        </Link>
        <Link
          to="/media-library/all"
          className={page === 'media-library' ? 'studio-nav active' : 'studio-nav'}
          aria-current={page === 'media-library' ? 'page' : undefined}
        >
          <Icon name="scene" />
          资产库
        </Link>
        <Link
          to={skillsPath}
          className={page === 'skills' ? 'studio-nav active' : 'studio-nav'}
          aria-current={page === 'skills' ? 'page' : undefined}
        >
          <Icon name="library" />
          Skill 管理
        </Link>
        <Link
          to={settingsPath}
          className={page === 'settings' ? 'studio-nav active' : 'studio-nav'}
          aria-current={page === 'settings' ? 'page' : undefined}
        >
          <Icon name="settings" />
          配置
        </Link>
      </nav>
      <p className="studio-sidebar-foot">从故事出发，逐镜打磨。</p>
    </aside>
  );
}

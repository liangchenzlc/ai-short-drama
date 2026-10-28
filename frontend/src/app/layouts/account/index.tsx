import { Icon } from '@host/shared/ui/Icon';
import { Link } from 'react-router-dom';
export function AccountFrame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="identity-page">
      <section className="identity-form" aria-labelledby="account-title">
        <Link className="identity-brand" to="/projects">
          <Icon name="film" size={22} />
          <span>短剧工作台</span>
        </Link>
        <h1 id="account-title">{title}</h1>
        {children}
      </section>
    </main>
  );
}

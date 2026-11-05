import { brand } from '@host/shared/branding';
import { Link } from 'react-router-dom';
export function AccountFrame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="identity-page">
      <section className="identity-form" aria-labelledby="account-title">
        <Link className="identity-brand" to="/projects" aria-label={`${brand.name} 首页`}>
          <img src={brand.assets.lockup} alt={brand.name} width={240} />
        </Link>
        <h1 id="account-title">{title}</h1>
        {children}
      </section>
    </main>
  );
}

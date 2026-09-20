import { Link } from 'react-router-dom';

export function NotFoundPage({ message = '页面不存在，请检查地址或返回项目列表。' }: { message?: string }) {
  return <section className="studio-empty"><h1>没有找到内容</h1><p>{message}</p><Link to="/projects">返回项目管理</Link></section>;
}

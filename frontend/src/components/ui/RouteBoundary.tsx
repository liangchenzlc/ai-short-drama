import { Component, type ReactNode } from 'react';
import { Alert, Button } from 'antd';

export class RouteBoundary extends Component<{ resetKey: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: { resetKey: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }
  render() {
    if (this.state.failed) return <div className="route-loading"><Alert type="error" showIcon
      message="工作区未能加载" description="请检查网络连接，然后刷新页面。已保存的内容仍在服务端。"
      action={<Button onClick={() => window.location.reload()}>刷新页面</Button>}/></div>;
    return this.props.children;
  }
}

import { useEffect, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { projectsApi, projectError } from '../../api/modules/projects';

export function CanvasLaunch({ projectId, canvasId }: { projectId: string; canvasId: string | null }) {
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    if (!canvasId) { setError('项目暂时没有可打开的主画布，请返回项目列表重新载入。'); return; }
    void projectsApi.canvas(projectId, canvasId, controller.signal).then(canvas => {
      if (!controller.signal.aborted) window.location.replace(`/canvas-app/canvas/${encodeURIComponent(canvas.source_key)}`);
    }).catch(cause => { if (!controller.signal.aborted) setError(projectError(cause)); });
    return () => controller.abort();
  }, [projectId, canvasId, revision]);
  return error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setRevision(value => value + 1)}>重试</Button>} />
    : <div className="studio-empty" role="status"><Spin /> 正在打开无限画布…</div>;
}

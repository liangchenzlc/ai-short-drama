import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '@ant-design/v5-patch-for-react-19';
import { AssemblyResultPlayer } from '../src/features/projects/AssemblyResultPlayer';
import type { RenderJob } from '../src/api/modules/assembly';
import '../src/app/styles.css';
import '../src/app/assembly.css';
import '../src/app/workbench.css';

function Fixture() {
  const [job, setJob] = useState<RenderJob>({ id: '1', media_id: '2', status: 'succeeded', kind: 'preview',
    url: '/.runtime/timeline-fixture.mp4', duration_ms: 3000, context_hash: 'test', is_stale: false,
    stage: 'done', progress: 100, cancel_requested: false, created_at: '', finished_at: '', error: null });
  const [visible, setVisible] = useState(true), [frame, setFrame] = useState(0);
  const video = useRef<HTMLVideoElement | null>(null);
  return <main style={{ maxWidth: 900, padding: 16, margin: 'auto' }}>
    <h1>成片恢复验收 · 合成测试视频</h1>
    <button onClick={() => setVisible(false)}>离开播放器</button><output aria-label="当前帧">{frame}</output>
    <div className="assembly-editing">{visible && <AssemblyResultPlayer job={job} videoRef={video} aspect="16:9" stale={false}
      refresh={async signal => { const response = await fetch('/recovery/job', { signal }); if (!response.ok) throw new Error('refresh_failed'); return response.json(); }}
      onResult={setJob} onFrame={setFrame} onPlaying={() => {}} download="/.runtime/timeline-fixture.mp4" />}</div>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Fixture /></React.StrictMode>);

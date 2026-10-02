import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, theme } from 'antd';
import '@ant-design/v5-patch-for-react-19';
import type { AssemblyClip, AssemblyState, RenderJob } from '../src/api/modules/assembly';
import { AssemblyWorkbench } from '../src/features/projects/AssemblyWorkbench';
import type { useAssembly } from '../src/features/projects/useAssembly';
import '../src/app/styles.css';
import '../src/app/assembly.css';
import '../src/app/workbench.css';

const source: AssemblyClip = { id: 'source-a', shot_id: 'shot-a', media_id: 'new-video', position: 1,
  shot_position: 1, included: true, muted: false, trim_in_ms: 0, trim_out_ms: null, duration_ms: 3000,
  script: '测试分镜', url: '/.runtime/timeline-fixture.mp4', poster: '/.runtime/timeline-poster.jpg',
  filmstrip: null, is_stale: false, archived: false, issue: null };
const missing: AssemblyClip = { ...source, id: 'missing-clip', shot_id: 'shot-missing', media_id: null,
  shot_position: 3, position: 2, duration_ms: null, url: null, poster: null, issue: 'missing' };
const frozen: RenderJob = { id: 'frozen-job', kind: 'export', status: 'succeeded', stage: 'done', progress: 100,
  cancel_requested: false, error: null, created_at: '', finished_at: '', context_hash: 'frozen', media_id: 'frozen-output',
  url: '/.runtime/timeline-fixture.mp4', duration_ms: 1000, is_stale: true, aspect: '9:16',
  timeline: [{ clip_id: 'frozen-clip', shot_id: 'shot-a', media_id: 'old-video', trim_in_ms: 0, trim_out_ms: 1000,
    duration_ms: 3000, muted: false, poster: '/.runtime/timeline-filmstrip.jpg', url: '/.runtime/timeline-fixture.mp4' }] };

function Fixture() {
  const [value, setValue] = useState<AssemblyState>({ assembly: { id: 'fixture', row_version: '1', aspect: '16:9',
    resolution: '720p', current_media_id: null }, source_hash: 'fixture', context_hash: 'fixture',
    clips: [{ ...source, trim_out_ms: 1000 }, missing], sources: [source,
      { ...source, id: 'preparing', shot_id: 'shot-b', shot_position: 2, duration_ms: null, issue: 'preparing' }], jobs: [] });
  const [result, setResult] = useState<RenderJob | null>(null);
  const past = useRef<AssemblyState[]>([]), future = useRef<AssemblyState[]>([]);
  const editor = { value, status: 'saved', canUndo: past.current.length > 0, canRedo: future.current.length > 0,
    edit: (transform: (current: AssemblyState) => AssemblyState) => { past.current.push(value); future.current = []; setValue(transform(value)); },
    undo: () => { const before = past.current.pop(); if (before) { future.current.push(value); setValue(before); } },
    redo: () => { const next = future.current.pop(); if (next) { past.current.push(value); setValue(next); } },
    refreshMedia: async () => {}, api: { job: async () => frozen, download: () => '/.runtime/timeline-fixture.mp4' },
  } as unknown as ReturnType<typeof useAssembly>;
  return <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}><main style={{ padding: 16, maxWidth: 1200, margin: 'auto' }}>
    <h1>剪辑验收 · 本地合成视频</h1><button onClick={() => setResult(frozen)}>查看冻结成片</button>
    <output aria-label="草稿片段数量">{value.clips?.length}</output>
    <AssemblyWorkbench editor={editor} disabled={false} result={result} onResult={setResult} onPreview={() => {}} previewBusy={false}/>
  </main></ConfigProvider>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Fixture /></React.StrictMode>);

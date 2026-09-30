import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import '@ant-design/v5-patch-for-react-19';
import { studioTheme } from '../src/app/theme';
import { SoundPanel } from '../src/features/projects/SoundPanel';
import type { NavigationBarrier } from '../src/features/projects/writing-navigation';
import '../src/app/styles.css';
import '../src/app/studio.css';
import '../src/app/web.css';
import '../src/app/workbench.css';
function Fixture() {
  const [flushed, setFlushed] = useState(0);
  const navigation = useRef<NavigationBarrier | null>(null);
  const [unsettled, setUnsettled] = useState(false);
  const barrier = useCallback((value: NavigationBarrier | null) => { navigation.current = value; }, []);
  return <main style={{ padding: 24 }}><h1>声音制作验收</h1><output aria-label="视频保存次数">{flushed}</output><button onClick={() => setUnsettled(navigation.current?.hasUnsettled() ?? false)}>检查未保存状态</button><output aria-label="未保存状态">{String(unsettled)}</output><SoundPanel projectId="1" episodeId="2" disabled={location.search.includes('readonly')} flushVideo={async () => { setFlushed(v => v + 1); return true; }} onSaved={async () => {}} registerBarrier={barrier}/></main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}><MemoryRouter><Fixture/></MemoryRouter></ConfigProvider></React.StrictMode>);

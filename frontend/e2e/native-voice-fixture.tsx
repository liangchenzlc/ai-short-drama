import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import '@ant-design/v5-patch-for-react-19';
import { studioTheme } from '../src/app/theme';
import { CharacterVoicePanel, NativeDialoguePanel } from '../src/features/projects/NativeVoicePanel';
import type { NavigationBarrier } from '../src/features/projects/writing-navigation';
import '../src/app/styles.css';
import '../src/app/studio.css';
import '../src/app/web.css';
import '../src/app/workbench.css';
function Fixture() {
  const barrier = useRef<NavigationBarrier | null>(null);
  const register = useCallback((value: NavigationBarrier | null) => { barrier.current = value; }, []);
  const [revision, setRevision] = useState(0);
  return <main style={{ padding: 24, maxWidth: 960 }}><h1>角色声音与分镜对白</h1>
    <CharacterVoicePanel projectId="1" characterId="3" disabled={false}/>
    <NativeDialoguePanel projectId="1" episodeId="2" shotId="4" disabled={false} revision={revision} registerBarrier={register} prepare={async () => true} onChanged={() => setRevision(r => r + 1)}/>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}><MemoryRouter><Fixture/></MemoryRouter></ConfigProvider></React.StrictMode>);

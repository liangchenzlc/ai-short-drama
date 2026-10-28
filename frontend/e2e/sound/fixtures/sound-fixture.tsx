import '@host/app/styles/index.css';
import React, { useCallback, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '@ant-design/v5-patch-for-react-19';
import { StudioProvider } from '../../../src/shared/ui/StudioProvider';
import { StandardSaveStatus } from '../../../src/features/creation/shared/feedback/StandardSaveStatus';
import { SoundPanel } from '../../../src/features/creation/sound/tracks/SoundPanel';
import { ConfigCatalogProvider } from '../../../src/features/ai-config/catalog/ConfigCatalogProvider';
import type { NavigationBarrier } from '../../../src/types/creation/writing/sessions/writing-navigation';
function Fixture() {
  const [flushed, setFlushed] = useState(0);
  const navigation = useRef<NavigationBarrier | null>(null);
  const [unsettled, setUnsettled] = useState(false);
  const barrier = useCallback((value: NavigationBarrier | null) => { navigation.current = value; }, []);
  return <main style={{ padding: 24 }}><header className="creation-top"><h1>声音制作验收</h1><StandardSaveStatus/></header><output aria-label="视频保存次数">{flushed}</output><button onClick={() => setUnsettled(navigation.current?.hasUnsettled() ?? false)}>检查未保存状态</button><output aria-label="未保存状态">{String(unsettled)}</output><SoundPanel projectId="1" disabled={location.search.includes('readonly')} flushVideo={async () => { setFlushed(v => v + 1); return true; }} onSaved={async () => {}} registerBarrier={barrier}/></main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><StudioProvider><MemoryRouter><ConfigCatalogProvider><Fixture/></ConfigCatalogProvider></MemoryRouter></StudioProvider></React.StrictMode>);

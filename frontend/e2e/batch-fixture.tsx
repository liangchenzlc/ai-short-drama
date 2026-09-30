import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Checkbox, ConfigProvider } from 'antd';
import { studioTheme } from '../src/app/theme';
import '@ant-design/v5-patch-for-react-19';
import { BatchLauncher, useBatchSelection } from '../src/features/generations/BatchGeneration';
import '../src/app/styles.css';
import '../src/app/studio.css';
import '../src/app/web.css';
import '../src/app/workbench.css';

function Fixture() {
  const selection = useBatchSelection('episode:1:2');
  const [page, setPage] = useState(0), [saves, setSaves] = useState(0);
  const ids = page ? ['3', '4'] : ['1', '2'];
  return <main style={{ padding: 24 }}><h1>批量生成验收</h1>
    <button onClick={() => setPage(1 - page)}>切换列表页</button><output aria-label="保存次数">{saves}</output>
    <BatchLauncher scope={{ library: 'episode', project_id: '1', episode_id: '2' }} selection={selection} loadedIds={ids}
      beforePreflight={async () => { setSaves(n => n + 1); return true; }}/>
    {ids.map(id => <p key={id}><Checkbox checked={selection.ids.includes(id)} onChange={event => selection.toggle(id, event.target.checked)}>镜头 {id}</Checkbox></p>)}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}><MemoryRouter><Fixture/></MemoryRouter></ConfigProvider></React.StrictMode>);

import '@host/app/styles/index.css';
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Checkbox, ConfigProvider } from 'antd';
import { studioTheme } from '../../../src/shared/ui/theme';
import '@ant-design/v5-patch-for-react-19';
import { BatchLauncher, useBatchSelection } from '../../../src/features/generations/batches/BatchGeneration';
import { ConfigCatalogProvider } from '../../../src/features/ai-config/catalog/ConfigCatalogProvider';
import '../../../src/features/creation/workspace/layout/overlays.css';

function Fixture() {
  const selection = useBatchSelection('episode:1:2');
  const [page, setPage] = useState(0), [saves, setSaves] = useState(0), [filtered, setFiltered] = useState(false), [project, setProject] = useState('1');
  const ids = page ? ['3', '4'] : ['1', '2'];
  return <main style={{ padding: 24 }}><h1>批量生成验收</h1>
    <button onClick={() => setPage(1 - page)}>切换列表页</button><output aria-label="保存次数">{saves}</output>
    <button onClick={() => selection.setIds(Array.from({ length: 101 }, (_, index) => String(index + 1)))}>选择101项</button>
    <button onClick={() => setFiltered(true)}>筛选素材</button>
    <button onClick={() => setProject(value => value === '1' ? '2' : '1')}>切换作品</button>
    <BatchLauncher presentation="standard-creation" scope={{ library: 'project', project_id: '1' }} selection={selection} loadedIds={ids}
      compactScene="shot_image" beforePreflight={async () => { setSaves(n => n + 1); return true; }}/>
    <BatchLauncher presentation="standard-creation" scope={{ library: 'project', project_id: '1' }} selection={selection} loadedIds={ids}
      compactScene="shot_video" beforePreflight={async () => { setSaves(n => n + 1); return true; }}/>
    <BatchLauncher presentation="standard-creation" scope={{ library: 'project', project_id: project, asset_kind: 'character', search: filtered ? '角色' : undefined }} selection={selection} loadedIds={ids}
      compactScene="asset_image" beforePreflight={async () => { setSaves(n => n + 1); return true; }}/>
    <BatchLauncher presentation="standard-creation" scope={{ library: 'episode', project_id: '1', episode_id: '2' }} selection={selection} loadedIds={ids}
      beforePreflight={async () => { setSaves(n => n + 1); return true; }}/>
    {ids.map(id => <p key={id}><Checkbox checked={selection.ids.includes(id)} onChange={event => selection.toggle(id, event.target.checked)}>镜头 {id}</Checkbox></p>)}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider theme={studioTheme} button={{ autoInsertSpace: false }}><MemoryRouter><ConfigCatalogProvider><Fixture/></ConfigCatalogProvider></MemoryRouter></ConfigProvider></React.StrictMode>);

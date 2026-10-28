import '@host/app/styles/index.css';
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import '@ant-design/v5-patch-for-react-19';
import { Button } from 'antd';
import { StudioProvider } from '../../../src/shared/ui/StudioProvider';
import { StudioFeedback } from '../../../src/shared/ui/StudioFeedback';
import { StandardSaveStatus } from '../../../src/features/creation/shared/feedback/StandardSaveStatus';
import { Dialog } from '../../../src/shared/ui/Dialog';
import { confirmAction } from '../../../src/shared/ui/confirm';

function Fixture() {
  const [kind, setKind] = useState(''), [open, setOpen] = useState(false), [dialogError, setDialogError] = useState(false);
  return <div style={{ padding: 12 }}>
    <header style={{ height: 48 }}><StandardSaveStatus/></header>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
      <Button onClick={() => setKind('error')}>触发报错</Button>
      <Button onClick={() => setKind('warning')}>触发警告</Button>
      <Button onClick={() => setKind('progress')}>后台准备</Button>
      <Button onClick={() => { setKind(''); setOpen(true); }}>打开操作弹窗</Button>
    </div>
    <main style={{ height: 400, display: 'flex', flexDirection: 'column' }}>
      {kind === 'error' && <StudioFeedback title="素材上传" type="error" message="图片上传失败，请检查网络后重试。" actions={<><Button onClick={() => setKind('')}>重试原操作</Button><Button onClick={() => void confirmAction('核对当前操作。', { title: '保存详情确认' })}>保存详情内确认</Button></>}/>}
      {kind === 'warning' && <StudioFeedback type="warning" message="生成请求尚未确认。" actions={<Button onClick={() => setKind('')}>核对原请求</Button>}/>}
      <textarea aria-label="原始编辑器" defaultValue="保留当前剧本输入" style={{ flex: 1, minHeight: 0, width: '100%' }}/>
    </main>
    {open && <Dialog title="反馈层级验收" onClose={() => setOpen(false)}>
      <div style={{ width: 'min(420px, calc(100vw - 72px))', height: 260 }}>
        <textarea aria-label="弹窗编辑器" defaultValue="保留弹窗输入" style={{ width: '100%', height: 170 }}/>
        <Button onClick={() => setDialogError(true)}>弹窗上传</Button>
        {dialogError && <StudioFeedback type="error" message="弹窗图片上传失败。" actions={<><Button onClick={() => setDialogError(false)}>重试弹窗上传</Button><Button onClick={() => void confirmAction('核对弹窗操作。', { title: '弹窗详情确认' })}>弹窗详情内确认</Button></>}/>}
      </div>
    </Dialog>}
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><StudioProvider><BrowserRouter><Fixture/></BrowserRouter></StudioProvider></React.StrictMode>);

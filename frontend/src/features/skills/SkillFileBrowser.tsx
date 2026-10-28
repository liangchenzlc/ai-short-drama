import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Skeleton, Splitter, Tree } from 'antd';
import { Copy } from 'lucide-react';
import { errorMessage } from '../../api/http';
import { useStudioMessage } from '../../components/ui/StudioFeedback';
import type { SkillResourceDraft } from './skill-draft';
import { instructionBody, skillFileTree } from './skill-model';
import { readSkillResourceText } from './skill-resource';

const SkillCodeViewer = lazy(() => import('./SkillCodeViewer'));
export function SkillFileBrowser({ identity, skillId, instructions, resources }: {
  identity: string; skillId?: string; instructions: string; resources: SkillResourceDraft[];
}) {
  return <FileBrowser key={identity} skillId={skillId} instructions={instructions} resources={resources}/>;
}
function FileBrowser({ skillId, instructions, resources }: Omit<Parameters<typeof SkillFileBrowser>[0], 'identity'>) {
  const [selected, setSelected] = useState('SKILL.md');
  const [expanded, setExpanded] = useState<React.Key[]>([]);
  const [result, setResult] = useState<{ path: string; content: string } | null>(null);
  const [issue, setIssue] = useState('');
  const [revision, setRevision] = useState(0);
  const cache = useRef(new Map<string, string>());
  const positions = useRef(new Map<string, { top: number; left: number }>());
  const message = useStudioMessage();
  const tree = useMemo(() => skillFileTree(['SKILL.md', ...resources.map(item => item.path)]), [resources]);
  const resource = resources.find(item => item.path === selected);
  const immediate = selected === 'SKILL.md' ? instructionBody(instructions) : resource?.content;
  const content = immediate ?? (result?.path === selected ? result.content : null);
  useEffect(() => {
    setIssue('');
    if (immediate !== null && immediate !== undefined || !resource || !skillId) return;
    const key = `${resource.origin_version}:${resource.path}:${resource.checksum_sha256}`;
    const cached = cache.current.get(key);
    if (cached !== undefined) { setResult({ path: selected, content: cached }); return; }
    const controller = new AbortController();
    readSkillResourceText(skillId, resource, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      cache.current.set(key, value); setResult({ path: selected, content: value });
    }).catch(cause => { if (!controller.signal.aborted) setIssue(errorMessage(cause)); });
    return () => controller.abort();
  }, [selected, immediate, resource, skillId, revision]);
  const directory = <div className="skill-directory"><Tree.DirectoryTree aria-label="Skill 文件目录"
    treeData={tree} selectedKeys={[selected]} expandedKeys={expanded} expandAction="click"
    onExpand={setExpanded} onSelect={(_, info) => {
      if (!info.node.isLeaf) return;
      setSelected(String(info.node.key)); setIssue('');
    }} titleRender={node => <span title={String(node.key)}>{node.title}</span>}/></div>;
  const viewer = <div className="skill-file-viewer">
    <div className="skill-file-bar">
      <span className="skill-file-path" title={selected}>{selected}</span><span className="skill-readonly">只读</span>
      {resource?.kind !== 'image' && <Button type="text" size="small" aria-label="复制文件内容" icon={<Copy size={14}/>}
        disabled={content === null} onClick={() => {
          void navigator.clipboard.writeText(content ?? '').then(() => message.success('已复制'), cause => message.error(errorMessage(cause)));
        }}/ >}
    </div>
    <div className="skill-file-content" key={selected}>
      {issue ? <div className="skill-file-state" role="alert"><p>读取失败：{issue}</p><Button onClick={() => { setResult(null); setRevision(value => value + 1); }}>重试</Button></div>
        : content === null || content === undefined ? <div className="skill-file-state" role="status" aria-label="正在读取文件"><Skeleton active/></div>
        : resource?.kind === 'image' ? <div className="skill-image-preview"><img alt={selected} src={`data:image/${/\.png$/i.test(selected) ? 'png' : /\.webp$/i.test(selected) ? 'webp' : 'jpeg'};base64,${content}`}/></div>
        : <Suspense fallback={<div className="skill-file-state" role="status" aria-label="正在加载只读区域"><Skeleton active/></div>}>
          <SkillCodeViewer path={selected} content={content} scroll={positions.current.get(selected) ?? { top: 0, left: 0 }}
            onScroll={position => positions.current.set(selected, position)}/>
        </Suspense>}
    </div>
  </div>;
  return <div className="skill-file-browser"><Splitter><Splitter.Panel defaultSize={230} min={160} max="40%">{directory}</Splitter.Panel><Splitter.Panel min={200}>{viewer}</Splitter.Panel></Splitter></div>;
}

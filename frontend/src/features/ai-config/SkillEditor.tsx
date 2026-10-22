import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox } from 'antd';
import { ArrowLeft, Download, Save } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { agentsApi } from '../../api/modules/agents';
import type { AgentSkillContent, AgentSkillDetail } from '../../api/types/agents';
import { ApiError, errorMessage } from '../../api/http';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { SkillResources, readSkillResourceText } from './SkillResources';
import { createSkillDraft, downloadSkillText, skillContent, skillDraftIssue, type SkillDraft, type SkillFormValues, type SkillSubmission } from './skill-draft';

async function matchesSubmission(detail: AgentSkillDetail, body: AgentSkillContent): Promise<boolean> {
  const fields = ['name', 'slug', 'description', 'instructions', 'tags', 'modes', 'exclusions', 'required_tools', 'source'] as const;
  if (fields.some(field => JSON.stringify(detail[field]) !== JSON.stringify(body[field]))) return false;
  if (detail.resources.length !== body.resources?.length) return false;
  for (const resource of body.resources ?? []) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(resource.content));
    const checksum = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (!detail.resources.some(item => item.path === resource.path && item.kind === resource.kind && item.checksum_sha256 === checksum)) return false;
  }
  return true;
}

export function SkillEditor({ draft, readOnly, storageIssue, onDraft, onBack, onSaved, onBusy, onEdit, onCopy }: {
  draft: SkillDraft; readOnly: boolean; storageIssue: string;
  onDraft: (value: SkillDraft, settled?: boolean) => boolean;
  onBack: () => void; onSaved: (detail: AgentSkillDetail) => void; onBusy: (busy: boolean) => void;
  onEdit: () => void; onCopy: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState('');
  const [latest, setLatest] = useState<AgentSkillDetail | null>(null);
  const [preview, setPreview] = useState(false);
  const lock = useRef(false); const live = useRef(true); const current = useRef(draft); current.current = draft;
  const values = draft.values;
  const [tagsText, setTagsText] = useState(values.tags.join('，'));
  const [toolsText, setToolsText] = useState(values.required_tools.join('\n'));
  const blocked = busy || !!draft.pending;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => { setTagsText(draft.values.tags.join('，')); setToolsText(draft.values.required_tools.join('\n')); }, [draft.key, draft.base?.row_version]);
  function markBusy(value: boolean) { lock.current = value; if (live.current) { setBusy(value); onBusy(value); } }
  function change(patch: Partial<SkillFormValues>) {
    if (!blocked && !readOnly) onDraft({ ...draft, changed: true, values: { ...values, ...patch } });
  }
  function failed(cause: unknown, submitted: SkillDraft) {
    if (!live.current) return;
    const conflict = cause instanceof ApiError && cause.status === 409;
    const unknown = !(cause instanceof ApiError) || !cause.status || cause.status === 408 || cause.status >= 500;
    if (conflict) {
      onDraft({ ...submitted, pending: null, conflict: true }, true);
      setIssue('Skill 已在其他窗口更新。草稿仍在，请读取服务端版本后比较。');
    } else if (unknown) setIssue('保存结果待核对。原请求与草稿已保留，请使用原请求核对保存。');
    else {
      onDraft({ ...submitted, pending: null }, true);
      setIssue(`Skill 未保存：${errorMessage(cause)}`);
    }
  }
  async function send(submitted: SkillDraft) {
    const pending = submitted.pending!;
    try {
      let saved: AgentSkillDetail;
      if (submitted.base) {
        const { enabled: _enabled, invocation_mode: _invocation, ...content } = pending.body;
        saved = await agentsApi.updateSkill(submitted.base.id, { ...content, row_version: pending.row_version! });
      } else saved = await agentsApi.createSkill(pending.body, pending.key);
      if (live.current) onSaved(saved);
    } catch (cause) { failed(cause, submitted); }
  }
  async function save() {
    if (lock.current || readOnly || draft.pending || draft.conflict) return;
    const issue = skillDraftIssue(values);
    if (issue) { setIssue(issue); return; }
    markBusy(true); setIssue('');
    try {
      const resources = [];
      for (const resource of values.resources) {
        const content = resource.content ?? await readSkillResourceText(draft.base!.id, resource);
        resources.push({ ...resource, content });
      }
      if (!live.current) return;
      const fullValues = { ...values, resources };
      const pending: SkillSubmission = { key: draft.key, body: skillContent(fullValues), row_version: draft.base?.row_version ?? null };
      const submitted = { ...draft, values: fullValues, pending };
      if (!onDraft(submitted)) { setIssue('原请求尚未可靠保存到浏览器，未发送。请下载草稿或恢复浏览器存储后重试。'); return; }
      await send(submitted);
    } catch (cause) { if (live.current) setIssue(`准备资料失败，未发布版本：${errorMessage(cause)}`); }
    finally { markBusy(false); }
  }
  async function reconcile() {
    if (lock.current || !draft.pending) return;
    markBusy(true); setIssue('');
    try {
      if (!onDraft(draft)) { setIssue('原请求尚未可靠保存到浏览器，未发送。请下载草稿或恢复浏览器存储后重试。'); return; }
      if (draft.base) {
        const server = await agentsApi.skill(draft.base.id);
        if (!live.current) return;
        if (await matchesSubmission(server, draft.pending.body)) { onSaved(server); return; }
        if (server.row_version !== draft.pending.row_version) {
          onDraft({ ...draft, pending: null, conflict: true }, true); setLatest(server);
          setIssue('服务端内容与原请求不同，草稿已保留。请比较后明确选择如何继续。'); return;
        }
      }
      await send(draft);
    } catch (cause) { if (live.current) setIssue(`原请求仍未核对完成：${errorMessage(cause)}`); }
    finally { markBusy(false); }
  }
  async function readLatest() {
    if (lock.current || !draft.base) return;
    markBusy(true);
    try { const value = await agentsApi.skill(draft.base.id); if (live.current) setLatest(value); }
    catch (cause) { if (live.current) setIssue(`读取服务端版本失败：${errorMessage(cause)}`); }
    finally { markBusy(false); }
  }
  async function useLatest(keepDraft: boolean) {
    if (!latest || busy || !await confirmAction(keepDraft
      ? '将保留当前草稿的全部内容与资料目录，基于已显示的服务端版本继续编辑。确认核对完差异？'
      : '放弃当前尚未保存的修改，载入已显示的服务端版本？', { title: '核对 Skill 版本', confirmText: keepDraft ? '保留草稿继续' : '载入服务端版本', danger: !keepDraft })) return;
    const next = keepDraft ? { ...draft, base: latest, conflict: false, pending: null, changed: true } : createSkillDraft(latest);
    if (onDraft(next, true)) { setLatest(null); setIssue(''); }
  }
  const warning = draft.pending ? '保存结果待核对。请保留原请求并完成核对。' : draft.conflict ? '存在版本冲突，当前草稿没有被覆盖。' : '';
  return <section className="skill-editor" aria-labelledby="skill-editor-title">
    <div className="skill-editor-heading"><Button type="text" icon={<ArrowLeft size={16}/>} disabled={busy} onClick={onBack}>返回列表</Button>
      <div><h2 id="skill-editor-title">{readOnly ? draft.base?.name : draft.base ? '编辑 Skill' : '新建 Skill'}</h2>
        <p>{draft.base ? `${draft.base.builtin ? '内置原件' : '我的 Skill'} · 内容版本 ${draft.base.content_version}` : '保存后加入你的技能库，仅在任务需要时使用。'}</p></div>
      <div className="skill-actions">{readOnly && (draft.base?.builtin ? <Button onClick={onCopy} disabled={busy}>复制为我的 Skill</Button> : <Button onClick={onEdit}>编辑此 Skill</Button>)}</div>
    </div>
    {(issue || warning || storageIssue) && <StudioFeedback type={draft.pending || draft.conflict || storageIssue ? 'warning' : 'error'}
      message={issue || warning || storageIssue} noticeKey={`skill-editor:${draft.key}`} persistent
      description={<><p>{storageIssue}</p>{draft.pending && <details><summary>查看原保存请求</summary><pre>{JSON.stringify(draft.pending, null, 2)}</pre></details>}</>}
      actions={<><Button disabled={busy} onClick={() => downloadSkillText('skill-draft.json', JSON.stringify(draft, null, 2))}>下载草稿</Button>
        {draft.pending && <Button disabled={busy} onClick={() => void reconcile()}>使用原请求核对保存</Button>}
        {draft.conflict && <Button disabled={busy} onClick={() => void readLatest()}>读取服务端版本</Button>}
        {storageIssue && <Button disabled={busy} onClick={() => onDraft(draft)}>重试保存草稿</Button>}</>}/>}
    <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <div className="skill-editor-grid">
        <fieldset className="skill-basic-fields" disabled={blocked}>
          <legend>基本信息</legend>
          <label>显示名称<input autoFocus={!readOnly} value={values.name} readOnly={readOnly} maxLength={120} required onChange={event => change({ name: event.target.value })}/></label>
          <label>用途描述<textarea value={values.description} readOnly={readOnly} rows={3} maxLength={2000} required
            placeholder="在什么任务中使用，能产出什么。" onChange={event => change({ description: event.target.value })}/></label>
          <div><span className="skill-field-label" id="skill-mode-label">适用模式</span><div className="skill-mode-options" role="group" aria-labelledby="skill-mode-label">
            {(['standard', 'infinite_canvas'] as const).map(mode => <Checkbox key={mode} checked={values.modes.includes(mode)} disabled={readOnly || blocked}
              onChange={event => change({ modes: event.target.checked ? [...values.modes, mode] : values.modes.filter(item => item !== mode) })}>{mode === 'standard' ? '标准创作' : '无限画布'}</Checkbox>)}
          </div></div>
          {!draft.base && <div className="skill-new-settings"><Checkbox checked={values.enabled} onChange={event => change({ enabled: event.target.checked })}>启用此 Skill</Checkbox>
            <label>调用方式<select value={values.invocation_mode} onChange={event => change({ invocation_mode: event.target.value as 'auto' | 'manual' })}>
              <option value="auto">按需自动</option><option value="manual">仅手动</option></select></label></div>}
          <details className="skill-advanced"><summary>标签、适用条件与来源</summary>
            <label>格式标识<input value={values.slug} readOnly={readOnly} maxLength={64} onChange={event => change({ slug: event.target.value })}/></label>
            <label>标签（用逗号分隔）<input value={tagsText} readOnly={readOnly} onChange={event => { setTagsText(event.target.value); change({ tags: event.target.value.split(/[,，]/).map(item => item.trim()).filter(Boolean) }); }}/></label>
            <label>排除场景<textarea rows={2} value={values.exclusions} readOnly={readOnly} maxLength={2000} onChange={event => change({ exclusions: event.target.value })}/></label>
            <label>需要的平台工具（每行一个）<textarea rows={2} value={toolsText} readOnly={readOnly}
              onChange={event => { setToolsText(event.target.value); change({ required_tools: event.target.value.split('\n').map(item => item.trim()).filter(Boolean) }); }}/></label>
            <label>来源与适配说明<textarea rows={3} value={values.source} readOnly={readOnly} maxLength={4000} onChange={event => change({ source: event.target.value })}/></label>
          </details>
        </fieldset>
        <section className="skill-instructions" aria-labelledby="skill-instructions-title">
          <div className="skill-section-heading"><div><h3 id="skill-instructions-title">主指令</h3><p>写清必要输入、方法、输出与自检。长资料放在附加文件。</p></div>
            <Button type="text" aria-pressed={preview} onClick={() => setPreview(value => !value)}>{preview ? '返回正文' : '预览'}</Button></div>
          {preview ? <div className="skill-markdown" aria-label="主指令预览"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]}
            components={{ a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>{alt || '图片链接'}</span> }}>{values.instructions}</ReactMarkdown></div>
            : <label className="skill-instruction-input"><span className="sr-only">Markdown 主指令</span><textarea rows={18} value={values.instructions}
              readOnly={readOnly} disabled={blocked} required maxLength={65536} placeholder="说明如何处理用户提供或主动选入的内容…"
              onChange={event => change({ instructions: event.target.value })}/></label>}
        </section>
      </div>
      <SkillResources skillId={draft.base?.id} resources={values.resources} readOnly={readOnly} disabled={blocked}
        onChange={resources => change({ resources })} onCache={(resource, content) => {
          const value = current.current;
          onDraft({ ...value, values: { ...value.values, resources: value.values.resources.map(item => item.path === resource.path
            && item.origin_version === resource.origin_version && item.content === null ? { ...item, content } : item) } });
        }}/>
      {latest && <section className="skill-server-comparison" aria-label="服务端版本比较">
        <div className="skill-section-heading"><div><h3>服务端内容版本 {latest.content_version}</h3><p>当前编辑区保留你的草稿。核对名称、用途、正文与文件目录后再选择。</p></div></div>
        <dl><dt>名称</dt><dd>{latest.name}</dd><dt>用途</dt><dd>{latest.description}</dd><dt>资料</dt><dd>{latest.resources.map(item => item.path).join('、') || '无附加资料'}</dd></dl>
        <pre>{latest.instructions}</pre><div className="skill-actions"><Button disabled={busy} onClick={() => void useLatest(true)}>保留草稿，基于此版本继续</Button>
          <Button disabled={busy} onClick={() => void useLatest(false)}>放弃草稿并载入服务端</Button></div>
      </section>}
      <footer className="skill-editor-footer"><span role="status">{busy ? '正在核对并保存…' : draft.pending ? '保存结果待核对' : draft.conflict ? '版本冲突 · 草稿已保留'
        : readOnly ? '查看不会激活 Skill' : draft.changed ? '有未保存修改' : '当前内容已保存'}</span><div className="skill-actions">
        {!readOnly && <Button icon={<Download size={15}/>} disabled={busy} onClick={() => downloadSkillText('skill-draft.json', JSON.stringify(draft, null, 2))}>下载草稿</Button>}
        {draft.pending ? <Button key="reconcile" type="primary" aria-label="使用原请求核对保存" aria-busy={busy} loading={busy} onClick={() => void reconcile()}>使用原请求核对保存</Button>
          : draft.conflict ? <Button key="compare" type="primary" aria-label="读取服务端版本" aria-busy={busy} loading={busy} onClick={() => void readLatest()}>读取服务端版本</Button>
          : !readOnly && <Button key="save" type="primary" htmlType="submit" aria-label="保存 Skill" aria-busy={busy} icon={<Save size={15}/>} loading={busy} disabled={!draft.changed || busy}>保存 Skill</Button>}
      </div></footer>
    </form>
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Drawer, Form, Input, InputNumber, Select, Spin } from 'antd';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/http';
import { mediaLibrary } from '../../api/modules/media-library';
import { storyboardApi } from '../../api/modules/storyboard';
import { assetLibraries } from '../../api/modules/assets';
import type { ApplyAssetRequest, MediaAsset } from '../../api/types/generations';
import { isServerId } from '../generations/attempt';
import { dateLabel, generationError } from '../generations/presentation';

interface ApplyValues {
  type: ApplyAssetRequest['target']['type']; id: string; expected_media_id?: string;
  expected_row_version?: string; expected_context_hash?: string; acknowledge_stale_source?: boolean;
  project_id?: string; episode_id?: string;
  layout?: 'single' | 'four' | 'five' | 'nine'; aspect?: string; resolution?: string; duration?: number; confirm_shared?: boolean;
}
const idRule = { validator: (_: unknown, value?: string) => !value || isServerId(value.trim()) ? Promise.resolve() : Promise.reject(new Error('请输入真实服务端的正整数 ID')) };
export function AssetDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [asset, setAsset] = useState<MediaAsset | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [previewError, setPreviewError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState('');
  const [nameConflict, setNameConflict] = useState(false);
  const [busy, setBusy] = useState<'rename' | 'apply' | null>(null);
  const [applyOpen, setApplyOpen] = useState(false);
  const [applyError, setApplyError] = useState('');
  const [applyConflict, setApplyConflict] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [targetLoaded, setTargetLoaded] = useState(false);
  const [targetPreview, setTargetPreview] = useState<{ name: string; url?: string | null } | null>(null);
  const [notice, setNotice] = useState('');
  const [form] = Form.useForm<ApplyValues>();
  const targetType = Form.useWatch('type', form);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(true);
  const mutation = useRef(false);
  const savedName = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort;
    setLoading(true); setError('');
    if (!isServerId(id)) { setError('资产 ID 格式不正确。'); setLoading(false); return; }
    mediaLibrary.detail(id, abort.signal).then((value) => {
      if (abort.signal.aborted) return;
      setAsset(value); setPreviewError(false);
      if (!savedName.current) { setName(value.name); savedName.current = true; }
    }).catch((cause) => { if (!abort.signal.aborted) setError(generationError(cause)); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [id, revision]);
  function refresh() { controller.current?.abort(); setRevision((value) => value + 1); }
  async function rename() {
    if (!asset || !name.trim() || mutation.current || nameConflict) return;
    mutation.current = true; setBusy('rename'); setNameError(''); setNotice(''); controller.current?.abort();
    try {
      const value = await mediaLibrary.rename(id, name.trim(), asset.row_version);
      if (active.current) { setAsset(value); setName(value.name); setNotice('资产名称已更新。'); onChanged(); }
    } catch (cause) {
      if (active.current) { setNameError(generationError(cause)); setNameConflict(cause instanceof ApiError && cause.status === 409); }
    } finally { mutation.current = false; if (active.current) setBusy(null); }
  }
  async function apply(values: ApplyValues, acknowledgeStaleSource = false) {
    if (!asset || !confirmed || applyConflict || mutation.current || (values.type !== 'shot_video' && !targetLoaded)) return;
    mutation.current = true; setBusy('apply'); setApplyError(''); setNotice('');
    try {
      const parameters = {
        ...(values.type === 'shot_image' && values.layout ? { layout: values.layout } : {}),
        ...(values.type !== 'asset_image' && values.aspect?.trim() ? { aspect: values.aspect.trim() } : {}),
        ...(values.type !== 'asset_image' && values.resolution?.trim() ? { resolution: values.resolution.trim() } : {}),
        ...(values.type === 'shot_video' && values.duration != null ? { duration: values.duration } : {}),
      };
      const request: ApplyAssetRequest = { target: { type: values.type, id: values.id.trim() }, expected_media_id: values.expected_media_id?.trim() || null,
        ...(values.type === 'shot_image' || values.type === 'asset_image' ? { expected_row_version: values.expected_row_version?.trim() } : {}),
        ...(values.type === 'shot_image' ? { expected_context_hash: values.expected_context_hash?.trim(), acknowledge_stale_source: acknowledgeStaleSource } : {}),
        ...(Object.keys(parameters).length ? { parameters } : {}), ...(values.type === 'asset_image' ? { confirm_shared: values.confirm_shared === true } : {}),
      };
      await mediaLibrary.apply(id, request);
      if (active.current) { setNotice('已确认采用到指定目标。生成任务和资产仍保留。'); setApplyOpen(false); setConfirmed(false); onChanged(); }
    } catch (cause) {
      if (active.current) {
        if (cause instanceof ApiError && cause.code === 'stale_generation_source' && !acknowledgeStaleSource
          && window.confirm('此图片来自旧创作上下文。你已核对当前目标名称与图片，仍要采用吗？')) {
          mutation.current = false; setBusy(null); return void apply(values, true);
        }
        const conflict = cause instanceof ApiError && cause.status === 409;
        setApplyConflict(conflict);
        setApplyError(conflict ? '目标的当前媒体已变化。请重新核对目标及当前媒体 ID，不能直接覆盖。' : generationError(cause));
      }
    } finally { mutation.current = false; if (active.current) setBusy(null); }
  }
  async function inspectTarget() {
    const values = form.getFieldsValue();
    if (!values.type || !values.id?.trim() || mutation.current) return;
    mutation.current = true; setBusy('apply'); setApplyError(''); setTargetLoaded(false); setTargetPreview(null);
    try {
      if (values.type === 'shot_image') {
        if (!values.project_id?.trim() || !values.episode_id?.trim()) { setApplyError('读取分镜目标需要项目 ID 和分集 ID。'); return; }
        const result = await storyboardApi(values.project_id.trim(), values.episode_id.trim()).shot(values.id.trim());
        form.setFieldsValue({ expected_media_id: result.shot.image?.media_id ?? undefined, expected_row_version: result.shot.row_version, expected_context_hash: result.shot.context_hash });
        setTargetPreview({ name: `分镜 ${result.shot.position}`, url: result.shot.image?.url }); setTargetLoaded(true);
      } else if (values.type === 'asset_image') {
        const target = await assetLibraries.detail(values.id.trim());
        form.setFieldsValue({ expected_media_id: target.media_id ?? undefined, expected_row_version: target.row_version });
        setTargetPreview({ name: target.name, url: target.image?.url }); setTargetLoaded(true);
      }
    } catch (cause) { setApplyError(generationError(cause)); }
    finally { mutation.current = false; if (active.current) setBusy(null); }
  }
  return <Drawer open title="资产详情" width={760} onClose={() => !busy && onClose()} closable={!busy} maskClosable={!busy} keyboard={!busy} rootClassName="generation-drawer">
    <div className="generation-detail-toolbar"><span className="generation-id">{id}</span><Button onClick={refresh} loading={loading} disabled={!!busy}>刷新访问链接</Button></div>
    {error && <Alert type="error" showIcon message={error} />}
    {notice && <Alert type="success" showIcon message={notice} />}
    {loading && !asset ? <div className="generation-loading"><Spin tip="加载资产…"><div /></Spin></div> : asset && <>
      <div className="asset-detail-preview">
        {asset.url && !previewError ? asset.media_type === 'image' ? <img src={asset.url} alt={asset.name} onError={() => setPreviewError(true)} /> : <video src={asset.url} controls preload="metadata" playsInline onError={() => setPreviewError(true)} />
          : <div className="asset-preview-unavailable"><p>{previewError ? '预览链接已失效或文件暂时不可访问' : '暂时没有可用的预览链接'}</p><Button onClick={refresh}>刷新访问链接</Button></div>}
      </div>
      <section className="generation-section"><h3>资产信息</h3>
        <form className="asset-rename" onSubmit={(event) => { event.preventDefault(); void rename(); }}><label htmlFor="asset-name">资产名称</label><div><Input id="asset-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={255} disabled={!!busy || loading || nameConflict} required /><Button htmlType="submit" loading={busy === 'rename'} disabled={!!busy || loading || nameConflict || !name.trim() || name.trim() === asset.name}>保存名称</Button></div></form>
        {nameError && <Alert type="error" showIcon message={nameError} action={nameConflict ? <Button onClick={() => { savedName.current = false; setNameConflict(false); setNameError(''); refresh(); }}>重新加载名称</Button> : undefined} />}
        <dl className="generation-facts"><div><dt>媒体 ID</dt><dd className="generation-id">{asset.media_id}</dd></div><div><dt>尺寸</dt><dd>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : '未提供'}</dd></div>{asset.media_type === 'video' && <div><dt>时长</dt><dd>{asset.duration_ms != null ? `${asset.duration_ms / 1000} 秒` : '未提供'}</dd></div>}<div><dt>入库时间</dt><dd>{dateLabel(asset.created_at)}</dd></div><div><dt>来源任务</dt><dd><Link to={`/tasks/${asset.media_type}?task=${asset.generation_id}`}>{asset.generation_id}</Link></dd></div>{asset.source?.scene === 'shot_image' && <div><dt>来源分镜</dt><dd className="generation-id">{asset.source.shot_id}</dd></div>}</dl>
      </section>
      <section className="generation-section"><h3>采用到业务目标</h3><p className="generation-hint">资产已永久保存。请先读取目标详情，核对目标名称与当前图片，再明确确认采用。</p>
        {!applyOpen ? <Button type="primary" disabled={!!busy} onClick={() => { setApplyOpen(true); setConfirmed(false); setApplyError(''); form.setFieldsValue({ type: asset.media_type === 'video' ? 'shot_video' : 'shot_image' }); }}>选择目标并确认采用</Button> :
          <Form form={form} layout="vertical" onFinish={apply} disabled={!!busy} onValuesChange={(changed) => { setConfirmed(false); setApplyConflict(false); if (Object.keys(changed).some((key) => ['type', 'id', 'project_id', 'episode_id'].includes(key))) { setTargetLoaded(false); setTargetPreview(null); } }}>
            <div className="generation-form-grid"><Form.Item name="type" label="目标类型" rules={[{ required: true }]}><Select options={asset.media_type === 'video' ? [{ value: 'shot_video', label: '分镜视频' }] : [{ value: 'shot_image', label: '分镜图片' }, { value: 'asset_image', label: '角色 / 场景 / 道具素材图片' }]} /></Form.Item>
              <Form.Item name="id" label="目标的真实服务端 ID" rules={[{ required: true, message: '请输入目标 ID' }, idRule]}><Input placeholder="分镜 ID 或素材 ID" /></Form.Item></div>
            {targetType === 'shot_image' && <div className="generation-form-grid"><Form.Item name="project_id" label="项目 ID" rules={[{ required: true, message: '请输入项目 ID' }, idRule]}><Input/></Form.Item><Form.Item name="episode_id" label="分集 ID" rules={[{ required: true, message: '请输入分集 ID' }, idRule]}><Input/></Form.Item></div>}
            {(targetType === 'shot_image' || targetType === 'asset_image') && <><Button onClick={() => void inspectTarget()} loading={busy === 'apply'}>读取并核对目标详情</Button>{targetLoaded && targetPreview && <Alert type="success" showIcon message={`已读取目标：${targetPreview.name}`} description={targetPreview.url ? <img src={targetPreview.url} alt="目标当前图片" style={{ maxWidth: 280, maxHeight: 180, objectFit: 'contain' }}/> : '目标当前没有图片'}/>}</>}
            {targetType === 'shot_video' ? <Form.Item name="expected_media_id" label="目标当前媒体 ID（无媒体则留空）" rules={[idRule]}><Input /></Form.Item> : <Form.Item name="expected_media_id" hidden><Input /></Form.Item>}
            {(targetType === 'shot_image' || targetType === 'asset_image') && <Form.Item name="expected_row_version" hidden rules={[{ required: true }]}><Input /></Form.Item>}
            {targetType === 'shot_image' && <Form.Item name="expected_context_hash" hidden rules={[{ required: true, pattern: /^[0-9a-f]{64}$/ }]}><Input /></Form.Item>}
            {targetType !== 'asset_image' && <><p className="generation-hint">以下业务参数优先沿用生成快照；快照缺少时必须明确填写。不会自动裁剪或转码。</p><div className="generation-form-grid">
              {targetType === 'shot_image' && <Form.Item name="layout" label="图片布局（按需补充）"><Select allowClear placeholder="沿用生成快照" options={[{ value: 'single', label: '单图' }, { value: 'four', label: '四宫格' }, { value: 'five', label: '五宫格' }, { value: 'nine', label: '九宫格' }]} /></Form.Item>}
              <Form.Item name="aspect" label="画面比例（按需补充）"><Select allowClear placeholder="沿用生成快照" options={['16:9', '9:16', '1:1', '4:3', '3:4'].map((value) => ({ value, label: value }))} /></Form.Item><Form.Item name="resolution" label="分辨率（按需补充）"><Input maxLength={32} placeholder="例如 2K / 1080p" /></Form.Item>
              {targetType === 'shot_video' && <Form.Item name="duration" label="时长（毫秒，按需补充）"><InputNumber min={1} precision={0} step={1000} /></Form.Item>}
            </div></>}
            {targetType === 'asset_image' && <Form.Item name="confirm_shared" valuePropName="checked"><Checkbox>我确认共享素材的图片变化会影响引用此素材的项目。</Checkbox></Form.Item>}
            {applyError && <Alert type="error" showIcon message={applyError} />}
            <Checkbox checked={confirmed} disabled={!!busy || applyConflict || (targetType !== 'shot_video' && !targetLoaded)} onChange={(event) => setConfirmed(event.target.checked)}>我已核对真实目标和当前媒体，确认采用此资产。</Checkbox>
            <div className="generation-form-actions"><Button onClick={() => setApplyOpen(false)} disabled={!!busy}>收起</Button><Button type="primary" htmlType="submit" loading={busy === 'apply'} disabled={!!busy || !confirmed || applyConflict}>确认采用</Button></div>
          </Form>}
      </section>
    </>}
  </Drawer>;
}

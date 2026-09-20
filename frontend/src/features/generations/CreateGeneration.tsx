import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Form, Input, InputNumber, Select } from 'antd';
import { Link } from 'react-router-dom';
import { generations } from '../../api/modules/generations';
import type { GenerationKind, GenerationReceipt, ImageGenerationRequest, TextGenerationRequest, VideoGenerationRequest } from '../../api/types/generations';
import { attemptStorage, clearAttempt, isServerId, requestAttempt } from './attempt';
import { ConfigSelect } from './ConfigSelect';
import { generationError, kindLabels } from './presentation';

interface FormValues {
  config_id?: string; prompt: string; system?: string; temperature?: number; max_output_tokens?: number;
  reference_media_ids?: string; first_frame_media_id?: string; last_frame_media_id?: string;
  aspect?: string; resolution?: string; count?: number; duration_ms?: number;
  source_id?: string; layout?: 'single' | 'four' | 'five' | 'nine';
}
const optionalId = { validator: (_: unknown, value?: string) => !value || isServerId(value.trim()) ? Promise.resolve() : Promise.reject(new Error('请输入真实服务端的正整数 ID')) };
export function CreateGeneration({ kind, onClose, onCreated }: { kind: GenerationKind; onClose: () => void; onCreated: (value: GenerationReceipt) => void }) {
  const [form] = Form.useForm<FormValues>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const alive = useRef(true);
  const scope = `create:${kind}`;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function submit(values: FormValues) {
    if (inFlight.current) return;
    inFlight.current = true; setPending(true); setError('');
    try {
      const config = values.config_id ? { config_id: values.config_id } : {};
      let result: GenerationReceipt;
      if (kind === 'text') {
        const body: TextGenerationRequest = { ...config, input: { messages: [
          ...(values.system?.trim() ? [{ role: 'system' as const, content: values.system.trim() }] : []),
          { role: 'user', content: values.prompt.trim() },
        ] }, parameters: { ...(values.temperature != null ? { temperature: values.temperature } : {}), ...(values.max_output_tokens != null ? { max_output_tokens: values.max_output_tokens } : {}) } };
        result = await generations.generateText(body, await requestAttempt(scope, body, attemptStorage()));
      } else if (kind === 'image') {
        const references = (values.reference_media_ids ?? '').split(/[\s,，]+/).filter(Boolean);
        if (references.some((id) => !isServerId(id))) throw new Error('reference');
        const body: ImageGenerationRequest = { ...config,
          ...(values.source_id?.trim() ? { source: { scene: 'shot_image', shot_id: values.source_id.trim(), layout: values.layout ?? 'single' } as const } : {}),
          input: { prompt: values.prompt.trim(), reference_media_ids: references },
          parameters: { count: values.count ?? 1, ...(values.aspect ? { aspect: values.aspect.trim() } : {}), ...(values.resolution ? { resolution: values.resolution.trim() } : {}) },
        };
        result = await generations.generateImage(body, await requestAttempt(scope, body, attemptStorage()));
      } else {
        const body: VideoGenerationRequest = { ...config, input: { prompt: values.prompt.trim(),
          ...(values.first_frame_media_id?.trim() ? { first_frame_media_id: values.first_frame_media_id.trim() } : {}),
          ...(values.last_frame_media_id?.trim() ? { last_frame_media_id: values.last_frame_media_id.trim() } : {}),
        }, parameters: { ...(values.aspect ? { aspect: values.aspect.trim() } : {}), ...(values.resolution ? { resolution: values.resolution.trim() } : {}), ...(values.duration_ms != null ? { duration_ms: values.duration_ms } : {}) } };
        result = await generations.generateVideo(body, await requestAttempt(scope, body, attemptStorage()));
      }
      clearAttempt(scope, attemptStorage());
      if (alive.current) onCreated(result);
    } catch (cause) { if (alive.current) setError(generationError(cause)); }
    finally { inFlight.current = false; if (alive.current) setPending(false); }
  }
  return <Drawer open title={`新建${kindLabels[kind]}任务`} width={600} onClose={() => !pending && onClose()} closable={!pending} maskClosable={!pending} keyboard={!pending} rootClassName="generation-drawer">
    <p className="generation-intro">选择模型并填写创作内容。任务提交后在后台执行，结果会保留在服务端。</p>
    <Form form={form} layout="vertical" onFinish={submit} disabled={pending} initialValues={{ count: 1, layout: 'single' }}
      onValuesChange={() => { clearAttempt(scope, attemptStorage()); setError(''); }}>
      <Form.Item name="config_id" label="模型配置" extra={<span>留空使用此类型的默认配置。<Link to="/ai">管理 AI 配置</Link></span>}><ConfigSelect kind={kind} disabled={pending} /></Form.Item>
      {kind === 'text' && <Form.Item name="system" label="创作要求（选填）"><Input.TextArea rows={2} maxLength={100000} placeholder="例如：你是一名短剧编剧，请使用对白推进情节。" /></Form.Item>}
      <Form.Item name="prompt" label={kind === 'text' ? '创作内容' : '画面描述'} rules={[{ required: true, whitespace: true, message: '请填写创作内容' }]}>
        <Input.TextArea rows={6} maxLength={200000} showCount placeholder={kind === 'text' ? '输入故事梗概、改编要求或需要处理的正文…' : kind === 'image' ? '描述主体、场景、构图与光线…' : '描述画面、镜头运动与主体动作…'} />
      </Form.Item>
      <div className="generation-form-grid">
        {kind === 'text' ? <>
          <Form.Item name="temperature" label="随机程度（选填）"><InputNumber min={0} max={2} step={0.1} placeholder="模型默认值" /></Form.Item>
          <Form.Item name="max_output_tokens" label="最大输出 Token（选填）"><InputNumber min={1} max={1000000} precision={0} placeholder="模型默认值" /></Form.Item>
        </> : <>
          <Form.Item name="aspect" label="画面比例（选填）"><Select allowClear placeholder="模型默认值" options={['16:9', '9:16', '1:1', '4:3', '3:4'].map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="resolution" label="分辨率（选填）" extra={kind === 'image' ? '首次测试建议留空。明确像素尺寸如 1024x1024；2K 等档位仅适用于已适配的模型。' : undefined}><Input maxLength={32} placeholder={kind === 'image' ? '留空使用模型默认尺寸' : '例如 1080p'} /></Form.Item>
          {kind === 'image' ? <Form.Item name="count" label="候选图片数量"><InputNumber min={1} max={4} precision={0} /></Form.Item>
            : <Form.Item name="duration_ms" label="视频时长（毫秒，选填）"><InputNumber min={1} max={3600000} precision={0} step={1000} placeholder="例如 6000" /></Form.Item>}
        </>}
      </div>
      {kind === 'image' && <>
        <Form.Item name="reference_media_ids" label="参考图片媒体 ID（选填）" extra="使用资产详情中的媒体 ID，多个 ID 以逗号分隔，最多 16 张。" rules={[{ validator: (_, value?: string) => { const ids = (value ?? '').split(/[\s,，]+/).filter(Boolean); return ids.length <= 16 && ids.every(isServerId) ? Promise.resolve() : Promise.reject(new Error('最多填写 16 个真实服务端媒体 ID')); } }]}><Input placeholder="服务端媒体 ID" /></Form.Item>
        <details className="generation-source"><summary>关联真实分镜（选填）</summary>
          <p>仅接受已经保存在服务端的分镜。当前项目页的本地演示分镜不能用于此处；留空即可独立生成。</p>
          <Form.Item name="source_id" label="来源分镜的服务端 ID" rules={[optionalId]}><Input placeholder="输入真实分镜 ID" /></Form.Item>
          <Form.Item name="layout" label="单张图片布局"><Select options={[{ value: 'single', label: '单图' }, { value: 'four', label: '四宫格' }, { value: 'five', label: '五宫格' }, { value: 'nine', label: '九宫格' }]} /></Form.Item>
        </details>
      </>}
      {kind === 'video' && <div className="generation-form-grid">
        <Form.Item name="first_frame_media_id" label="首帧媒体 ID（选填）" rules={[optionalId]}><Input placeholder="真实服务端图片媒体 ID" /></Form.Item>
        <Form.Item name="last_frame_media_id" label="尾帧媒体 ID（选填）" rules={[optionalId]}><Input placeholder="真实服务端图片媒体 ID" /></Form.Item>
      </div>}
      <p className="generation-hint">可选参数需符合所选模型能力。提交生成可能产生模型调用费用，生成结果不会自动替换项目内容。</p>
      {error && <Alert type="error" showIcon message={error} description="表单已保留。保持内容不变再次提交会复用本次请求；修改内容后会作为新请求提交。" />}
      <div className="generation-form-actions"><Button onClick={onClose} disabled={pending}>取消</Button><Button type="primary" htmlType="submit" loading={pending}>提交{kindLabels[kind]}任务</Button></div>
    </Form>
  </Drawer>;
}

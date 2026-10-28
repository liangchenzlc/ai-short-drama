import { aiModelConfigs } from '@host/api/ai-config/management';
import { generations } from '@host/api/generations/tasks/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { imageGroups } from '@host/api/media/image-groups';
import { ConfigSelect } from '@host/features/ai-config/selection/ModelSelect';
import {
  clearRecovery,
  creationScope,
  readRecovery,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { ImageEditingSurface } from '@host/features/media/image-tools/ImageEditingSurface';
import { buildToolParameters } from '@host/features/media/image-tools/tool-parameters';
import {
  attemptStorage,
  clearAttempt,
  getAttemptAccount,
  requestAttempt,
} from '@host/features/shared/async/request-receipt';
import {
  canvasEmotionPresets
} from '@host/shared/media/image-tools/emotion';
import {
  LIGHT_POSITIONS,
  STYLE_PRESETS
} from '@host/shared/media/image-tools/lighting';
import {
  DEFAULT_PORTRAIT_TEXTURE_SETTINGS,
  PORTRAIT_TEXTURE_GROUPS
} from '@host/shared/media/image-tools/portrait-texture';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AiModelCapabilitiesDto } from '@host/types/ai-config/api';
import type { ImageGenerationRequest, TextGenerationRequest } from '@host/types/generations/api';
import type {
  ImageGroupItem,
  ImageOperationRequest,
  ImageTool,
  ImageToolParameters,
} from '@host/types/media/image-groups';
import type { ImageEditingHandle } from '@host/types/media/image-tools/ImageEditingSurface';
import type { PendingTool } from '@host/types/media/image-tools/ImageToolsDialog';
import type { ImageCropRect } from '@host/types/shared/media/image-tools/image-data';
import type { PortraitTextureSettings } from '@host/types/shared/media/image-tools/portrait-texture';
import { Alert, Button, Checkbox, Input, InputNumber, Select, Slider, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
export const imageToolLabels: Record<ImageTool, string> = {
  crop: '裁切',
  annotation: '标注',
  resize: '调整尺寸',
  split: '宫格切分',
  replace: '上传替换',
  mask: '局部重绘',
  emotion: '表情',
  texture: '质感',
  angle: '多角度',
  lighting: '打光',
  reverse_prompt: '看图反推',
};
const AI_TOOLS = new Set<ImageTool>(['mask', 'emotion', 'texture', 'angle', 'lighting', 'reverse_prompt']);
const isPendingTool = (value: unknown): value is PendingTool => {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<PendingTool>;
  return (
    item.format === 1 &&
    typeof item.key === 'string' &&
    !!item.body &&
    typeof item.body.item_id === 'string' &&
    typeof item.body.expected_item_version === 'string' &&
    typeof item.body.operation_type === 'string' &&
    (!item.operationId || /^\d+$/.test(item.operationId)) &&
    (!item.generation ||
      (['text', 'image'].includes(item.generation.kind) &&
        typeof item.generation.key === 'string' &&
        !!item.generation.body))
  );
};

export function ImageToolsDialog({
  item,
  tool,
  imageModelId,
  disabled,
  onBeforeChange,
  onChanged,
  onClose,
  onBusyChange
}: {
  item: ImageGroupItem;
  tool: ImageTool;
  imageModelId?: string;
  disabled: boolean;
  onBeforeChange: () => Promise<boolean>;
  onChanged: () => void;
  onClose: () => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const ai = AI_TOOLS.has(tool);
  const textTool = tool === 'reverse_prompt';
  const [model, setModel] = useState<string | undefined>(textTool ? undefined : imageModelId);
  const [caps, setCaps] = useState<AiModelCapabilitiesDto | null>(null);
  const [capsLoading, setCapsLoading] = useState(false);
  const [capsError, setCapsError] = useState('');
  const [capsRevision, setCapsRevision] = useState(0);
  const [src, setSrc] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [sourceRevision, setSourceRevision] = useState(0);
  const [size, setSize] = useState({
    width: item.width ?? 1,
    height: item.height ?? 1,
  });
  const [output, setOutput] = useState({
    width: Math.min(4096, item.width ?? 1024),
    height: Math.min(4096, item.height ?? 1024),
  });
  const [linkedSize, setLinkedSize] = useState(true);
  const [selection, setSelection] = useState<ImageCropRect>(
    tool === 'emotion' ? { x: 0.3, y: 0.2, width: 0.4, height: 0.45 } : { x: 0, y: 0, width: 1, height: 1 },
  );
  const [parameters, setParameters] = useState<ImageToolParameters>({
    rows: 2,
    columns: 2,
    interpolation: 'high',
    horizontal_angle: 45,
    pitch_angle: 0,
    camera_distance: 4.8,
    wide_angle: false,
    azimuth: 0,
    elevation: 0,
    brightness: 50,
    light_color: '#ffffff',
    rim_light: false,
    preset: tool === 'emotion' ? 'emotion-0-0' : '',
  });
  const [texture, setTexture] = useState<PortraitTextureSettings>({
    ...DEFAULT_PORTRAIT_TEXTURE_SETTINGS,
  });
  const [instructions, setInstructions] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [feedback, setFeedback] = useState<{
    message: string;
    type: 'info' | 'success' | 'warning' | 'error';
  }>({ message: '', type: 'info' });
  const { message, type: messageType } = feedback;
  const setMessage = (message: string, type: typeof messageType = 'info') => setFeedback({ message, type });
  const [resultText, setResultText] = useState('');
  const [pending, setPending] = useState<PendingTool | null>(null);
  const [recoveryError, setRecoveryError] = useState('');
  const surface = useRef<ImageEditingHandle>(null);
  const alive = useRef(true);
  const account = getAttemptAccount();
  const attemptScope = `image-tool:${item.id}:${tool}`;
  const recoveryScope = creationScope(attemptScope);
  const current = () => alive.current && account === getAttemptAccount();
  const patch = (next: Partial<ImageToolParameters>) => setParameters((value) => ({ ...value, ...next }));
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  useEffect(() => {
    try {
      setPending(readRecovery(recoveryScope, isPendingTool));
    } catch (cause) {
      setRecoveryError(cause instanceof Error ? cause.message : '处理恢复记录无法读取');
    }
  }, [recoveryScope]);
  useEffect(() => {
    const controller = new AbortController();
    let url = '';
    setSrc('');
    setSourceError('');
    if (item.media_id)
      void imageGroups
        .content(item.media_id, controller.signal)
        .then(async (value) => {
          const bitmap = await createImageBitmap(value);
          const width = bitmap.width,
            height = bitmap.height;
          bitmap.close();
          if (!controller.signal.aborted) {
            url = URL.createObjectURL(value);
            setSrc(url);
            setSize({ width, height });
            const scale = Math.min(1, 4096 / Math.max(width, height));
            setOutput({
              width: Math.max(1, Math.round(width * scale)),
              height: Math.max(1, Math.round(height * scale)),
            });
          }
        })
        .catch((cause) => {
          if (!controller.signal.aborted) setSourceError(`原图读取失败：${errorMessage(cause)}`);
        });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [item.media_id, sourceRevision]);
  useEffect(() => {
    const controller = new AbortController();
    setCaps(null);
    setCapsError('');
    if (!ai || !model) {
      setCapsLoading(false);
      return;
    }
    setCapsLoading(true);
    void aiModelConfigs
      .capabilities(model, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setCaps(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setCapsError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setCapsLoading(false);
      });
    return () => controller.abort();
  }, [ai, model, capsRevision]);
  const blocked =
    capsError ||
    (ai
      ? !model
        ? '请选择处理模型'
        : capsLoading
          ? '正在核对模型能力…'
          : !caps?.known
            ? '当前模型协议未识别，请选择可用模型'
            : textTool
              ? !caps.vision
                ? '当前文字模型未声明图片理解能力，请选择支持看图的模型'
                : ''
              : !caps.reference_images
                ? '当前模型不支持参考图编辑，请切换图片模型'
                : tool === 'mask' && !caps.mask
                  ? '当前模型不支持蒙版局部重绘，请切换支持蒙版的图片模型'
                  : ''
      : '');
  const frozen = busy || disabled || !!pending;
  function setDimension(key: 'width' | 'height', value: number | null) {
    const next = Math.max(1, Math.min(4096, Math.round(value ?? 1)));
    const other = key === 'width' ? 'height' : 'width';
    const ratio = size[other] / size[key];
    if (linkedSize && next * ratio > 4096) {
      const scale = 4096 / size[other];
      setOutput({
        width: Math.max(1, Math.round(size.width * scale)),
        height: Math.max(1, Math.round(size.height * scale)),
      });
    } else
      setOutput((current) => ({
        ...current,
        [key]: next,
        ...(linkedSize ? { [other]: Math.max(1, Math.round(next * ratio)) } : {}),
      }));
  }
  const builtParameters = () => buildToolParameters({ tool, selection, parameters, size, instructions, output, texture });
  function persist(value: PendingTool) {
    saveRecovery(recoveryScope, value);
    if (current()) setPending(value);
  }
  function clear() {
    clearRecovery(recoveryScope);
    clearAttempt(attemptScope, attemptStorage());
    clearAttempt(`${attemptScope}:generation`, attemptStorage());
    if (current()) setPending(null);
  }
  async function observeTask(taskId: string, operationId?: string, active = () => true) {
    const detail = await generations.detail(taskId);
    if (!current() || !active()) return true;
    if (detail.status === 'queued' || detail.status === 'running') {
      setMessage('图片处理已提交，关闭工具后任务会继续执行。');
      return false;
    }
    const operation = operationId ? await imageGroups.operation(operationId) : null;
    if (!current() || !active()) return true;
    if (detail.status === 'succeeded') {
      if (textTool) setResultText(detail.result.text?.content ?? '');
      setMessage(
        operation?.status === 'conflict'
          ? '处理期间原图已变化，结果已保存在图片历史中。'
          : textTool
            ? '提示词已生成，可复制使用。'
            : '图片处理完成，已保存到原位置。',
        operation?.status === 'conflict' ? 'warning' : 'success',
      );
      clear();
      onChanged();
    } else {
      setMessage(detail.error?.message ?? '图片处理未完成，原图已保留。可在任务详情核对并安全恢复。', 'error');
      clear();
      onChanged();
    }
    return true;
  }
  useEffect(() => {
    const taskId = pending?.generation?.taskId;
    const operationId = pending?.operationId;
    if (!taskId) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    async function poll() {
      let terminal = false;
      try {
        terminal = await observeTask(taskId!, operationId, () => !stopped);
      } catch (cause) {
        if (!stopped && current()) setMessage(`任务状态读取失败：${errorMessage(cause)}`, 'error');
      }
      if (!stopped && !terminal) timer = setTimeout(() => void poll(), 2000);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pending?.generation?.taskId, pending?.operationId]);
  async function submit() {
    if (
      lock.current ||
      frozen ||
      blocked ||
      !src ||
      (tool === 'replace' && !file) ||
      (tool === 'mask' && !instructions.trim())
    )
      return;
    lock.current = true;
    setBusy(true);
    setMessage('');
    let saved: PendingTool | null = null;
    try {
      if (!(await onBeforeChange()) || !current()) return;
      const body: ImageOperationRequest = {
        item_id: item.id,
        expected_item_version: item.item_version,
        operation_type: tool,
        parameters: builtParameters(),
      };
      const key = await requestAttempt(attemptScope, body, attemptStorage());
      saved = { format: 1, key, body };
      persist(saved);
      const operation = await imageGroups.create(body, key);
      saved = { ...saved, operationId: operation.id };
      persist(saved);
      const data =
        tool === 'annotation'
          ? await surface.current?.image()
          : tool === 'mask'
            ? await surface.current?.mask()
            : tool === 'replace'
              ? (file ?? undefined)
              : undefined;
      const prepared = await imageGroups.execute(operation.id, data);
      if (!current()) return;
      if (!ai) {
        clear();
        onChanged();
        onClose();
        return;
      }
      const source = {
        scene: 'image_operation' as const,
        operation_id: prepared.id,
      };
      const generationBody: ImageGenerationRequest | TextGenerationRequest = textTool
        ? { config_id: model, source, parameters: {} }
        : {
          config_id: model,
          source,
          input: { prompt: '', reference_media_ids: [] },
          parameters: { count: 1 },
        };
      const generationKey = await requestAttempt(`${attemptScope}:generation`, generationBody, attemptStorage());
      const generation: NonNullable<PendingTool['generation']> = {
        kind: textTool ? 'text' : 'image',
        key: generationKey,
        body: generationBody,
      };
      const submitted: PendingTool = {
        format: 1,
        key,
        body,
        operationId: operation.id,
        generation,
      };
      saved = submitted;
      persist(submitted);
      const receipt = textTool
        ? await generations.generateText(generationBody as TextGenerationRequest, generationKey)
        : await generations.generateImage(generationBody as ImageGenerationRequest, generationKey);
      persist({
        ...submitted,
        generation: { ...generation, taskId: receipt.generation_id },
      });
      onChanged();
      setMessage('处理任务已提交，结果会自动保存。');
    } catch (cause) {
      if (current()) {
        if (cause instanceof ApiError && !!cause.status && cause.status < 500 && cause.status !== 408) {
          clear();
          setMessage(errorMessage(cause), 'error');
        } else
          setMessage(
            saved
              ? '处理结果待核对，请使用原请求核对，避免重复生成。'
              : cause instanceof Error
                ? cause.message
                : errorMessage(cause),
            saved ? 'warning' : 'error',
          );
      }
    } finally {
      lock.current = false;
      if (current()) setBusy(false);
    }
  }
  async function reconcile() {
    if (!pending || lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      const operation = pending.operationId
        ? await imageGroups.operation(pending.operationId)
        : await imageGroups.create(pending.body, pending.key);
      if (pending.generation) {
        const receipt = await generations.lookup(pending.generation.kind, pending.generation.key);
        if (!receipt) {
          setMessage('暂未查到原生成请求，请稍后再次核对；当前不会重新提交。', 'warning');
          return;
        }
        persist({
          ...pending,
          operationId: operation.id,
          generation: { ...pending.generation, taskId: receipt.generation_id },
        });
        await observeTask(receipt.generation_id, operation.id);
      } else if (operation.status === 'completed' || operation.status === 'conflict') {
        clear();
        onChanged();
        setMessage('原处理已完成，图片已刷新。', 'success');
      } else {
        clear();
        setMessage('原请求尚未提交模型，原图保持不变。可重新保存本次处理。', 'warning');
      }
    } catch (cause) {
      if (current()) setMessage(`原请求核对失败：${errorMessage(cause)}`, 'error');
    } finally {
      lock.current = false;
      if (current()) setBusy(false);
    }
  }
  const selecting = tool === 'crop' || tool === 'emotion';
  const surfaceMode = selecting ? tool : tool === 'annotation' || tool === 'mask' ? tool : 'preview';
  const selectionField = (key: keyof ImageCropRect, label: string) => (
    <label key={key}>
      {label}
      <InputNumber
        aria-label={`选区${label}百分比`}
        min={key === 'width' || key === 'height' ? 0.1 : 0}
        max={
          key === 'x'
            ? (1 - selection.width) * 100
            : key === 'y'
              ? (1 - selection.height) * 100
              : key === 'width'
                ? (1 - selection.x) * 100
                : (1 - selection.y) * 100
        }
        precision={1}
        value={Number((selection[key] * 100).toFixed(1))}
        disabled={frozen}
        onChange={(value) => setSelection((current) => ({ ...current, [key]: (value ?? 0) / 100 }))}
      />
      <span>%</span>
    </label>
  );
  return (
    <Dialog
      title={imageToolLabels[tool]}
      className="image-tools-dialog standard-creation-dialog"
      canClose={!busy}
      onClose={onClose}
    >
      <div className="image-tools-body">
        <div className="image-tools-workspace">
          {sourceError ? (
            <Alert
              type="error"
              message={sourceError}
              action={<Button onClick={() => setSourceRevision((value) => value + 1)}>重新读取原图</Button>}
            />
          ) : !src ? (
            <div className="image-tools-loading" role="status">
              <Spin />
              <span>正在读取原图…</span>
            </div>
          ) : (
            <ImageEditingSurface
              ref={surface}
              src={src}
              width={size.width}
              height={size.height}
              mode={surfaceMode}
              selection={selection}
              onSelection={setSelection}
              disabled={frozen}
            />
          )}
        </div>
        <div className="image-tools-settings">
          <p className="image-tools-dimensions">
            原图 {size.width} × {size.height}
          </p>
          {selecting && (
            <>
              <p>
                {tool === 'emotion'
                  ? '在图片上框选目标人脸；只融合该人脸周围的表情区域。'
                  : '拖动框选或输入选区，可自由裁切。'}
              </p>
              <div className="image-selection-fields">
                {selectionField('x', '左侧')}
                {selectionField('y', '顶部')}
                {selectionField('width', '宽度')}
                {selectionField('height', '高度')}
              </div>
              {tool === 'crop' && (
                <p>
                  裁切后 {Math.ceil(selection.width * size.width)} × {Math.ceil(selection.height * size.height)}
                  ；保留下次生成画幅设置。
                </p>
              )}
            </>
          )}
          {tool === 'replace' && (
            <label className="image-file-control">
              选择新图片
              <input
                type="file"
                aria-label="选择替换图片"
                accept="image/png,image/jpeg,image/webp"
                disabled={frozen}
                onChange={(event) => {
                  const next = event.target.files?.[0];
                  if (next && next.size > 20 * 1024 ** 2) {
                    setMessage('图片不能超过 20 MiB', 'error');
                    setFile(null);
                  } else setFile(next ?? null);
                }}
              />
              <span>PNG、JPEG、WebP，最多 20 MiB。</span>
            </label>
          )}
          {tool === 'resize' && (
            <>
              <label>
                目标宽度
                <InputNumber
                  aria-label="目标宽度"
                  min={1}
                  max={4096}
                  precision={0}
                  value={output.width}
                  disabled={frozen}
                  onChange={(value) => setDimension('width', value)}
                />
              </label>
              <label>
                目标高度
                <InputNumber
                  aria-label="目标高度"
                  min={1}
                  max={4096}
                  precision={0}
                  value={output.height}
                  disabled={frozen}
                  onChange={(value) => setDimension('height', value)}
                />
              </label>
              <Checkbox
                checked={linkedSize}
                disabled={frozen}
                onChange={(event) => setLinkedSize(event.target.checked)}
              >
                保持比例
              </Checkbox>
              <label>
                插值方式
                <Select
                  classNames={{ popup: { root: 'standard-creation-popup' } }}
                  aria-label="插值方式"
                  value={parameters.interpolation}
                  disabled={frozen}
                  options={[
                    { value: 'nearest', label: '邻近像素' },
                    { value: 'bilinear', label: '双线性' },
                    { value: 'high', label: '高质量' },
                  ]}
                  onChange={(interpolation) => patch({ interpolation })}
                />
              </label>
              <p>长边最多 4096 像素。插值调整不调用 AI 模型。</p>
            </>
          )}
          {tool === 'split' && (
            <>
              <label>
                行数
                <InputNumber
                  aria-label="宫格行数"
                  min={1}
                  max={5}
                  precision={0}
                  value={parameters.rows}
                  disabled={frozen}
                  onChange={(rows) => patch({ rows: rows ?? 1 })}
                />
              </label>
              <label>
                列数
                <InputNumber
                  aria-label="宫格列数"
                  min={1}
                  max={5}
                  precision={0}
                  value={parameters.columns}
                  disabled={frozen}
                  onChange={(columns) => patch({ columns: columns ?? 1 })}
                />
              </label>
              <p>
                按从左到右、从上到下切分为 {(parameters.rows ?? 2) * (parameters.columns ?? 2)} 张新图片，最多 25
                张。第一张为主图，原整组保留在历史中。
              </p>
            </>
          )}
          {tool === 'emotion' && (
            <label>
              目标表情
              <Select
                classNames={{ popup: { root: 'standard-creation-popup' } }}
                aria-label="目标表情"
                value={parameters.preset}
                disabled={frozen}
                options={canvasEmotionPresets.map((value) => ({
                  value: value.id,
                  label: value.label,
                }))}
                onChange={(preset) => patch({ preset })}
              />
            </label>
          )}
          {tool === 'texture' &&
            PORTRAIT_TEXTURE_GROUPS.map((group) => (
              <label key={group.key}>
                {group.label}
                <Select
                  classNames={{ popup: { root: 'standard-creation-popup' } }}
                  aria-label={group.label}
                  disabled={frozen}
                  value={texture[group.key]}
                  options={group.options.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  onChange={(value) =>
                    setTexture((current) => ({
                      ...current,
                      [group.key]: value,
                    }))
                  }
                />
              </label>
            ))}
          {tool === 'angle' && (
            <>
              {(
                [
                  ['horizontal_angle', '水平角度', -180, 180],
                  ['pitch_angle', '俯仰角度', -90, 90],
                  ['camera_distance', '镜头距离', 1, 10],
                ] as const
              ).map(([key, label, min, max]) => (
                <label key={key}>
                  {label}
                  <Slider
                    aria-label={label}
                    min={min}
                    max={max}
                    step={key === 'camera_distance' ? 0.1 : 1}
                    value={parameters[key]}
                    disabled={frozen}
                    onChange={(value) => patch({ [key]: value })}
                  />
                </label>
              ))}
              <Checkbox
                checked={parameters.wide_angle}
                disabled={frozen}
                onChange={(event) => patch({ wide_angle: event.target.checked })}
              >
                广角镜头
              </Checkbox>
            </>
          )}
          {tool === 'lighting' && (
            <>
              <label>
                打光预设
                <Select
                  classNames={{ popup: { root: 'standard-creation-popup' } }}
                  aria-label="打光预设"
                  value={parameters.preset}
                  disabled={frozen}
                  options={[
                    { value: '', label: '自定义' },
                    ...STYLE_PRESETS.map((value) => ({
                      value: value.id,
                      label: value.name,
                    })),
                  ]}
                  onChange={(preset) => patch({ preset })}
                />
              </label>
              <div className="image-light-positions">
                {LIGHT_POSITIONS.map((position) => (
                  <Button
                    key={position.label}
                    size="small"
                    disabled={frozen}
                    onClick={() =>
                      patch({
                        azimuth: position.azimuth,
                        elevation: position.elevation,
                      })
                    }
                  >
                    {position.label}
                  </Button>
                ))}
              </div>
              {(
                [
                  ['azimuth', '方位角', 0, 360],
                  ['elevation', '俯仰角', -90, 90],
                  ['brightness', '亮度', 0, 100],
                ] as const
              ).map(([key, label, min, max]) => (
                <label key={key}>
                  {label}
                  <Slider
                    aria-label={label}
                    min={min}
                    max={max}
                    value={parameters[key]}
                    disabled={frozen}
                    onChange={(value) => patch({ [key]: value })}
                  />
                </label>
              ))}
              <label>
                光色
                <input
                  aria-label="光色"
                  type="color"
                  value={parameters.light_color}
                  disabled={frozen}
                  onChange={(event) => patch({ light_color: event.target.value })}
                />
              </label>
              <Checkbox
                disabled={frozen}
                checked={parameters.rim_light}
                onChange={(event) => patch({ rim_light: event.target.checked })}
              >
                轮廓光
              </Checkbox>
            </>
          )}
          {ai && (
            <>
              <label>
                {tool === 'mask' ? '重绘要求' : '补充要求'}
                <Input.TextArea
                  aria-label={tool === 'mask' ? '重绘要求' : '补充要求'}
                  maxLength={2000}
                  autoSize={{ minRows: 3, maxRows: 6 }}
                  value={instructions}
                  disabled={frozen}
                  onChange={(event) => setInstructions(event.target.value)}
                />
              </label>
              <label>
                处理模型
                <ConfigSelect
                  presentation="standard-creation"
                  key={textTool ? 'text' : 'image'}
                  kind={textTool ? 'text' : 'image'}
                  label="图片处理模型"
                  value={model}
                  onChange={setModel}
                  onResolvedChange={(id) => {
                    if (!model) setModel(id);
                  }}
                  disabled={frozen}
                />
              </label>
              {tool === 'emotion' && caps?.known && !caps.mask && <p>当前模型不支持蒙版，将使用人脸裁切与羽化融合。</p>}
              {blocked && !capsLoading && (caps || capsError) && (
                <StudioFeedback
                  type={capsError ? 'error' : 'warning'}
                  quiet={!capsError}
                  title={`${imageToolLabels[tool]} · 模型能力`}
                  noticeKey={`image-tool-capabilities:${item.id}:${tool}`}
                  message={blocked}
                  actions={
                    capsError ? (
                      <Button onClick={() => setCapsRevision((value) => value + 1)}>重新核对</Button>
                    ) : undefined
                  }
                />
              )}
            </>
          )}
          {tool !== 'reverse_prompt' && (
            <p className="image-tools-history-note">保存后替换当前图位，原图保留在图片历史中。</p>
          )}
        </div>
      </div>
      {recoveryError && (
        <StudioFeedback
          type="error"
          title={`${imageToolLabels[tool]} · 恢复记录`}
          noticeKey={`image-tool-recovery:${item.id}:${tool}`}
          message={recoveryError}
        />
      )}
      {pending && (
        <StudioFeedback
          type={busy || pending.generation?.taskId ? 'info' : 'warning'}
          quiet
          persistent
          title={`${imageToolLabels[tool]} · 原处理请求`}
          noticeKey={`image-tool-pending:${item.id}:${tool}`}
          message={
            pending.generation?.taskId
              ? '处理任务执行中，可关闭工具。'
              : busy
                ? '正在提交图片处理。'
                : '原处理请求待核对。'
          }
          actions={
            <Button loading={busy} onClick={() => void reconcile()}>
              核对原处理
            </Button>
          }
        />
      )}
      {message && (
        <StudioFeedback
          type={messageType}
          quiet={messageType === 'info'}
          title={imageToolLabels[tool]}
          noticeKey={`image-tool-feedback:${item.id}:${tool}`}
          message={message}
        />
      )}
      {resultText && (
        <div className="image-tools-feedback">
          <label>
            反推提示词
            <Input.TextArea
              aria-label="反推提示词"
              value={resultText}
              readOnly
              autoSize={{ minRows: 4, maxRows: 10 }}
            />
            <Button
              onClick={() =>
                void navigator.clipboard
                  .writeText(resultText)
                  .then(() => setMessage('提示词已复制。', 'success'))
                  .catch(() => setMessage('无法自动复制，请选中文字复制。', 'error'))
              }
            >
              复制提示词
            </Button>
          </label>
        </div>
      )}
      <div className="production-dialog-footer">
        <span>{ai ? '模型处理会产生生成任务' : '本地图片处理'}</span>
        <Button disabled={busy} onClick={onClose}>
          关闭
        </Button>
        <Button
          type="primary"
          loading={busy}
          disabled={
            frozen ||
            !!blocked ||
            !src ||
            !!recoveryError ||
            (tool === 'replace' && !file) ||
            (tool === 'mask' && !instructions.trim()) ||
            (tool === 'split' && (parameters.rows ?? 2) * (parameters.columns ?? 2) < 2)
          }
          onClick={() => void submit()}
        >
          {ai ? '开始处理' : tool === 'split' ? '切分并保存' : '保存图片'}
        </Button>
      </div>
    </Dialog>
  );
}

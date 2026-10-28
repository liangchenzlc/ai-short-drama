import { annotationHistory, normalizeAnnotationRect } from '@host/shared/media/image-tools/annotation-model';
import type { DrawMode, ImageEditingHandle } from '@host/types/media/image-tools/ImageEditingSurface';
import type { AnnotationOperation, AnnotationPoint } from '@host/types/shared/media/image-tools/annotation-model';
import type { ImageCropRect } from '@host/types/shared/media/image-tools/image-data';
import { Alert, Button, Input, InputNumber, Segmented, Slider } from 'antd';
import { Redo2, Undo2 } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type PointerEvent } from 'react';
function draw(context: CanvasRenderingContext2D, operations: AnnotationOperation[], mask = false) {
  for (const operation of operations) {
    context.save();
    if (mask) context.globalCompositeOperation = 'destination-out';
    context.strokeStyle = operation.color;
    context.fillStyle = operation.color;
    context.lineWidth = operation.size;
    context.lineJoin = 'round';
    context.lineCap = 'round';
    if (operation.type === 'brush') {
      context.beginPath();
      operation.points.forEach((point, index) => {
        if (index) context.lineTo(point.x, point.y);
        else context.moveTo(point.x, point.y);
      });
      if (operation.points.length === 1) {
        const point = operation.points[0];
        context.arc(point.x, point.y, operation.size / 2, 0, Math.PI * 2);
        context.fill();
      } else context.stroke();
    } else if (operation.type === 'rectangle') {
      if (mask) context.fillRect(operation.x, operation.y, operation.width, operation.height);
      else context.strokeRect(operation.x, operation.y, operation.width, operation.height);
    } else {
      context.font = `${operation.size}px "Microsoft YaHei", sans-serif`;
      context.textBaseline = 'top';
      context.fillText(operation.text, operation.x, operation.y);
    }
    context.restore();
  }
}

const blob = (canvas: HTMLCanvasElement) =>
  new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((value) => (value ? resolve(value) : reject(new Error('无法保存标注图片'))), 'image/png'),
  );

export const ImageEditingSurface = forwardRef<
  ImageEditingHandle,
  {
    src: string;
    width: number;
    height: number;
    mode: 'crop' | 'emotion' | 'mask' | 'annotation' | 'preview';
    selection: ImageCropRect;
    onSelection: (value: ImageCropRect) => void;
    disabled: boolean;
  }
>(function ImageEditingSurface({ src, width, height, mode, selection, onSelection, disabled }, ref) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const bitmap = useRef<HTMLImageElement | null>(null);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [history, setHistory] = useState(annotationHistory.empty);
  const [draft, setDraft] = useState<AnnotationOperation | null>(null);
  const [drawMode, setDrawMode] = useState<DrawMode>('brush');
  const [color, setColor] = useState('#f87171');
  const [size, setSize] = useState(Math.max(4, Math.round(width / 100)));
  const [text, setText] = useState('');
  const [cursor, setCursor] = useState({
    x: Math.round(width / 2),
    y: Math.round(height / 2),
  });
  const drag = useRef<{
    start: AnnotationPoint;
    operation: AnnotationOperation | null;
  } | null>(null);
  const selecting = mode === 'crop' || mode === 'emotion';
  const painting = mode === 'annotation' || mode === 'mask';
  useEffect(() => {
    const image = new Image();
    let active = true;
    setReady(false);
    setLoadError('');
    image.onload = () => {
      if (active) {
        bitmap.current = image;
        setReady(true);
      }
    };
    image.onerror = () => {
      if (active) setLoadError('图片无法显示，请关闭工具后重新读取原图。');
    };
    image.src = src;
    return () => {
      active = false;
      bitmap.current = null;
    };
  }, [src]);
  useEffect(() => {
    const context = canvas.current?.getContext('2d');
    if (!context || !bitmap.current) return;
    context.clearRect(0, 0, width, height);
    context.drawImage(bitmap.current, 0, 0, width, height);
    if (mode === 'mask') context.globalAlpha = 0.55;
    draw(context, [...history.items, ...(draft ? [draft] : [])]);
    context.globalAlpha = 1;
  }, [ready, history, draft, width, height, mode]);
  useImperativeHandle(
    ref,
    () => ({
      image: async () => {
        if (!canvas.current || !ready) throw new Error('图片仍在载入');
        return blob(canvas.current);
      },
      mask: async () => {
        if (!history.items.length) throw new Error('请绘制需要重绘的区域');
        const output = document.createElement('canvas');
        output.width = width;
        output.height = height;
        const context = output.getContext('2d');
        if (!context) throw new Error('无法创建蒙版');
        context.fillStyle = '#fff';
        context.fillRect(0, 0, width, height);
        draw(context, history.items, true);
        return blob(output);
      },
    }),
    [ready, width, height, history],
  );
  function point(event: PointerEvent<HTMLCanvasElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(width - 1, ((event.clientX - bounds.left) / bounds.width) * width)),
      y: Math.max(0, Math.min(height - 1, ((event.clientY - bounds.top) / bounds.height) * height)),
    };
  }
  function begin(event: PointerEvent<HTMLCanvasElement>) {
    if (disabled || !ready || (!painting && !selecting) || event.button !== 0) return;
    const start = point(event);
    setCursor(start);
    event.currentTarget.setPointerCapture(event.pointerId);
    const operation: AnnotationOperation | null = painting
      ? drawMode === 'brush'
        ? { type: 'brush', color, size, points: [start] }
        : drawMode === 'rectangle'
          ? { type: 'rectangle', color, size, ...start, width: 0, height: 0 }
          : {
              type: 'text',
              color,
              size: Math.max(12, size * 3),
              ...start,
              text: text.trim(),
            }
      : null;
    drag.current = { start, operation };
    setDraft(operation);
  }
  function move(event: PointerEvent<HTMLCanvasElement>) {
    if (!drag.current) return;
    const end = point(event);
    const rectangle = normalizeAnnotationRect(drag.current.start, end);
    if (selecting)
      onSelection({
        x: rectangle.x / width,
        y: rectangle.y / height,
        width: Math.max(1 / width, rectangle.width / width),
        height: Math.max(1 / height, rectangle.height / height),
      });
    else {
      const operation = drag.current.operation;
      if (operation?.type === 'brush') operation.points = [...operation.points, end];
      else if (operation?.type === 'rectangle') Object.assign(operation, rectangle);
      if (operation) setDraft({ ...operation });
    }
  }
  function finish() {
    if (drag.current?.operation) {
      const operation = drag.current.operation;
      if (operation.type !== 'text' || operation.text) setHistory((value) => annotationHistory.push(value, operation));
    }
    drag.current = null;
    setDraft(null);
  }
  function addAtCursor() {
    const operation: AnnotationOperation =
      drawMode === 'text'
        ? {
            type: 'text',
            color,
            size: Math.max(12, size * 3),
            ...cursor,
            text: text.trim(),
          }
        : drawMode === 'rectangle'
          ? {
              type: 'rectangle',
              color,
              size,
              ...cursor,
              width: Math.min(width - cursor.x, width / 5),
              height: Math.min(height - cursor.y, height / 5),
            }
          : { type: 'brush', color, size, points: [cursor] };
    if (operation.type !== 'text' || operation.text) setHistory((value) => annotationHistory.push(value, operation));
  }
  return (
    <div className="image-editing-surface">
      {loadError && <Alert type="error" message={loadError} />}
      {painting && (
        <div className="image-drawing-controls">
          <Segmented
            aria-label="标注工具"
            value={drawMode}
            disabled={disabled}
            onChange={(value) => setDrawMode(value as DrawMode)}
            options={[
              { value: 'brush', label: '画笔' },
              { value: 'rectangle', label: '矩形' },
              ...(mode === 'annotation' ? [{ value: 'text', label: '文字' }] : []),
            ]}
          />
          <label>
            颜色
            <input
              aria-label="标注颜色"
              type="color"
              value={color}
              disabled={disabled}
              onChange={(event) => setColor(event.target.value)}
            />
          </label>
          <label className="image-brush-size">
            笔画大小
            <Slider
              aria-label="笔画大小"
              min={1}
              max={Math.max(160, Math.round(width / 10))}
              value={size}
              disabled={disabled}
              onChange={setSize}
            />
          </label>
          <Button
            aria-label="撤销标注"
            icon={<Undo2 size={15} />}
            disabled={disabled || !history.items.length}
            onClick={() => setHistory(annotationHistory.undo)}
          />
          <Button
            aria-label="重做标注"
            icon={<Redo2 size={15} />}
            disabled={disabled || !history.redo.length}
            onClick={() => setHistory(annotationHistory.redo)}
          />
          {drawMode === 'text' && (
            <Input
              aria-label="标注文字"
              placeholder="输入后点击图片放置"
              value={text}
              maxLength={200}
              disabled={disabled}
              onChange={(event) => setText(event.target.value)}
            />
          )}
        </div>
      )}
      <div className="image-editing-stage" style={{ width: `min(100%, ${(58 * width) / height}vh)` }}>
        <canvas
          ref={canvas}
          width={width}
          height={height}
          aria-label={
            selecting ? '拖动框选图片区域' : painting ? '图片标注画布，可用方向键移动光标，回车添加标注' : '图片预览'
          }
          tabIndex={painting || selecting ? 0 : -1}
          onPointerDown={begin}
          onPointerMove={move}
          onPointerUp={finish}
          onPointerCancel={() => {
            drag.current = null;
            setDraft(null);
          }}
          onKeyDown={(event) => {
            if (!painting || disabled) return;
            const delta = event.shiftKey ? 25 : 5;
            if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
              event.preventDefault();
              setCursor((value) => ({
                x: Math.max(
                  0,
                  Math.min(
                    width - 1,
                    value.x + (event.key === 'ArrowRight' ? delta : event.key === 'ArrowLeft' ? -delta : 0),
                  ),
                ),
                y: Math.max(
                  0,
                  Math.min(
                    height - 1,
                    value.y + (event.key === 'ArrowDown' ? delta : event.key === 'ArrowUp' ? -delta : 0),
                  ),
                ),
              }));
            }
            if (event.key === 'Enter') {
              event.preventDefault();
              addAtCursor();
            }
          }}
        />
        {selecting && (
          <div
            className="image-selection"
            style={{
              left: `${selection.x * 100}%`,
              top: `${selection.y * 100}%`,
              width: `${selection.width * 100}%`,
              height: `${selection.height * 100}%`,
            }}
          />
        )}
      </div>
      {painting && (
        <div className="image-keyboard-position">
          <span>标注位置</span>
          <label>
            X
            <InputNumber
              aria-label="标注位置 X"
              min={0}
              max={width - 1}
              value={Math.round(cursor.x)}
              disabled={disabled}
              onChange={(x) => setCursor((value) => ({ ...value, x: x ?? 0 }))}
            />
          </label>
          <label>
            Y
            <InputNumber
              aria-label="标注位置 Y"
              min={0}
              max={height - 1}
              value={Math.round(cursor.y)}
              disabled={disabled}
              onChange={(y) => setCursor((value) => ({ ...value, y: y ?? 0 }))}
            />
          </label>
          <Button disabled={disabled || (drawMode === 'text' && !text.trim())} onClick={addAtCursor}>
            在此添加
            {drawMode === 'text' ? '文字' : drawMode === 'rectangle' ? '矩形' : '笔点'}
          </Button>
          <Button disabled={disabled || !history.items.length} onClick={() => setHistory(annotationHistory.empty())}>
            清空标注
          </Button>
        </div>
      )}
    </div>
  );
});

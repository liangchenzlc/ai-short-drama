import { CreationModelSelect } from '@host/features/ai-config/selection/CreationModelSelect';
import { scriptCharacterCount, selectionSummary } from '@host/features/creation/writing/editor/script-selection';
import { Dialog } from '@host/shared/ui/Dialog';
import type { ScriptSelection } from '@host/types/creation/writing/rewrite/writing-requests';
import { Button } from 'antd';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
export function ScriptRewriteDialog({
  selection,
  instructions,
  onInstructions,
  model,
  onModel,
  resolvedModel,
  onResolvedModel,
  submitting,
  disabled,
  feedback,
  onSubmit,
  onClose,
}: {
  selection: ScriptSelection;
  instructions: string;
  onInstructions: (value: string) => void;
  model: string;
  onModel: (value: string) => void;
  submitting: boolean;
  disabled: boolean;
  resolvedModel: string | undefined | null;
  onResolvedModel: (value: string | undefined) => void;
  feedback: ReactNode;
  onSubmit: () => void;
  onClose: () => void;
}) {
  const form = useRef<HTMLFormElement>(null);
  const [modelContainer, setModelContainer] = useState<HTMLDivElement | null>(null);
  const count = useMemo(() => scriptCharacterCount(selection.text).toLocaleString(), [selection.text]);
  const summary = useMemo(() => selectionSummary(selection.text), [selection.text]);
  const wholeDocument = selection.start === 0 && selection.end === selection.source.length;
  useEffect(() => {
    const dialog = form.current?.closest('dialog');
    if (!dialog) return;
    let positioned = false;
    function place() {
      if (!dialog?.open) return;
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft ?? 0,
        top = viewport?.offsetTop ?? 0;
      const width = viewport?.width ?? window.innerWidth,
        height = viewport?.height ?? window.innerHeight;
      dialog.style.width = `${Math.min(360, width - 24)}px`;
      dialog.style.maxHeight = `${height - 24}px`;
      const box = dialog.getBoundingClientRect();
      const below = selection.bottom ?? selection.top + 30;
      const y =
        below + 8 + box.height <= top + height - 12
          ? below + 8
          : selection.top - box.height - 8 >= top + 12
            ? selection.top - box.height - 8
            : Math.max(top + 12, Math.min(below + 8, top + height - box.height - 12));
      dialog.style.top = `${y}px`;
      dialog.style.left = `${Math.max(left + 12, Math.min(selection.left, left + width - box.width - 12))}px`;
      dialog.dataset.positioned = 'true';
      if (!positioned) {
        positioned = true;
        form.current?.querySelector('textarea')?.focus({ preventScroll: true });
      }
    }
    const outside = (event: PointerEvent) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      if (
        event.clientX < box.left ||
        event.clientX > box.right ||
        event.clientY < box.top ||
        event.clientY > box.bottom
      )
        onClose();
    };
    const observer = new ResizeObserver(place);
    observer.observe(dialog);
    const frame = requestAnimationFrame(place);
    window.addEventListener('resize', place);
    window.visualViewport?.addEventListener('resize', place);
    window.visualViewport?.addEventListener('scroll', place);
    dialog.addEventListener('pointerdown', outside);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', place);
      window.visualViewport?.removeEventListener('resize', place);
      window.visualViewport?.removeEventListener('scroll', place);
      dialog.removeEventListener('pointerdown', outside);
    };
  }, [selection, onClose]);
  return (
    <Dialog title="AI 改写" className="standard-creation-dialog sd-rewrite-dialog" onClose={onClose}>
      <form
        className="sd-rewrite-form"
        ref={form}
        onSubmit={(event) => {
          event.preventDefault();
          if (!disabled && !submitting) onSubmit();
        }}
      >
        <div className="sd-rewrite-fields">
          <div className="sd-selection-scope">
            <span>
              {wholeDocument ? '全文' : '已选'} {count} 字
            </span>
            <span title="仅参考选区和当前剧本前后文">参考当前剧本上下文</span>
          </div>
          <p className="sd-selection-preview">{summary}</p>
          <label className="sd-rewrite-label">
            修改要求
            <textarea
              rows={3}
              value={instructions}
              maxLength={4000}
              required
              readOnly={submitting}
              onChange={(event) => onInstructions(event.target.value)}
              placeholder="希望这段文字怎样改写？"
            />
          </label>
          <div className="sd-model" ref={setModelContainer}>
            <span className="sd-model-label">文本模型</span>
            <CreationModelSelect
              kind="text"
              label="文本模型"
              value={model}
              optionLayout="compact"
              presentation="standard-creation"
              disabled={submitting}
              onChange={onModel}
              onResolvedChange={onResolvedModel}
              feedbackContainer={modelContainer}
            />
          </div>
          {resolvedModel === undefined && (
            <p className="sd-hint">
              请选择可用的文本模型，或
              <a href="/ai_config" target="_blank" rel="noreferrer">
                前往 AI 配置
              </a>
              添加。
            </p>
          )}
          <p className="sd-hint">完成后替换选区，可撤销</p>
          {feedback}
        </div>
        <footer>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            htmlType="submit"
            aria-label="改写选区"
            loading={submitting}
            disabled={disabled || !instructions.trim()}
          >
            改写选区
          </Button>
        </footer>
      </form>
    </Dialog>
  );
}

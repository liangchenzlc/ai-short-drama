import { useEffect, useId, useRef, type ReactNode } from "react";
import { Icon } from './Icon';
import { ConfigProvider } from "antd";

export function Dialog({
  title,
  onClose,
  canClose = true,
  className,
  children,
}: {
  title: string;
  onClose: () => void;
  canClose?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    const returnFocus = document.activeElement;
    dialog?.showModal();
    const initialFocus = dialog?.querySelector<HTMLElement>('[data-dialog-autofocus]')
      ?? dialog?.querySelector<HTMLElement>('input:not([disabled]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea:not([disabled]), select:not([disabled])');
    initialFocus?.focus({ preventScroll: true });
    return () => {
      dialog?.close();
      if (returnFocus instanceof HTMLElement && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={["ui-dialog", className].filter(Boolean).join(" ")}
      onKeyDownCapture={event => {
        if (event.key === 'Tab') {
          const controls = Array.from(ref.current?.querySelectorAll<HTMLElement>('a[href], button, input, textarea, select, [tabindex]') ?? [])
            .filter(control => control.tabIndex >= 0 && !control.matches(':disabled') && control.getClientRects().length > 0 && getComputedStyle(control).visibility !== 'hidden');
          const first = controls[0];
          const last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
        }
        if (event.key === 'Escape' && ref.current?.querySelector('.ant-select-open, .ant-dropdown:not(.ant-dropdown-hidden)')) {
          event.preventDefault();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (canClose) onClose();
      }}
      aria-label={title}
      aria-labelledby={titleId}
    >
      <header>
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          disabled={!canClose}
          aria-label="关闭弹窗"
        >
          <Icon name="close" size={18} />
        </button>
      </header>
      <ConfigProvider getPopupContainer={(trigger) => trigger?.parentElement ?? ref.current ?? document.body}>
        {children}
      </ConfigProvider>
    </dialog>
  );
}

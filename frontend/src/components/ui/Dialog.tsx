import { useEffect, useRef, type ReactNode } from "react";

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
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={["ui-dialog", className].filter(Boolean).join(" ")}
      onCancel={(event) => {
        if (canClose) onClose();
        else event.preventDefault();
      }}
      aria-label={title}
    >
      <header>
        <h2>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          disabled={!canClose}
          aria-label="关闭弹窗"
        >
          ×
        </button>
      </header>
      {children}
    </dialog>
  );
}

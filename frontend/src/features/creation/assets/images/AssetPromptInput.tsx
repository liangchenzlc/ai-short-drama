import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';
import type { Mention, Position, Props, Selection } from '@host/types/creation/assets/images/AssetPromptInput';
import { Image as ImageIcon } from 'lucide-react';
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import './asset-prompt-input.css';
export function AssetPromptInput({ id, value, references, disabled = false, maxLength = 4000, onChange }: Props) {
  const editor = useRef<HTMLDivElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const composing = useRef(false);
  const inserting = useRef(false);
  const renderedReferences = useRef('');
  const [mention, setMention] = useState<Mention | null>(null);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState<Position | null>(null);
  const menuId = useId();
  const helpId = useId();
  const referenceKey = JSON.stringify(references);
  const candidates = useMemo(
    () =>
      references
        .map((reference, index) => ({ reference, index }))
        .filter(
          ({ reference, index }) =>
            !mention?.query || `图片${index + 1} ${reference.name}`.toLowerCase().includes(mention.query.toLowerCase()),
        ),
    [references, mention?.query],
  );
  const open = !!mention && !disabled;

  useLayoutEffect(() => {
    const element = editor.current;
    if (!element || composing.current) return;
    const current = serialize(element);
    if (current === value && renderedReferences.current === referenceKey) return;
    if (current === value && refreshChipMetadata(element, value, references)) {
      renderedReferences.current = referenceKey;
      return;
    }
    const focused = document.activeElement === element;
    const selection = focused ? readSelection(element) : null;
    const scrollTop = element.scrollTop;
    element.replaceChildren(promptNodes(value, references));
    renderedReferences.current = referenceKey;
    if (focused && selection) writeSelection(element, selection);
    element.scrollTop = scrollTop;
  }, [value, referenceKey, references]);

  useEffect(() => {
    if (disabled) setMention(null);
  }, [disabled]);

  useLayoutEffect(() => {
    const element = editor.current;
    if (!open || !element || !mention) {
      setPosition(null);
      return;
    }
    const update = () => setPosition(menuPosition(element, mention.end));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    const viewport = window.visualViewport;
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      viewport?.removeEventListener('resize', update);
      viewport?.removeEventListener('scroll', update);
    };
  }, [open, mention]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !editor.current?.contains(event.target) &&
        !menu.current?.contains(event.target)
      )
        setMention(null);
    };
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, [open]);

  useEffect(() => {
    menu.current?.querySelector<HTMLElement>(`[data-candidate="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  function syncMention(text: string, cursor: number) {
    const match = /@([^\s@,.;:!?，。；：！？、)\]}】）]*)$/u.exec(text.slice(0, cursor));
    setMention(match ? { start: match.index, end: cursor, query: match[1] } : null);
    setActive(0);
  }

  function receiveInput() {
    const element = editor.current;
    if (!element || composing.current || inserting.current) return;
    let next = serialize(element);
    if (next.length > maxLength) {
      const scrollTop = element.scrollTop;
      const cursor = readSelection(element)?.start ?? next.length;
      const clippedCursor = Math.max(0, cursor - next.length + maxLength);
      next = next.slice(0, clippedCursor) + next.slice(cursor);
      element.replaceChildren(promptNodes(next, references));
      writeSelection(element, { start: clippedCursor, end: clippedCursor });
      element.scrollTop = scrollTop;
    }
    onChange(next);
    const selection = readSelection(element);
    if (selection && selection.start === selection.end) syncMention(next, selection.start);
    else setMention(null);
  }

  function insertText(text: string, selection?: Selection, reference?: AssetPromptReference, referenceIndex?: number) {
    const element = editor.current;
    if (!element || disabled) return;
    const selected = selection ?? readSelection(element) ?? { start: value.length, end: value.length };
    const current = serialize(element);
    const available = Math.max(0, maxLength - current.length + selected.end - selected.start);
    if (reference && text.length > available) return;
    const inserted = text.slice(0, available);
    if (!inserted && selected.start === selected.end) return;
    const next = current.slice(0, selected.start) + inserted + current.slice(selected.end);
    const scrollTop = element.scrollTop;
    element.focus({ preventScroll: true });
    writeSelection(element, selected);
    const fragment =
      reference && referenceIndex !== undefined
        ? referenceFragment(reference, referenceIndex)
        : promptNodes(inserted, references);
    const container = document.createElement('div');
    container.append(fragment);
    inserting.current = true;
    try {
      // 浏览器可能在已插入后返回 false，按实际文本校验，避免重复插入。
      document.execCommand('insertHTML', false, container.innerHTML);
      if (serialize(element) !== next) element.replaceChildren(promptNodes(next, references));
      writeSelection(element, {
        start: selected.start + inserted.length,
        end: selected.start + inserted.length,
      });
    } finally {
      inserting.current = false;
    }
    element.scrollTop = scrollTop;
    setMention(null);
    onChange(next);
  }

  function selectReference(candidate: { reference: AssetPromptReference; index: number }) {
    if (!mention) return;
    insertText(
      `@图片${candidate.index + 1} `,
      { start: mention.start, end: mention.end },
      candidate.reference,
      candidate.index,
    );
  }

  function keyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (open && event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setMention(null);
      return;
    }
    if (open && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      setActive((current) =>
        candidates.length
          ? (current + (event.key === 'ArrowDown' ? 1 : -1) + candidates.length) % candidates.length
          : 0,
      );
      return;
    }
    if (open && (event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey && candidates.length) {
      event.preventDefault();
      event.stopPropagation();
      selectReference(candidates[Math.min(active, candidates.length - 1)]);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      insertText('\n');
    }
    if (event.key === 'Tab') setMention(null);
  }

  return (
    <div className="asset-prompt-input">
      <div
        id={id}
        ref={editor}
        className="asset-prompt-editor"
        role="textbox"
        aria-label="画面要求"
        aria-multiline="true"
        aria-disabled={disabled}
        aria-readonly={disabled}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-activedescendant={
          open && candidates.length ? `${menuId}-${Math.min(active, candidates.length - 1)}` : undefined
        }
        aria-describedby={helpId}
        tabIndex={disabled ? -1 : 0}
        contentEditable={!disabled}
        suppressContentEditableWarning
        data-empty={!value}
        data-placeholder="描述本次生成要求，输入 @ 引用参考图片"
        spellCheck={false}
        onInput={receiveInput}
        onKeyDown={keyDown}
        onKeyUp={(event) => {
          if (composing.current || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          const selection = editor.current && readSelection(editor.current);
          if (selection && selection.start === selection.end) syncMention(serialize(editor.current!), selection.start);
          else setMention(null);
        }}
        onErrorCapture={(event) => {
          if (event.target instanceof HTMLImageElement && event.target.closest('.asset-prompt-chip'))
            event.target.replaceWith(chipFallback());
        }}
        onBeforeInput={(event) => {
          const native = event.nativeEvent as InputEvent;
          if (native.isComposing || composing.current || inserting.current) return;
          const selection = editor.current && readSelection(editor.current);
          if (native.inputType === 'insertParagraph') {
            event.preventDefault();
            insertText('\n');
          } else if (
            native.data &&
            value.length - (selection ? selection.end - selection.start : 0) + native.data.length > maxLength
          ) {
            event.preventDefault();
            insertText(native.data, selection ?? undefined);
          }
        }}
        onCompositionStart={() => {
          composing.current = true;
          setMention(null);
        }}
        onCompositionEnd={() => {
          composing.current = false;
          receiveInput();
        }}
        onPaste={(event) => {
          event.preventDefault();
          insertText(event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n'));
        }}
        onCopy={(event) => {
          const selection = editor.current && readSelection(editor.current);
          if (!selection) return;
          event.preventDefault();
          event.clipboardData.setData('text/plain', serialize(editor.current!).slice(selection.start, selection.end));
        }}
        onCut={(event) => {
          const selection = editor.current && readSelection(editor.current);
          if (!selection || disabled) return;
          event.preventDefault();
          event.clipboardData.setData('text/plain', serialize(editor.current!).slice(selection.start, selection.end));
          insertText('', selection);
        }}
        onClick={() => {
          const selection = editor.current && readSelection(editor.current);
          if (selection && selection.start === selection.end) syncMention(value, selection.start);
          else setMention(null);
        }}
        onBlur={() => setMention(null)}
      />
      <span id={helpId} className="asset-prompt-help" role="status">
        {open
          ? `${candidates.length} 张可引用图片。上下方向键选择，Enter 或 Tab 插入，Escape 关闭。`
          : '输入 @ 引用已添加的参考图片。'}
      </span>
      {open &&
        position &&
        createPortal(
          <div
            ref={menu}
            id={menuId}
            className="asset-prompt-menu"
            role="listbox"
            aria-label="引用参考图片"
            style={{
              left: position.left,
              top: position.top,
              width: position.width,
              maxHeight: position.maxHeight,
              transform: position.above ? 'translateY(-100%)' : undefined,
            }}
            onPointerDown={(event) => event.preventDefault()}
            onMouseDown={(event) => event.preventDefault()}
          >
            <div className="asset-prompt-menu-heading">
              <span>参考图片</span>
              <small>{candidates.length}</small>
            </div>
            <div className="asset-prompt-menu-items">
              {candidates.length ? (
                candidates.map((candidate, index) => (
                  <button
                    key={candidate.reference.media_id}
                    id={`${menuId}-${index}`}
                    type="button"
                    role="option"
                    aria-selected={index === active}
                    data-candidate={index}
                    tabIndex={-1}
                    className={index === active ? 'is-active' : undefined}
                    onPointerMove={() => setActive(index)}
                    onClick={() => selectReference(candidate)}
                  >
                    <ReferenceThumbnail reference={candidate.reference} />
                    <span>
                      <strong>图片{candidate.index + 1}</strong>
                      <small>{candidate.reference.name || '参考图片'}</small>
                    </span>
                  </button>
                ))
              ) : (
                <p className="asset-prompt-menu-empty">
                  {references.length ? '没有匹配的参考图片' : '请先添加参考图片'}
                </p>
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

function ReferenceThumbnail({ reference }: { reference: AssetPromptReference }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  return (
    <span className="asset-prompt-thumbnail">
      {reference.url && failedUrl !== reference.url ? (
        <img src={reference.url} alt="" onError={() => setFailedUrl(reference.url)} />
      ) : (
        <ImageIcon size={16} aria-hidden="true" />
      )}
    </span>
  );
}

function chipFallback() {
  const fallback = document.createElement('span');
  fallback.className = 'asset-prompt-chip-fallback';
  fallback.innerHTML =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
  return fallback;
}

function refreshChipMetadata(editor: HTMLElement, value: string, references: AssetPromptReference[]) {
  const chips = [...editor.querySelectorAll<HTMLElement>('[data-asset-prompt-token]')];
  const tokenCount = [...value.matchAll(/@图片([1-9]\d*)(?!\d)/gu)].filter(
    (match) => references[Number(match[1]) - 1],
  ).length;
  if (tokenCount !== chips.length) return false;
  if (
    chips.some((chip) => {
      const match = /^@图片([1-9]\d*)$/u.exec(chip.dataset.assetPromptToken ?? '');
      return !match || references[Number(match[1]) - 1]?.media_id !== chip.dataset.assetPromptMediaId;
    })
  )
    return false;
  for (const chip of chips) {
    const index = Number(chip.dataset.assetPromptToken!.slice(3)) - 1;
    const reference = references[index];
    chip.title = reference.name || `图片${index + 1}`;
    const image = chip.querySelector('img');
    if (reference.url) {
      if (image) {
        if (image.getAttribute('src') !== reference.url) image.src = reference.url;
      } else {
        const preview = document.createElement('img');
        preview.src = reference.url;
        preview.alt = '';
        preview.draggable = false;
        const fallback = chip.querySelector('.asset-prompt-chip-fallback');
        if (fallback) fallback.replaceWith(preview);
        else chip.insertBefore(preview, chip.lastChild);
      }
    } else if (image) image.replaceWith(chipFallback());
  }
  return true;
}

function referenceFragment(reference: AssetPromptReference, index: number) {
  const fragment = document.createDocumentFragment();
  const chip = document.createElement('span');
  chip.contentEditable = 'false';
  chip.className = 'asset-prompt-chip';
  chip.dataset.assetPromptToken = `@图片${index + 1}`;
  chip.dataset.assetPromptMediaId = reference.media_id;
  chip.title = reference.name || `图片${index + 1}`;
  const at = document.createElement('span');
  at.textContent = '@';
  chip.append(at);
  if (reference.url) {
    const image = document.createElement('img');
    image.src = reference.url;
    image.alt = '';
    image.draggable = false;
    chip.append(image);
  } else chip.append(chipFallback());
  const label = document.createElement('span');
  label.textContent = `图片${index + 1}`;
  chip.append(label);
  fragment.append(chip, document.createTextNode(' '));
  return fragment;
}

function promptNodes(value: string, references: AssetPromptReference[]) {
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const match of value.matchAll(/@图片([1-9]\d*)(?!\d)/gu)) {
    const index = Number(match[1]) - 1;
    if (!references[index]) continue;
    fragment.append(document.createTextNode(value.slice(cursor, match.index)));
    const chip = referenceFragment(references[index], index);
    chip.lastChild?.remove();
    fragment.append(chip);
    cursor = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(value.slice(cursor)));
  return fragment;
}

function serialize(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent?.replace(/\u00a0/g, ' ') || '';
  if (node instanceof HTMLElement) {
    if (node.dataset.assetPromptToken) return node.dataset.assetPromptToken;
    if (node.tagName === 'BR') return '\n';
  }
  return Array.from(node.childNodes).map(serialize).join('');
}

function pointOffset(root: Node, target: Node, offset: number): number {
  if (root === target)
    return root.nodeType === Node.TEXT_NODE
      ? offset
      : Array.from(root.childNodes)
          .slice(0, offset)
          .reduce((total, node) => total + serialize(node).length, 0);
  let total = 0;
  for (const child of root.childNodes) {
    if (child === target || child.contains(target)) return total + pointOffset(child, target, offset);
    total += serialize(child).length;
  }
  return total;
}

function readSelection(root: HTMLElement): Selection | null {
  const selection = window.getSelection();
  if (!selection?.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  return {
    start: pointOffset(root, range.startContainer, range.startOffset),
    end: pointOffset(root, range.endContainer, range.endOffset),
  };
}

function pointForOffset(root: Node, offset: number): { node: Node; offset: number } {
  if (root.nodeType === Node.TEXT_NODE)
    return {
      node: root,
      offset: Math.min(offset, root.textContent?.length ?? 0),
    };
  let remaining = Math.max(0, offset);
  for (const [index, child] of Array.from(root.childNodes).entries()) {
    const length = serialize(child).length;
    if (remaining > length) {
      remaining -= length;
      continue;
    }
    if (child instanceof HTMLElement && child.dataset.assetPromptToken)
      return { node: root, offset: index + (remaining > 0 ? 1 : 0) };
    return pointForOffset(child, remaining);
  }
  return { node: root, offset: root.childNodes.length };
}

function writeSelection(root: HTMLElement, selection: Selection) {
  const range = document.createRange();
  const start = pointForOffset(root, selection.start);
  const end = pointForOffset(root, selection.end);
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  window.getSelection()?.removeAllRanges();
  window.getSelection()?.addRange(range);
}

function menuPosition(editor: HTMLElement, cursor: number): Position {
  const range = document.createRange();
  const point = pointForOffset(editor, cursor);
  range.setStart(point.node, point.offset);
  range.collapse(true);
  const caret = range.getClientRects()[0] || range.getBoundingClientRect();
  const bounds = editor.getBoundingClientRect();
  const left = caret.height ? caret.left : bounds.left + 4;
  const top = caret.height ? caret.top : bounds.top + 4;
  const bottom = caret.height ? caret.bottom : top + 22;
  const viewport = window.visualViewport;
  const viewportLeft = viewport?.offsetLeft ?? 0;
  const viewportTop = viewport?.offsetTop ?? 0;
  const viewportWidth = viewport?.width ?? window.innerWidth;
  const viewportHeight = viewport?.height ?? window.innerHeight;
  const width = Math.min(320, Math.max(0, viewportWidth - 16));
  const below = Math.max(0, viewportTop + viewportHeight - bottom - 16);
  const above = Math.max(0, top - viewportTop - 16);
  const showAbove = below < 180 && above > below;
  const maxHeight = Math.min(320, Math.max(0, viewportHeight - 16), showAbove ? above : below);
  const menuTop = showAbove
    ? Math.max(viewportTop + maxHeight + 8, Math.min(top - 8, viewportTop + viewportHeight - 8))
    : Math.max(viewportTop + 8, Math.min(bottom + 8, viewportTop + viewportHeight - maxHeight - 8));
  return {
    left: Math.max(viewportLeft + 8, Math.min(left, viewportLeft + viewportWidth - width - 8)),
    top: menuTop,
    width,
    maxHeight,
    above: showAbove,
  };
}

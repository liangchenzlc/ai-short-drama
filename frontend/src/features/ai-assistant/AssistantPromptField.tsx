import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, type ComponentProps, type KeyboardEvent, type ReactNode } from 'react';
import { Input } from 'antd';
import { splitHighlightedText, tokenDeletionRange } from './prompt-tokens';

type TextAreaProps = ComponentProps<typeof Input.TextArea>;

const TokenContext = createContext<readonly string[]>([]);

/** 只提供高亮词表，输入框本身仍由 Sender 驱动，避免受控值被重建。 */
export function PromptTokenProvider({ tokens, children }: { tokens: readonly string[]; children: ReactNode }) {
  return <TokenContext.Provider value={tokens}>{children}</TokenContext.Provider>;
}

const GEOMETRY = [
  'boxSizing', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight',
  'textAlign', 'textIndent', 'textTransform', 'wordSpacing', 'tabSize', 'wordBreak', 'overflowWrap',
  'whiteSpace', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
] as const;

/**
 * 高亮层贴在 textarea 身后：文字仍由原生输入框渲染，选中、输入法、撤销都不受影响，
 * 只有已选词条获得底色。几何属性从输入框实测复制，两层换行才会完全一致。
 */
export function AssistantPromptInput({ value, onScroll, onKeyDown, className, ...props }: TextAreaProps) {
  const tokens = useContext(TokenContext);
  const shell = useRef<HTMLDivElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const text = typeof value === 'string' ? value : '';
  const parts = useMemo(() => splitHighlightedText(text, tokens), [text, tokens]);
  const sync = useCallback(() => {
    const source = shell.current?.querySelector('textarea');
    const target = mirror.current;
    if (!source || !target) return;
    const style = window.getComputedStyle(source);
    GEOMETRY.forEach(property => { target.style[property] = style[property]; });
    // 出现滚动条时内容宽度会变窄；高亮层补上同样的差值，换行位置才与输入框一致。
    const scrollbar = Math.max(0, source.offsetWidth - source.clientWidth);
    target.style.paddingRight = `${Number.parseFloat(style.paddingRight || '0') + scrollbar}px`;
    target.scrollTop = source.scrollTop;
  }, []);
  useLayoutEffect(sync);
  useEffect(() => {
    const source = shell.current?.querySelector('textarea');
    if (!source) return;
    const observer = new ResizeObserver(sync);
    observer.observe(source);
    return () => observer.disconnect();
  }, [sync]);
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    const composing = event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229;
    const deleting = event.key === 'Backspace' || event.key === 'Delete';
    if (!composing && deleting) {
      const area = event.currentTarget;
      const start = area.selectionStart ?? 0;
      const end = area.selectionEnd ?? start;
      if (start === end) {
        const range = tokenDeletionRange(text, tokens, start, event.key === 'Backspace' ? 'backward' : 'forward');
        if (range) {
          // 整词删除：直接改 DOM 值再派发 input，受控状态与光标位置一起落位。
          event.preventDefault();
          area.setRangeText('', range.start, range.end, 'end');
          area.dispatchEvent(new Event('input', { bubbles: true }));
          return;
        }
      }
    }
    onKeyDown?.(event);
  }
  return <div ref={shell} className="ai-assistant-field">
    <div ref={mirror} className="ai-assistant-field-mirror" aria-hidden="true">
      {parts.map((part, index) => part.token
        ? <mark key={index} className="ai-assistant-field-token">{part.text}</mark>
        : <span key={index}>{part.text}</span>)}
    </div>
    <Input.TextArea {...props} value={value} maxLength={32000} aria-label="给助手的消息"
      className={['ai-assistant-field-input', className].filter(Boolean).join(' ')}
      onKeyDown={handleKeyDown}
      onScroll={event => {
        if (mirror.current) mirror.current.scrollTop = event.currentTarget.scrollTop;
        onScroll?.(event);
      }}/>
  </div>;
}

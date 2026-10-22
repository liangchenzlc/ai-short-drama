import { useId, type ReactNode, type Ref } from 'react';
import { Button, Input, Tooltip } from 'antd';
import { Icon } from '../../components/ui/Icon';

export function GenerationComposer({ prompt, onPrompt, model, parameters, sendDisabled, sendTitle, busy, onSend, purpose = 'image', label, disabled = false, maxLength = 4000, feedbackRef, promptInput }: {
  prompt: string; onPrompt: (value: string) => void; model: ReactNode; parameters?: ReactNode;
  sendDisabled: boolean; sendTitle?: string; busy: boolean; onSend: () => void; purpose?: 'image' | 'video' | 'novel';
  label?: string; disabled?: boolean; maxLength?: number; feedbackRef?: Ref<HTMLDivElement>;
  promptInput?: (id: string) => ReactNode;
}) {
  const id = useId();
  const text = label ?? (purpose === 'novel' ? '创作要求' : purpose === 'video' ? '视频提示词' : '画面要求');
  return <div className={`generation-inputs generation-inputs-${purpose}`}>
    <label htmlFor={id} className={`generation-composer-label${purpose !== 'novel' ? ' visually-hidden' : ''}`}>{text}</label>
    <div className={`generation-composer generation-composer-${purpose}`}>
    {promptInput ? promptInput(id) : <Input.TextArea id={id} aria-label={text} value={prompt} onChange={event => onPrompt(event.target.value)}
      disabled={disabled} maxLength={maxLength} placeholder={purpose === 'novel' ? '描述你对本集剧本的期望，例如：保留主线、压缩支线，每集结尾留悬念……' : '描述本次生成要求'} />}
    <div className="generation-composer-footer"><div className="generation-composer-model">{model}</div>
      <div className="generation-composer-parameters">{parameters}</div>
      <Tooltip title={sendTitle}><span className="generation-composer-send-anchor" tabIndex={sendDisabled && sendTitle ? 0 : undefined} aria-label={sendDisabled ? sendTitle : undefined}>
        <Button type="primary" shape="circle" className="generation-composer-send" aria-label={purpose === 'novel' ? '生成剧本' : purpose === 'video' ? '生成视频' : '生成图片'}
          loading={busy} disabled={sendDisabled} icon={<Icon name="arrow" size={18} style={{ transform: 'rotate(-90deg)' }}/>} onClick={onSend}/>
      </span></Tooltip>
    </div>
  </div>{feedbackRef && <div ref={feedbackRef} className="generation-composer-feedback"/>}</div>;
}

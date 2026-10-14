import { useId, type ReactNode } from 'react';
import { Button, Input } from 'antd';
import { Icon } from '../../components/ui/Icon';

export function GenerationComposer({ prompt, onPrompt, model, parameters, sendDisabled, busy, onSend, purpose = 'image', label, disabled = false, maxLength = 4000 }: {
  prompt: string; onPrompt: (value: string) => void; model: ReactNode; parameters?: ReactNode;
  sendDisabled: boolean; busy: boolean; onSend: () => void; purpose?: 'image' | 'video' | 'novel';
  label?: string; disabled?: boolean; maxLength?: number;
}) {
  const id = useId();
  const text = label ?? (purpose === 'novel' ? '改编要求' : purpose === 'video' ? '视频提示词' : '画面要求');
  return <div className={`generation-inputs generation-inputs-${purpose}`}>
    <label htmlFor={id} className={`generation-composer-label${purpose !== 'novel' ? ' visually-hidden' : ''}`}>{text}</label>
    <div className={`generation-composer generation-composer-${purpose}`}>
    <Input.TextArea id={id} aria-label={text} value={prompt} onChange={event => onPrompt(event.target.value)}
      disabled={disabled} maxLength={maxLength} placeholder={purpose === 'novel' ? '描述本集改编要求' : '描述本次生成要求'} />
    <div className="generation-composer-footer"><div className="generation-composer-model">{model}</div>
      <div className="generation-composer-parameters">{parameters}</div>
      <Button type="primary" shape="circle" className="generation-composer-send" aria-label={purpose === 'novel' ? '生成剧本' : purpose === 'video' ? '生成视频' : '生成图片'}
        loading={busy} disabled={sendDisabled} icon={<Icon name="arrow" size={18} style={{ transform: 'rotate(-90deg)' }}/>} onClick={onSend}/>
    </div>
  </div></div>;
}

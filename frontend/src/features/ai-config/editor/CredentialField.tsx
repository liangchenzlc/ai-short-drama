import { Eye, EyeOff } from 'lucide-react';
import { useState } from 'react';

const MASK = '••••••••';
export function CredentialField({ label, value, stored, cleared, disabled, onChange }: {
  label: string; value: string; stored: boolean; cleared: boolean; disabled: boolean;
  onChange: (value: string, cleared: boolean) => void;
}) {
  const [visible, setVisible] = useState(false);
  const masked = stored && !cleared && !value;
  return <label className="config-credential-field"><span>{label}</span><span className="config-credential-input">
    <input aria-label={label} type={visible && !masked ? 'text' : 'password'} value={masked ? MASK : value}
      disabled={disabled} autoComplete="new-password" placeholder="可选"
      onFocus={event => { if (masked) event.currentTarget.select(); }}
      onKeyDown={event => { if (masked && ['Backspace', 'Delete'].includes(event.key)) { event.preventDefault(); onChange('', true); } }}
      onChange={event => { const next = event.target.value.replace(MASK, ''); onChange(next, !next); }} />
    <button type="button" className="icon-button" aria-label={visible ? `隐藏${label}` : `显示${label}`} disabled={disabled || masked || !value} onClick={() => setVisible(old => !old)}>
      {visible ? <EyeOff size={16} /> : <Eye size={16} />}
    </button>
  </span></label>;
}

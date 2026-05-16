import type React from 'react';
import { useState } from 'react';
import { Input, Select } from '../common';

const MANUAL_VALUE = '__manual__';

interface LiteLLMModelSelectProps {
  fieldKey: string;
  value: string;
  onChange: (key: string, value: string) => void;
  yamlModels: string[];
  disabled?: boolean;
  label?: string;
  description?: string;
  placeholder?: string;
}

/**
 * Combo-style model picker: shows a dropdown of YAML-declared model names and
 * falls back to a free-text input when the user picks "手动输入" or the current
 * value isn't in the YAML list.
 */
const LiteLLMModelSelect: React.FC<LiteLLMModelSelectProps> = ({
  fieldKey,
  value,
  onChange,
  yamlModels,
  disabled = false,
  label,
  description,
  placeholder = '例如 gemini/gemini-2.0-flash',
}) => {
  const inYaml = yamlModels.includes(value);
  const [manualMode, setManualMode] = useState(!inYaml);

  const handleSelectChange = (selected: string) => {
    if (selected === MANUAL_VALUE) {
      setManualMode(true);
      return;
    }
    setManualMode(false);
    onChange(fieldKey, selected);
  };

  const handleTextChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(fieldKey, e.target.value);
  };

  const selectOptions = [
    ...yamlModels.map((m) => ({ value: m, label: m })),
    { value: MANUAL_VALUE, label: '── 手动输入 ──' },
  ];

  const commonClass =
    'h-11 w-full rounded-xl border settings-border bg-input-surface px-3 text-sm text-foreground ' +
    'focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:cursor-not-allowed disabled:opacity-50';

  return (
    <div className="space-y-2">
      {label ? (
        <p className="text-sm font-medium text-foreground">{label}</p>
      ) : null}
      {description ? (
        <p className="text-xs leading-5 text-muted-text">{description}</p>
      ) : null}

      {yamlModels.length > 0 ? (
        <div className="space-y-2">
          <Select
            value={manualMode ? MANUAL_VALUE : (value || '')}
            onChange={handleSelectChange}
            options={selectOptions}
            placeholder="从 litellm_config.yaml 中选择..."
            disabled={disabled}
          />
          {manualMode ? (
            <Input
              value={value}
              onChange={handleTextChange}
              placeholder={placeholder}
              disabled={disabled}
              className={commonClass}
            />
          ) : null}
        </div>
      ) : (
        <input
          type="text"
          value={value}
          onChange={handleTextChange}
          placeholder={placeholder}
          disabled={disabled}
          className={commonClass}
        />
      )}

      {yamlModels.length === 0 ? (
        <p className="text-xs text-muted-text">
          未检测到 litellm_config.yaml 模型列表，请直接输入模型名称。
        </p>
      ) : null}
    </div>
  );
};

export default LiteLLMModelSelect;

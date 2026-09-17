import { useCallback, useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { OllamaProviderSettings } from '../OllamaProviderSettings';
import '../../pages/settings-page.css';

/** Setup uses the same endpoint/model editor as Settings, including immediate saves. */
export function OllamaSetupCard() {
  const [primary, setPrimary] = useState(false);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    const status = await api.get<{ primary_provider: string }>('/settings/api-keys/status');
    setPrimary(status.primary_provider === 'ollama');
    setError('');
  }, []);

  useEffect(() => {
    let active = true;
    void api.get<{ primary_provider: string }>('/settings/api-keys/status').then(status => {
      if (active) setPrimary(status.primary_provider === 'ollama');
    }).catch(error => {
      if (active) setError(error instanceof Error ? error.message : 'Could not load provider status.');
    });
    return () => { active = false; };
  }, []);

  return <div className="settings-pane setup-ollama">
    <OllamaProviderSettings isPrimary={primary} onChanged={refresh} onSetPrimary={async () => {
      await api.post('/settings/primary-provider', { provider: 'ollama' });
      await refresh();
    }} />
    {error && <p role="alert" className="text-xs text-(--status-error) mt-2">{error}</p>}
  </div>;
}

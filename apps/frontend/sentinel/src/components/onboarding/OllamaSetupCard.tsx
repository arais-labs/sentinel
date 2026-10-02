import { useCallback, useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { OllamaProviderSettings } from '../OllamaProviderSettings';
import '../../pages/settings-page.css';

/** Setup uses the same endpoint/model editor as Settings, including immediate saves. */
export function OllamaSetupCard() {
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    await api.get('/settings/api-keys/status');
    setError('');
  }, []);

  useEffect(() => {
    let active = true;
    void api.get('/settings/api-keys/status').catch(error => {
      if (active) setError(error instanceof Error ? error.message : 'Could not load provider status.');
    });
    return () => { active = false; };
  }, []);

  return <div className="settings-pane setup-ollama">
    <OllamaProviderSettings onChanged={refresh} />
    {error && <p role="alert" className="text-xs text-(--status-error) mt-2">{error}</p>}
  </div>;
}

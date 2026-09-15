# Provider connections

Configure model providers in **Settings → LLM Providers** or during onboarding.
Credentials are encrypted in the selected instance's settings. Use the login
options offered for each provider; credentials for one OAuth client are not
interchangeable with another client's credentials.

| Provider | Connection options |
| --- | --- |
| Anthropic | API key or supported Claude OAuth credentials. |
| OpenAI | API key or supported Codex OAuth credentials. |
| Gemini | API key or Antigravity OAuth credentials. |

## Gemini OAuth

Sign in to Antigravity with `agy`, then choose **Google Gemini → OAuth → Import
from Antigravity CLI** in Sentinel. The import reads the Antigravity credential
from macOS Keychain and saves refreshable credentials to the selected instance.
It does not change the CLI's credential store. Where automatic import is not
available, use the credential import fields shown in Settings.

A Gemini CLI token is not an Antigravity token. Import from the application named
by the selected connection option. API keys use the public Gemini API; OAuth
requests use Antigravity's service.

## Development verification

Regular tests should not use a developer's personal provider login. Live provider
tests contact external services and require explicit opt-in and test credentials.
Consult the test configuration before running them. Model availability depends
on the provider and the account used.

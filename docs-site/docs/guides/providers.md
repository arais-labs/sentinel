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
| Ollama | Local or remote server URL, with an optional bearer token. |

Chats and [Voice](voice.md) use the same configured providers. Voice's local speech
models are separate from the LLM provider selected for its agent loop.

## Gemini OAuth

Sign in to Antigravity with `agy`, then choose **Google Gemini → OAuth → Import
from Antigravity CLI** in Sentinel. The import reads the Antigravity credential
from macOS Keychain and saves refreshable credentials to the selected instance.
It does not change the CLI's credential store. Where automatic import is not
available, use the credential import fields shown in Settings.

A Gemini CLI token is not an Antigravity token. Import from the application named
by the selected connection option. API keys use the public Gemini API; OAuth
requests use Antigravity's service.

## Ollama

Configure the server root URL, optional bearer token, and discover/select a tool-capable
model in **Settings → LLM Providers → Ollama → Configure/Update**. HTTP and HTTPS
endpoints are supported, including remote servers and path-prefixed proxies.
For remote servers, use HTTPS when sending credentials or private content.
The selected Ollama model is used in all three tiers; it is selectable in chats and Voice.
Changing the server URL never silently reuses a token saved for a different endpoint.

The expanded card lists models from the selected server automatically. Select a model
and **Save provider**, or use **Add model** to download by name. The same native Ollama
API manages both local and remote servers; no local CLI is required. Downloads run in
backend-owned, instance/endpoint-scoped tasks and continue when the pane or page closes.
Progress includes bytes and download/verification stages; reopening reconnects to it.

Each model has a removal button with confirmation naming the model and server.
Removing the selected model clears that provider's selection but retains its endpoint
and credentials. Disconnecting the provider only clears connection settings and never
deletes models. Servers must expose/permit the native pull/delete APIs for those actions.
Sentinel never installs, starts, stops, updates, or uninstalls the Ollama server itself.
Sentinel shutdown closes its own HTTP download connections, not the Ollama server;
retry an interrupted download to resume it.

Upstream: [Ollama chat protocol](https://docs.ollama.com/api/chat),
[Ollama model discovery](https://docs.ollama.com/api/tags).

## Development verification

Regular tests should not use a developer's personal provider login. Live provider
tests contact external services and require explicit opt-in and test credentials.
Consult the test configuration before running them. Model availability depends
on the provider and the account used.

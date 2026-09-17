---
title: Voice
---

# Voice

Voice is an app-level control for the current instance's main chats. It never creates a
session for itself. It can list chats, read recent replies, create a chat, send
instructions, and interrupt work. Instructions use the usual Chat steering queue;
stopping a chat does not delete it.

## Composer dictation

The microphone beside Attach records speech directly into the chat draft using
local Whisper. Click once to record: short pauses transcribe into the draft as you
speak, with continuous speech submitted in chunks of up to eight seconds. Click
again to stop and transcribe the remainder. Nothing is sent automatically and no
Voice agent or LLM is called. Recordings stop after 25 seconds. Cancel or switch
chats to discard untranscribed audio; text already inserted stays in the draft.
The input meter shows microphone activity. Local speech must be installed in Voice settings.

While dictating, the app-level Voice listener and speech playback are paused so
dictated text is not interpreted as an agent instruction. The existing Voice
connection stays intact. Microphone access and the temporary speech-runtime lease
are released when dictation finishes or is cancelled.

## Setup

Open **Voice** using the waveform icon centered in the top app bar. The icon expands
into a compact control island with microphone, connection, speaker, Settings, and
close controls; an interrupt button appears during playback. Smoke and sentence-by-sentence
captions below the ring float in the center of the window on a feathered veil (at most one-third
of desktop window width). The controls stay in the top bar.
Escape, clicking outside, or the close button dismisses the veil and collapses the
island without disconnecting Voice. Use the power button to disconnect.
The workspace stays visible and interactive. Configure an LLM in
**Settings → LLM Providers**. Voice uses the shared Fast tier by default;
**Settings → Voice → Model & reasoning** reuses chat's provider, Fast / Normal / Deep Think,
and supported reasoning and fast-mode controls. It can pin any configured provider
or **Use default provider**. Connection and model management belong to
[Provider connections](providers.md), not the speech runtime.
Selections persist per instance, independently of chat settings, and apply to the next
voice request without reconnecting or interrupting the current reply.
Pinning a provider disables cross-provider fallback, like the chat selector.
The default selection follows the configured primary and fallback providers.

Voice is a full Sentinel agent. It runs through the same runtime, context builder,
tool registry, approvals, persistence and compaction as a chat, in the `voice` agent mode,
which layers the spoken-output contract and coordination guidance on the shared prompt.
Its conversation is one hidden session per instance (`sessions.kind = "voice"`): it never
appears in the chat list, and is reset from the top-bar island with **Reset Voice conversation**.
The island's workspace control attaches a machine workspace to Voice, exactly as a chat's
header pill does; the runtime, files and terminal tools then act inside it. The attachment is
a Voice setting and survives a reset. Every message and tool result is recorded there, so Voice
remembers across turns and its history compacts like any chat.

Voice has every tool a chat has, plus two flags reserved for it: `chats` gains `create` and
`stop`, and `session_layout` gains `switch_chat`. A Voice-only action is absent from a chat's
tool schema and rejected if a chat calls it. Chats and Voice share one `chats` tool for
cross-chat context: `list` finds chats by title, `activity` shows each main chat's running
state, last five messages and three recent tool summaries, `search` finds saved conversation
text, `history` pages through a chat's messages, and `send` delivers a message to another
agent. The Voice conversation itself is not searchable by chats. If work ownership is
uncertain, Voice asks which chat you mean before sending work or changing a workspace.

In **Settings → Voice**, click **Install local speech engine**. Sentinel uses its bundled Python to create
an isolated environment, install `faster-whisper==1.2.1` and `kokoro-onnx==0.6.1`,
and download Whisper base plus Kokoro v1.0 int8 and its voices (~121 MB extra).
Existing Whisper files are reused on upgrade. Recognition and replies default to English.
No separate Python installation, compiler or speech server is needed.
Install status appears in Settings; installation continues if it closes.

Everything speech-related lives under `<SENTINEL_STORAGE_ROOT>/voice/`:

```
voice/
  runtime/          isolated Python and speech dependencies
  model/            Whisper base model
  speech/           Kokoro int8 model and voices
  cache/            download cache
  tmp/              installer temporary files
  installed.json    completion marker written after model validation
```

This directory is shared by instances. **Settings → Voice → Remove local voice
data** stops the worker, cancels installation, and deletes this directory. Chats,
traces, app dependencies, provider settings, and separately managed model servers are preserved.

## Lifecycle and privacy

After setup, opening Voice starts the speech worker and microphone automatically.
First use needs microphone permission. Each voice connection has a lease;
disconnecting the last connection stops the worker. Closing the popover keeps a
connected voice loop listening and speaking; the top-bar icon retains its status.
Abandoned leases expire after 60
seconds, checked every 15 seconds. Backend shutdown terminates the worker. If its
parent dies, pipe EOF also stops it during inference. Speech uses a private pipe,
not a listening port.

**Microphone** in Voice settings picks the input device for Voice and composer
dictation on this computer; *System default* follows the operating system. A device that
delivers only digital silence for a few seconds is reported instead of shown as listening.
On Apple Silicon MacBooks the built-in microphone is disabled while the lid is closed, so
docked use needs an external microphone, AirPods, or an iPhone via Continuity.

On macOS the desktop explicitly requests native microphone permission before
starting Whisper. If permission is denied, Voice offers **Open microphone settings**
and explains how to enable Sentinel (or **Electron** in development) under Privacy
& Security → Microphone. It rechecks on return and reconnects when access is granted;
macOS may require quitting and reopening the app after a permission change. Device
policy restrictions, missing microphones, and capture failures have distinct guidance.
Permission failure releases any acquired worker lease; no microphone is kept open.
Desktop IPC changes require restarting the desktop shell, not just refreshing the pane.

Switching chats, layouts, or pane focus does not own or restart the voice loop.
Switching instances or hiding the app releases its microphone
and connection. Returning reconnects if Voice was enabled. Agent work already
dispatched to chats continues independently.
Mute discards input. The smoke continues moving during setup and disconnection,
respecting reduced-motion and hidden-window preferences.

Speech model downloads need internet once. Recognition and synthesis then run offline.
The speech worker inherits no provider credentials. Raw recordings stay in memory.
Transcripts, the Voice conversation, and whatever chat context Voice reads go to the
selected LLM provider; choosing a cloud or remote provider means those texts leave this
computer. The Voice conversation persists in the instance database until you reset it.

All Voice replies use plain spoken English: no asterisks, Markdown,
headings, lists, or code blocks. A final formatting cleanup removes common Markdown
before the reply reaches the overlay or speech synthesizer.

Replies use local Kokoro with the American English `af_heart` voice, defaulting to 1.05× speed.
**Settings → Voice → Speech speed** adjusts synthesis from 0.75× to 2×, saved per
instance and applied to the next spoken reply without reconnecting. A reply keeps the
same speed across its chunks.
Voice streams public progress narration as well as the final reply through its own endpoint,
using Sentral's existing runtime events without modifying Sentral. Completed phrases can start
synthesis while the model continues or tools run. Reasoning and tool arguments are never speech.
Progress describes intent or ongoing work; success must follow a confirmed tool result.
Short initial chunks grow gradually; one next chunk is generated while audio plays.
Speaking over a reply interrupts playback and its in-flight Voice request after about 200 ms of sustained microphone
activity and captures the new request, retaining its opening words. Microphone echo
cancellation and a higher onset threshold reduce speaker feedback and brief-noise triggers;
headphones help if speaker audio still leaks into the microphone.
Queued speech is discarded on interruption. Work already dispatched to a chat is not undone;
Voice does not automatically retry interrupted actions. Disabling the speaker only silences
delivery, without cancelling the request. Connection-pool settings are unchanged.
Interrupting, muting replies, or disconnecting Voice cancels playback and queued chunks.
There is no system-voice or cloud fallback; playback errors leave the text visible.
Chat agents still use their own configured providers.

## Interaction and limits

Try “What is running?”, “Start a chat called Review and ask it to review my code”,
“Tell Review to focus on the backend”, “How is Review doing?”, or “Stop Review”.
It can also switch the visible chat and arrange that chat's workspace: “Show Review”,
“Put Terminal to the right of Chat”, “Make Files wider”, “Maximize Chat”, or
“Undo that layout change”. Voice inspects the actual layout before arranging it and
waits for the requesting window to acknowledge each change. Failed arrangement batches
roll back. Switching chats and hiding panes never stops their agents or processes.
The UI bridge stays connected when the Voice overlay closes; it cannot change another
instance or window. If the focused chat changes mid-request, layout edits are rejected
until Voice inspects again. No new voice session is created.
There is no chat selector. Voice reads the displayed chat from the window when it inspects
the workspace, and `chats` gives it every other chat. `chat_id` identifies a conversation;
`pane_id` identifies a view in that conversation's workspace; `view_type` identifies the
kind of view.

## Voice traces

Settings → Voice → Activity & traces provides a live, per-instance trace viewer.
Speech stages (recognition and synthesis) are traced here; the conversation itself is
the Voice session's own history. Each speech trace shows its input, output, timings,
tool arguments/results, provider-reported reasoning/usage/model metadata, timings,
errors and final spoken output. Reasoning is only available when the provider returns
it; hidden internal reasoning and provider-internal transport retries are not exposed.
Transcription and individual synthesis traces link to their conversation turn;
client playback events distinguish completed, interrupted, skipped and failed audio.
The viewer uses the Session Logs surfaces and shared tool inspector: select a request
on the left, then browse its readable timeline on the right, both newest first. Filter or search input,
context, model steps, actions, output and errors. Calls and results are paired; repeated
runtime/checkpoint representations are kept in the optional Raw events view. Context,
instructions and full payloads expand on demand. Export retains the complete trace as
JSON without preview truncation.
Streaming deltas are coalesced in short batches within each content block to avoid
a database write per token; their text is retained, but individual token timings are not.

Traces persist in the instance database, not as chat sessions. They start with new
activity; prior Voice conversations cannot be reconstructed retroactively. Partial
events survive interrupted requests. A hard backend termination can leave a trace
marked running with no terminal event; the viewer explicitly identifies this condition.
Raw audio, media bytes and structured credential/signature fields are omitted. Trace
text still contains private prompts, chat context and tool output: treat exports as
sensitive. Logs never get fed back into the Voice model as conversation history.
The schema is installed through the normal instance migrations.

## Talking to other agents

“Ask Review to check the build and report back” uses `chats` with `action=send`,
`target=<chat id>` and the message. The message is stored in that chat as a system notice
(`source: agent_message`) and wakes the chat if it is idle, exactly like a steering
message. The chat answers by sending a message back with `target="voice"`; that message
wakes Voice, which speaks it. There is no request ID, report table, acknowledgement or
polling: the ongoing conversation on each side decides what happens next. Any agent can
message any main chat or Voice this way; a delegated sub-agent can only reach its parent
or a sibling task. Other agents' messages are data, never instructions.

Actions that need approval show an approval card in the Voice overlay, which opens by
itself; approve or deny on screen. Voice never claims an approved or completed result
before the tool confirms it. **Interrupt reply** stops speech and the Voice run.

This baseline is continuous **turn-based** voice. About 650 ms of silence ends an
utterance; recordings are capped at 25 seconds. Input is ignored while the controller
is transcribing or running tools, but speaking during a reply interrupts it and starts
the next utterance. **Interrupt reply** also resumes listening. This is not
yet full-duplex speech-to-speech. Latency depends on hardware and cold model loading.
The baseline reads each main chat's last five text messages and at most three tool summaries. It does not
directly control subagents or delete chats.

## Development

Implementation is scoped to `app/services/voice/`, `app/routers/voice.py`,
`VoicePage.tsx`, `TopBarVoice.tsx`, `VoiceSettings.tsx`, `VoiceTraceLog.tsx`, their stylesheets and audio helpers. `InstanceClouds` supplies the overlay's ring;
the top bar owns its controls independently of workspace panes. macOS packaging declares microphone
access. Providers are configured through the existing per-instance settings and
take effect when the runtime context is rebuilt. Voice uses the shared provider layer;
it does not own provider installation, credentials, or server lifecycle.
Chat and Voice share `app/services/sessions/steering.py` for instruction delivery;
Voice supplies provenance and report IDs, while the sessions service owns queueing and wakeups.

Upstream: [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx).

# Voice in the house

Status: partly implemented. The plug-in `voice` (`plugins/tools/voice/`) has the TTS service, the satellites and their
cards, say/play/stop/volume, the replies and chimes, "Ask the house", push-to-talk and the sample recorder. Speech
recognition, the wake-word match and the command logic run in a separate voice service (`voice-vad`) that this
repository does not ship: the plug-in only talks to it (below). The planned parts are at the end of this page.

## Principle: everything on the server

A satellite is thin: it sends the microphone stream (after its own wake word), plays the audio it is given, and has a
mute and a volume. TTS, the sounds, routing, per-satellite settings and the replies live in the house. Firmware gets no
business logic: no baked sounds, no local command list. The wake word stays ON THE DEVICE: audio goes to the voice
service only after the wake, never as a continuous stream.

The interface of a satellite (MQTT, under its own node prefix):

| topic | direction | what |
|---|---|---|
| `<node>/status` | from the node | `online` / `offline` |
| `<node>/wake` | from the node | a wake word was heard |
| `<node>/say/url` | to the node | stop what plays, play this WAV URL |
| `<node>/stop/command` | to the node | `PRESS`: stop |
| `<node>/volume/set` | to the node | 0..100 |
| `<node>/volume/state` | from the node | the volume (another topic with `volume_state`) |
| `<node>/listen/command`, `<node>/listen/state` | both | `ON` / `OFF`: the microphone listens or is muted |
| `<node>/listen_once/command` | to the node | seconds: stream once without a wake word (a node with `listen_once`) |
| `<node>/player/state` | from the node | `playing` / `idle` |

ONE audio format: everything the house sends to a satellite (TTS and the confirmation sounds) is WAV PCM 16 kHz,
16-bit, stereo (the mono voice repeated on both channels), made ON THE SERVER. A satellite with `lead_in` gets every
sound behind 0.4 s of faint noise (+-100 counts, fixed seed): some codec paths mute on digital zeros and open late, which
cuts the first syllable or beep.

On a wake the HOUSE stops the playback (`stop/command`, only when something plays): the node does not decide that.

## TTS service (`service/tts`, container `tts`)

Silero on the CPU, two threads. Russian (`v5_ru`, default `ru-xenia`) and English (`v3_en`, default `en-en_99`); any
Silero speaker may be asked for as `<language>-<speaker>`, and the language follows the phrase when none is given. The
models are NOT in the image or the repository: the service downloads them once into the docker volume `voice-models`.
Numbers are written out in words first (`numwords.py`). **The Silero models are CC BY-NC-SA 4.0: non-commercial use only**
(NOTICE.md). The container has `mem_limit: 2g`.

- `POST /say {text, lang?, voice?, lead_in?}` with the header `X-Voice-Key: <VOICE_TTS_KEY>` -> `{url: "/a/<name>.wav",
  voice, seconds, ms}`. The name is 128 random bits; the file lives 10 minutes. Without a key the service refuses.
- `GET /a/<name>.wav`, `GET /s/<sound>.wav` (`listening`, `done`, `error`, `thinking`; `<sound>_lead.wav` with the
  lead-in), `GET /health`.

It runs only with `COMPOSE_PROFILES` containing `voice`, on the compose network only (no port of its own). `make install`
generates `VOICE_TTS_KEY`. The satellites fetch the files from the dashboard's port:
`http://<BIND_ADDRESS>:<DASHBOARD_PORT>/plugins/voice/tts/a|s/<name>.wav` (open: a file's random name is its secret).
Without `BIND_ADDRESS` the house cannot speak to a satellite.

The same service can run on another computer of the house: Settings -> Computers and servers -> Speech (TTS), Install
and Use (plug-in `computers`). It listens there on loopback only, with no key (`VOICE_TTS_OPEN=1`); the house reaches it
through the helper's ssh tunnel (settings `voice.tts`) and still hands its files to the satellites.

## Satellites and cards

`PUT /api/voice/satellites/<id>` (house token) makes or changes one:
`{node, mic, player, name?, room?, peer?, voice?, volume_state?, lead_in?, chime?, wake_chime?, listen_once?, quiet_fail?,
need_wake?, panel?, phone?}`.

- `peer` is the address the satellite's audio comes from: the reply goes to the satellite of that address.
- `voice` is the TTS voice of its answers; `volume_state` the topic of its volume when it is not `<node>/volume/state`.
- A node name may not be a topic of the house, of a plug-in, of a device or of another satellite (409).
- `GET` lists them with their counters (wakes, commands, errors, last wake); `DELETE` removes one with its cards.
- Saved in `settings.json` under `voice.satellites` (`setup/settings.example.json`).

Each role is a card in the group "voice", on the dashboard tab "Voice / Sound":

- `<id>_mic`: Listening (on / muted, through `<node>/listen/command`, so the device stops streaming), the time of the
  last wake, and the state of a background recording.
- `<id>_spk`: what it does now (playing / idle), a Volume slider, Stop and Test sound. The channels `say` (a text) and
  `play` (an http URL) are under advanced and work from the API, a rule and MQTT: `home/cmd/<id>_spk/say|play|stop|volume`.
- A device that is both shows a mic card and a speaker card. The window of either has a "Voice settings" section (name,
  room, the audio address, the voice, the flags, the counters).

API (house token): `POST /api/voice/say {text, target, lang?, voice?}`, `POST /api/voice/play {url, target}`,
`POST /api/voice/stop {target}`, `POST /api/voice/volume {target, value}`. `target` is a satellite id, a room or `all`.
`GET /api/voice/status` gives the TTS health, the public address and whether the key is set.

**A tablet as a satellite.** A satellite with `panel` (the tablet's device id) is driven through the panels plug-in: the
same commands go as one JSON line on `panel/<id>/voice/cmd`, the tablet takes them by a long-poll
(`GET /api/panels/voice/next`, the tablet's own key) and reports wake, player, listen and volume to
`POST /api/panels/voice/event`. Those come out on the topics of a node `panel-<id>`, so the plug-in sees one kind of
satellite. A command older than 30 s is dropped.

**One room, one answer.** When two satellites of one room wake within 1.5 s, the house acts only on the first; the
second gets no stop and no chime.

## Replies and chimes

The voice service publishes what it did on `voice/heard`
`{ip, ok, reply?, sound?, ask?, quiet?}`. The plug-in answers on the satellite whose `peer` is `ip`:

- `sound` (for example `thinking`) is only played, when the satellite's `chime` is on;
- else `reply` is spoken (also when `ok` is false, "I did not understand");
- else, unless `quiet` (an understood command already confirmed by a beep), the `done` or `error` chime.
- `quiet_fail` ("Silent when not understood"): a command that was not understood plays nothing; a question is still
  asked.
- `wake_chime` (off by default): the `listening` sound after a wake.

**One sound at a time.** The house never cuts its own reply. Per satellite a `listening` or `thinking` sound is dropped
while anything plays or waits; a reply or a chime queues and goes out when the node reports idle (a node that never
reports is taken as idle after 10 s). A new wake, Stop, or an explicit play from the API cuts and replaces.

**A question must be heard.** With `ask: true` the reply is a question. The satellite must start playing it within 6 s,
else the house publishes `voice/ask_failed {ip}` and the question is cancelled. When the question has played, a
satellite with `listen_once` gets `listen_once/command 6`: its mic opens once for the answer.

## The voice service (`voice-vad`, outside this repository)

The plug-in reaches it at `http://voice-vad:8093` with the header `X-Voice-Key: <VOICE_TTS_KEY>`. It needs these paths:
`/ask`, `/confirm`, `/chat` (streamed text), `/utter` (raw audio), `/models`, and `/record/*` for the recorder. The
service reads its settings every 10 s from `GET /api/voice/config` (house token) and publishes `voice/heard`. Without it
the satellites still play, but nothing is recognized and "Ask the house" answers 503.

How it recognizes speech (the recognizer, the command map, the model prompt) is the service's own and is not described
or tested here.

## Settings -> Voice ("Wake word and answers")

`GET/PUT /api/voice/config` (house token), saved in `settings.json` under `voice.config`. No restart: the voice service
reads it every 10 s.

- `wake`: the wake word (one word); `wake_maxdist`: how many letters a heard word may differ (at most `(length - 1) // 2`
  and 3: 7 letters -> 3, 4 -> 1); `wake_prefix`: a heard word that starts so wakes (3 letters or more, empty = off);
  `wake_variants`: how it is also heard (a word of 2+ letters, or a stem of 3+ letters and `*`).
- `llm` (smart answers on/off: off = no model is loaded or polled), `llm_model`, `big_model` (`POST /api/voice/models`
  lists what the model hosts have), `llm_timeout`, `confirm_all_off` (ask before "turn everything off"), `ask_ttl`
  (seconds a question waits for yes / no).
- A satellite with `need_wake` acts only on a phrase that holds the wake word; the GET lists their `peer`s as
  `need_wake`. A save without the field keeps it.
- `lang` in the GET is the house language (Settings -> House language): the answers' language.
- The voice service's first read seeds every field once from what it runs (`PUT ?seed=1`). A page save before that is
  refused (409).

## Ask the house

The header's Ask opens a chat window (house token). A typed phrase goes to the voice service (`POST /api/voice/ask
{text, speak?}`): it takes the road of a spoken phrase after recognition. When the service asks a question, Yes / No
buttons send `POST /api/voice/ask/confirm {token, yes}`. "Say the answer aloud" plays the answer on a chosen speaker.
When the text is no command, `POST /api/voice/chat {text, history}` streams a chat answer of the model as plain text;
nothing of it is acted on.

## Push-to-talk (phones)

A phone is a satellite with `phone: true` (id `phone_<name>`, one mic card with the last talk time). The first talk of
a phone makes its satellite. `POST /api/voice/ptt?sat=<id>` takes the recording (16 kHz mono 16-bit little endian, at
most about 20 s) as the body and sends it to the voice service with no wake word. The answer is the service's JSON plus
`audio`: the address of the spoken reply or question, else the done or error chime. `POST /api/voice/tts {text}` gives the
speech of a chat answer. A phone is online for an hour after its app last reported (`voice/phone/<uid>/seen`, from the
family plug-in) or talked. Who may call ask, chat, ptt and tts: the house token and a full-access phone; a phone with
per-device rights and a panel key are refused.

## Recording samples

The toolbar of the Voice / Sound tab has "Voice recording": samples of the wake phrase and other words, recorded on a
satellite with `listen_once` and a `peer` (`POST /api/voice/record`: a beep, 5 s of raw audio, the first 3 s kept) or in
the browser / on a phone (`POST /api/voice/record/sample`). The voice service stores them; `GET /api/voice/record/...`
lists, plays and `POST /api/voice/record/delete` deletes them.

Background recording (`POST/GET/DELETE /api/voice/background`, house token): the raw audio of one satellite's room is
saved, never recognized or acted on. Two kinds: `quiet` (a given time from now, up to 12 h) and `tv` (armed: it starts
when the TV of the room turns on and ends when it turns off, at most two hours, once). One session at a time; the
sessions survive a restart (`plugin-state/voice/background.json`).

## Not implemented yet

- Players other than ESP nodes and tablets: a Linux box (PipeWire) and a TV toast.
- `voice-vad` and the recognizer as part of this repository (`make update` neither builds nor versions them).
- The satellite firmware in the build path.
- A command-map editor in the dashboard.
- A level meter on the mic card.
- A custom wake word trained for the house (from the recorded samples) for every satellite.
- Grammar-constrained model output.
- Personal assistant features (calendar, news digest, personal mail read aloud). Hard rules for them: the reading
  pipeline has no action rights in the house; while content is read aloud that satellite accepts no voice command except
  "stop".

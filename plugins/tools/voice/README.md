# voice

The house answers by voice. The design, the principle (thin satellites, the wake word on the device, everything else on the server) and
the plan are in [docs/voice.md](../../../docs/voice.md). This is step 1: the TTS service.

## TTS service (`service/tts`, container `tts`)

Silero on the CPU (two threads), the models kept loaded and warmed at start with each language's default voice (`ru-xenia`,
`en-en_99`; any Silero speaker may be asked for - each satellite's voice on the Voice page; the models `v5_ru.pt` 139 MB and `v3_en.pt` 54 MB
are downloaded once into the docker volume `voice-models`). A 2 s answer takes under 0.1 s. Numbers are written out in words before synthesis. **The Silero models are
CC BY-NC-SA 4.0: non-commercial use only** (see NOTICE.md); the container needs about 1.1 GB (`mem_limit: 2g`).

- `POST /say {text, lang?, voice?}` with the header `X-Voice-Key: <VOICE_TTS_KEY>` -> `{"url": "/a/<name>.wav", voice, seconds, ms}`.
  The WAV is the house format, PCM 16 kHz 16-bit stereo (the mono voice is repeated on both channels); the name is 128 random bits and the file lives 10 minutes. Without a key set the service refuses to speak.
- `GET /a/<name>.wav` the spoken file (no key: the name is the secret), `GET /s/<sound>.wav` the confirmation sounds `listening`, `done`
  and `error`, `GET /health`.

It runs only with `COMPOSE_PROFILES` containing `voice`, on the compose network only (no port of its own): the satellites fetch its
spoken files from the dashboard's port, `http://<BIND_ADDRESS>:<DASHBOARD_PORT>/plugins/voice/tts/a|s/<name>.wav`, which hands them on.
`make install` generates `VOICE_TTS_KEY`.

On a computer of the house instead (Settings -> Devices -> Computers and servers -> Speech (TTS): Install, Use): the same service
in `/opt/yashome-tts` there, its port on that computer's loopback only and no key (`VOICE_TTS_OPEN=1`); the house reaches it
through the helper's ssh tunnel (`helper:8092`) and hands its files to the satellites itself: `GET /plugins/voice/tts/a|s/<name>.wav`
(open, like the service's own: a file's random name is its secret), the address they are told (settings `voice.tts`).

## The plug-in: satellites, cards, say/play/stop/volume

A satellite is a thin device with the interface of docs/voice.md under its own node prefix: a microphone (it sends `<node>/wake`, and
audio to the voice service after it), a player (`<node>/say/url`, `<node>/stop/command`, `<node>/volume/set`, `<node>/player/state`), or both.
`PUT /api/voice/satellites/<id>` (the dashboard token) makes one: `{node, mic, player, name?, room?, peer?, voice?, volume_state?}`;
`peer` is the address the satellite's audio comes from (the reply goes to the satellite of that address), `voice` a TTS voice (`<language>-<speaker>`) for its
answers, `volume_state` the topic of its volume when it is not `<node>/volume/state` (ESPHome: `<node>/number/<name>/state`). `GET` lists
them with their counters, `DELETE` removes one with its cards. A node name may not be a topic of the house, a plug-in, a device, or another
satellite.

Each role is a card (group "voice"): `<id>_mic` has Listening (on / muted: `<node>/listen/command`) and the time of the last wake;
`<id>_spk` has what it does now, a Volume slider, Stop and Test sound, and the channels `say` (a text) and `play` (an http URL) that are
not on the card but work from the API, from a rule and from MQTT: `home/cmd/<id>_spk/say` with a text, `.../play` with a URL,
`.../stop`, `.../volume`.

- `POST /api/voice/say {text, target, lang?, voice?}`: the TTS speaks, every player of `target` (a satellite id, a room or `all`)
  plays it. `POST /api/voice/play {url, target}`, `POST /api/voice/stop {target}`, `POST /api/voice/volume {target, value 0..100}`.
- Settings -> Voice (`GET/PUT /api/voice/config`, the house token; voice-vad reads it every 10 s, no restart): the wake word, how exact
  (`wake_maxdist`, letters: at most (length - 1) // 2 and 3, so 7 letters -> 3, 4 -> 1), the start of the word (`wake_prefix`, 3 letters or
  more, "" = off), how it is also heard (`wake_variants`: a word of 2+ letters, or a stem of 3+ letters and *, "comput*" = any ending), smart answers on/off (`llm`: off = no model is loaded or
  polled), `llm_model`, `big_model` (`POST /api/voice/models` lists what the Ollama hosts have), `llm_timeout`, ask before
  "turn everything off" (`confirm_all_off`), `ask_ttl`. A satellite with `need_wake: true` acts only on a phrase that holds the wake word
  (`need_wake` in the GET = their `peer`s); a save without the field keeps it. voice-vad's first read seeds every field from what it runs
  (`PUT ?seed=1`, once); a page save before that is refused (409) and the page waits. A satellite added later at an address the env
  listed in VA_NEEDWAKE_IPS needs the wake word too. `lang` in the GET is the house language (Settings -> General): the answers' language.
  A GET of it that went fine is left out of the access log.
- A satellite with `quiet_fail: true` ("Silent when not understood" in its settings) gets no reply and no sound for a command that was not understood (a question is still asked): a false wake on the TV stays silent. Saving the settings form keeps the flags it does not show.
- On a wake the HOUSE stops what the satellite plays (the node decides nothing); it plays the `listening` sound only for a satellite with `wake_chime: true`
  (off by default: the command follows the wake word at once, and the node sends the last second of audio before the wake with the stream).
- The voice service tells what it did on `voice/heard` (`{"ip": <peer>, "ok": true|false, "reply": "text"?}`): the satellite of that peer
  address hears the `done` sound, the `error` sound, or the reply spoken; `quiet: true` (an understood command that was already confirmed by a beep) adds no sound.
- `GET /api/voice/status`: the service's health, the public address and whether the key is set.

The dashboard has a tab "Voice / Sound" with the cards of the satellites; the window of a card has a "Voice settings" section (name, room, the address
the audio comes from, the voice of the answers, the counters). The wake word is on the device and not set here.

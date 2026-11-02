# Voice in the house - today and the target

Status: design, no code yet. Today the house HEARS (wake word, speech to text, commands); the target is that it also ANSWERS by voice, on
the satellite that heard you, and that every microphone and every speaker is a card of the house.

## Principle: everything on the server

A satellite is thin: it sends the microphone stream (after its own wake word), plays the audio it is given, and has a mute and a volume.
Command logic, TTS, the sounds, routing, per-device settings, timers, retries and confirmations all live in the house. Firmware gets no
business logic: no baked sounds, no local command list. The wake word stays ON THE DEVICE (micro_wake_word on the ESP satellite, openWakeWord
on the Android panels): audio goes to the house only after the wake, never as a continuous stream.

The whole interface of a satellite (MQTT, under its own node prefix): `<node>/wake` (event), `<node>/say/url` (stop what plays, play this
WAV/FLAC/MP3 URL), `<node>/stop/command`, `<node>/volume/set` 0..100 (state kept), `<node>/mute/command` ON|OFF and `<node>/listen/command`
ON|OFF (states retained), `<node>/player/state` playing|idle (retained), `<node>/status` and `<node>/status/i2s` (the boot duplex check).
ONE audio format everywhere: everything the house sends to a satellite (TTS, the confirmation sounds, any played file) is converted
ON THE SERVER to WAV PCM 16 kHz, 16-bit, stereo, the native format of the satellite's port; a device resampler is only a safety net.
A satellite with the flag `lead_in` (the ReSpeaker; tablets and phones have no such gate) gets every WAV (reply or chime, `/say {lead_in}`,
`/s/<name>_lead.wav`) behind 0.4 s of faint noise (+-100 counts, fixed seed, no fade): the XVF/codec output path mutes on digital zeros and
opens 60-250 ms after the first non-zero sample, which cost the first syllable or beep. Ear test, 3 beeps: 2 heard with no lead-in or with
0.6 s of zeros, 3 with the noise. The node firmware stays unchanged.
On a wake the HOUSE stops the playback (`stop/command`) or turns the volume down: the node does not decide that.

## Today

```
 satellite (hears)                           the house host                                       acts through
 -----------------                           --------------                                       ------------
 ESP satellite (e.g. ReSpeaker XVF3800       voice-vad  (container, TCP :8091, python)  --HTTP--> whisper (container, :8090,
   + XIAO ESP32S3): wake word ON the device    webrtcvad cuts the utterance                        whisper.cpp, a ggml base model)
   MQTT <node>/wake + status                   peak-normalises, asks whisper
 Android panels (the launcher's Voice):        text -> intent (voice_commands.json,     MQTT  zigbee2mqtt/<switch>/set/state
   wake word on the device, PCM to :8091       hot-reloaded by mtime)                   MQTT  home/cmd/<device>/<code>
   (caps: mic, speaker, voice_port)            AC and TV intents: HTTP to the house API HTTP  /api/devices/<ac|tv>/... 
```

- **Where it lives.** `voice-vad` and `whisper` run from a `voice/` directory of the house data (a `compose.yml`, project `voice`, on the
  Yashome network). The files are copies of the pre-Yashome stack; they are outside the plug-ins and outside git, so `make update`
  neither builds nor versions them.
- **Wake word.** On the satellite, on the device. The server does not check it again for the peers listed in `VA_NOWAKE_IPS` (`*` = all);
  for any other peer the transcript must start with the wake word (whisper keyword spotting).
- **The command map** is data (`voice_commands.json`: on/off words, zones -> zigbee2mqtt or `home/cmd`, appliances such as air
  conditioners with modes and temperature). The pre-Yashome dashboard had a tab to edit it; Yashome has no voice plug-in yet.
- **The answer.** There is none: the service acts and logs. No audio goes back to any satellite.
- **The ESP satellite** is not in the Yashome build path: its YAML (mic only: I2S master, `micro_wake_word`, a custom `pcm_tcp_stream`
  component that streams about 5 s after the wake word with a 500 ms pre-roll) lives in the old stack only.

## Target

1. **TTS on the house, local.** Silero in a container (`tts`, HTTP `POST /say {text, voice} -> WAV`), Russian (v5_ru, default speaker
   xenia) and English (v3_en, default speaker en_99). CPU only, two threads: a 2 s reply takes 0.04-0.09 s (real-time factor ~0.03), a model
   loads in about 1 s and the service warms both at start, so the first answer is as fast as the rest; with both models loaded the container
   holds about 1.1 GB. The models are NOT in the image or the repository: the service downloads `v5_ru.pt` (139 MB) and `v3_en.pt` (54 MB)
   once into the volume `voice-models`. **Licence: the Silero models are CC BY-NC-SA 4.0, so this feature (and the product with it) is for
   non-commercial use only** (NOTICE.md). Silero does not read digits: the service writes numbers out in words first (`numwords.py`).
   The voice is a setting of the player or the room, so it is chosen in the device window.
2. **Replies go to the satellite that heard the wake word.** `voice-vad` knows the TCP peer: the house keeps a table peer address ->
   satellite device. After acting it says a short result (done, the new temperature, "I did not understand") on that satellite.
   Confirmation sounds (listening, done, error) come from the server too: short WAV files the house serves and plays through the same
   `say/url`, so the firmware carries none.
3. **One API for every player.** `POST /api/voice/say {text, target, lang?, voice?}`, `POST /api/voice/play {url, target}`,
   `POST /api/voice/stop {target}`, `POST /api/voice/volume {target, value}`, and the same as MQTT commands
   `home/cmd/<player>/say|play|stop|volume`, so rules and panels can use them. `target` is a player or a room (then every player of it).
4. **Players** are driven by a small piece on each: an ESP satellite (ESPHome `speaker`/`media_player` plays the URL the house gives),
   an Android panel (the launcher plays a URL: a new bridge call), a Linux box (a user service like the touchmouse typing helper: MQTT
   `<node>/play` -> `pw-play`/`mpv` on its own stream, never the media player in use), a TV (a toast at most, no audio).
5. **A Yashome plug-in `voice`** owns all of it: the virtual devices and cards below, the API, the command-map editor (the old tab,
   rebuilt); the compose of `voice-vad`, `whisper` and `tts` moves into the plug-in (`plugins/tools/voice/service/`), so `make update`
   builds and versions them. The satellite goes into the build path (its own package, with the description package of the house base).

## Devices and cards

Microphones: the ESP satellite and every Android panel with a mic. Players: the ESP satellite (after its firmware gets audio out),
Android panels (speaker), Linux boxes with PipeWire, and a TV only for a toast. IR remotes and TVs that cannot play are not players.
All of them sit in one device group, "Voice / Sound".

Card rule: the card has the daily control and the state, everything else is in the device window.

```
 MICROPHONE card                            PLAYER card
 +-------------------------------+          +-------------------------------+
 | [mic] Bedroom satellite       |          | [spk] Living-room box         |
 |  online                       |          |  playing: <what>  /  idle     |
 |  [ Listening  (o) ]           |          |  volume  ----o-------  40%    |
 |  last wake: 11:20             |          |  [ Stop ]   [ Test sound ]    |
 |  level  ||||||....            |          +-------------------------------+
 +-------------------------------+
 window: wake word, sensitivity, TTS voice, the room it answers for, counters (wakes, commands, errors)
```

- **Mic card:** online, a listening/muted toggle, time of the last wake, a level meter only if the device reports it cheaply (else
  omitted). Muting stops the stream on the device (MQTT `<node>/listen/command`), not only on the server.
- **Player card:** online, playing/idle and what, a volume slider, Stop, "Test sound".
- **A device that is both** (an Android panel, the ESP satellite) shows one card per role, a mic card and a player card, over one window.

### A tablet as a satellite

A tablet (the Android launcher) is a satellite like an ESP node, with its own `panel` (the tablet's device id). The house commands it with
the same four commands (play a URL, stop, volume, listen on/off) as one JSON line on `panel/<id>/voice/cmd`; the panels plug-in queues
them and the tablet takes them by a long-poll (`GET /api/panels/voice/next`, the tablet's own key; it works through the VPN too). What the
tablet reports (wake, playing/idle, listen, volume) goes to `POST /api/panels/voice/event` and out again on the topics of an ESP node
(`panel-<id>/wake`, `.../player/state`, ...), so the voice plug-in sees one kind of satellite. The tablet plays on the alarm stream (a
do-not-disturb "alarms only" mutes the media stream) and does not listen while it plays, so its own chimes and replies never reach the
recognizer.

**One room, one answer.** When two satellites of a room wake within 1.5 s (a tablet and a speaker in the same room), the house acts on
the first and the second gets no chime; its stream is dropped by the recognizer.

## Open decisions

1. Decided: Silero is the only TTS (CC BY-NC-SA 4.0, non-commercial use); Russian v5_ru speaker xenia, English v3_en speaker en_99.
2. Decided: confirmation sounds come from the server (see the principle); nothing is baked into the firmware.
3. Decided: the README line becomes "no cloud voice assistants" with this feature.

## Recognition

The command recognizer is Vosk (a small Russian model in a docker volume). Two passes on every utterance: a grammar built from the command
map (zones, verbs, appliance words, numbers; rebuilt when the map changes), then free recognition. The grammar can invent a command out of
any speech, so its text counts only when the free pass hears the same words; the log and `voice/heard` say which pass decided.
A house that wants another recognizer can swap it behind the same interface; whisper.cpp was measured (about 2 s per phrase on this CPU against 0.3-0.5 s for Vosk) and dropped.

What the recognizer tells the house is one MQTT line `voice/heard {ip, ok, text, engine, reply?, sound?}`; the plug-in answers on the
satellite whose `peer` is that address: `sound` (for example `thinking`, played while a slow answer is prepared) is only played; else
`reply` is spoken (also when `ok` is false, for "I did not understand"); else the done or error chime.

### LLM fallback and "Ask the house"

A phrase that no rule understood goes to a small local model (Ollama on the host, 4 threads, kept loaded, warmed at start). The prompt is
built from the live device registry (the house's own device names, rooms, channel names, the command-map words and the glossary aliases),
so it changes only when the house does. The model answers ONE line of a strict DSL (`<name> <code>=<value>; ...`, `all_off except=<name>`,
`none`); the house parses it against the registry: an unknown name, code or value drops the whole line ("I did not understand"). The model
never sees content (news, mail) and has no action rights: it proposes, the house checks and acts.

- **Questions.** The house asks first, and acts only on "yes", when the model GUESSED a device the phrase does not name, for every risky
  device (locks, gates, doors, alarms; never run from memory) and for every "all off". The question names devices with the glossary's
  human names (a case form each), else the house's own name, never an id. The answer is the next utterance of that satellite within 12 s
  (a satellite with the listen-once command opens its mic by itself; the others need the wake word first); anything but yes is no.
- **A question must be heard.** The reply that carries a question is marked; the house expects the satellite to start playing it within
  6 s, otherwise the question is cancelled with a log line (nothing waits silently for an answer nobody heard). When the question has been
  played and ends, a satellite that has the listen-once command (`listen_once` in its record) opens its mic once for the answer (6 s).
- **One sound at a time.** The house never cuts its own reply: per satellite a "listening" or "thinking" sound is dropped while anything plays
  or waits; a reply or a result chime queues and goes out when the node reports idle (a node that never reports is taken as idle after 10 s).
  Only a real stop, a new wake or an explicit play from the API cuts and replaces.
- **Chimes.** A satellite record has `chime` (default on): the "thinking" sound (only when the model takes longer than 1.5 s), the confirm beep ("done") the moment a command is understood and the "error" sound; `wake_chime` (default off) adds the "listening" sound after a wake; `quiet_fail` (default off; "Silent when not understood" in the satellite's settings) plays nothing at all for a command that was not understood (a satellite whose wake fires on the TV), a question still asked. An understood command is silent after its beep unless something went wrong (the reply names the device that did not answer); a phrase that was not understood is answered "Did not understand" aloud with no beep. A node that cannot
  play while it streams its mic is set to off; the stop on a wake goes out only when something is playing.
- **Learning.** A confirmed guess is remembered for that exact phrase (house data, validated against the registry again each time).
- **Settings** (an enum, a number) are driven by voice only on climate devices.
- **The text box "Ask the house"** (the header, right of the logo: Ask and a field, Enter or Ask sends; on a phone Ask opens the field; the house token) takes the road of a spoken phrase after
  recognition: phrase map, then the model, then the same checks, with Yes / No buttons for the questions; log `engine=text`. It can say
  the answer on a chosen speaker. When the model says the text is no command, the box switches to a **chat**: the same model answers
  conversationally, streamed, with a prompt that has no devices and no actions; a small "i" by the answer says nothing in the house was
  done, and nothing of its text is ever parsed as a command. The big model thinks first (up to 60 s, "Thinking..." meanwhile; only its
  answer is shown), the small one answers when it is away. A model that is down or late says so ("The model is not available now",
  "The model did not answer in time"). The time, the date, the weekday and how a room feels ("is it cold in the bedroom") are answered by
  the service itself from the house, never by the model.

### State questions

A question about the state of the house is answered from LIVE state, in words, spoken and in text; it never acts and needs no question
back. A question is recognized by its words ("is the light on", "who is home", "what is the temperature in the bedroom", "what is open",
"batteries") BEFORE any command rule runs, because a question shares its stem with a command ("is it switched on" / "switch on"): a
question can never switch anything. The fast templates answer the common ones in milliseconds; the rest go to the model, which has a
query form in its one-line language (`ask <device> [code]`, `ask room=<room>`, `ask who_home`, `ask all lights|open|battery|online|on`)
and so only chooses WHAT to ask: the words of the answer are a template over the registry (switches and lights on/off and brightness,
climate mode/temperature/room temperature, sensors, locks and doors, players, presence, batteries, devices that are offline or silent
for hours: "no data N ago"). Device names are the glossary's spoken names, else the house's own.

Statements ("I am home") get a plain "Understood". The chat never answers about the house, the people or the present time: a question
or statement with such words is answered by a fixed line before the model is asked, and a chat answer that mentions them is replaced by
the same line (the chat answer is held until it is checked, so a streamed invention cannot be taken back).

### Push-to-talk in the Android app

The Yash app has an "Ask the house" screen: the chat of the dashboard's box (typed phrases, Yes / No buttons, the chat mode) and a
**hold-to-talk button**. The phone is a satellite of its own (`phone: true`, for example `phone_guest`) with one card, the time it was
last talked to. Holding the button records 16 kHz mono; on release the recording goes up in one request (`POST /api/voice/ptt?sat=<id>`,
the raw samples as the body, the phone's key; it works through the VPN, nothing new is opened on the house) and the recognizer takes the
road of a spoken phrase without a wake word: the grammar pass, the free pass, the model, the same checks. The answer is JSON (what was
heard, what the house did or asks, a token for the Yes / No buttons) and an `audio` address the phone plays: the spoken question or
reply, else the done or error chime. A chat answer is streamed as text and the phone asks `POST /api/voice/tts` for its speech.
Who may call these paths: the house token and a full-access phone; a phone with per-device rights and a panel key are refused.

## Backlog


**A custom wake word for every satellite** (one phrase chosen by the household instead of the stock `hey_jarvis`). Not started.

1. **Record samples in the house.** A "record samples" mode (a button on the mic card): the satellite streams, the house cuts the
   utterances and stores them. Needed: the main speaker 50-150 times at different distances and levels, each family member 20-30 times,
   plus minutes of negatives (TV, talk, kitchen noise).
2. **Synthetic positives.** The phrase through the TTS with several voices and speeds, mixed with noise and room impulse responses.
3. **Train.** microWakeWord for the ESP satellite, openWakeWord for the Android panels.
4. **Live test.** Count false accepts and false rejects at home, one or two tuning rounds.
5. **Switch all satellites together**, so a phrase never works on some rooms only.

**Grammar-constrained model output** (next). The model may emit only valid lines: the device names of the live registry, their codes and
allowed values, plus `none` and the `ask` forms. Built from the registry on each change (an llama.cpp grammar through the model server's
format support, or a JSON schema with enums), so an unknown name or value cannot be produced at all instead of being dropped afterwards.
Measure accuracy and time on the bench phrases against the free-text output before switching.

**A training station** (later). A Windows PC with a 16 GB GPU of a family member, with their consent and in their free time (SSH access
is set up by the owner): training of the custom wake word (microWakeWord and openWakeWord) and a LoRA fine-tune of the house model
(about a month of real confirmed phrases plus synthetic variants are collected first). The runtime stays on the house server; the station
only produces model files.

**Personal assistant features** (backlog, not started). Personal and home content ONLY: no work mail, calendar or chat of any employer, ever.
Order:
1. **Calendar by voice** ("what do I have today"): the events of a personal calendar through a text template and the TTS; no LLM.
2. **Morning news digest**: prepared at night by the small local LLM from sites the household chooses, read on request.
3. **Personal mail and messenger** read aloud, summaries prepared in the background. Which accounts and chats is decided later, one by one.

Hard rules for all three:
- **The reading and summarising pipeline has NO action rights in the house.** It only produces text for the TTS. House actions come only from
  the user's own voice, through the same validation as every command (a registry check of the device and the value), so a text inside a mail,
  a message or a web page can never switch anything (prompt injection).
- **While the house reads content aloud (news, mail, messages) it accepts no voice command from that satellite or room**: the content
  audio must never become a command (the stream is not acted on, or its results are dropped, while the content plays).
- **The only input during reading is stop**, two ways: the Stop button (the speaker card, the panel) and the voice phrase "wake word + stop"
  (exactly that phrase; nothing else is acted on). After the stop, or when the reading ends, normal commands work again.

# Third-party notices

## Silero TTS models (non-commercial)

The voice feature speaks with the Silero text-to-speech models `v5_ru` (Russian) and `v3_en` (English) from
https://github.com/snakers4/silero-models. The models are licensed CC BY-NC-SA 4.0: attribution, non-commercial use only, share alike.

The models are not part of this repository or of the images: the `tts` service downloads them once into a docker volume at its
first start. Because the voice feature needs them, **Yashome is for non-commercial use only.** The code of this repository stays MIT
(see LICENSE); the restriction comes from the models.

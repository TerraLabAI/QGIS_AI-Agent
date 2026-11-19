# Notification sounds shipped with AI Agent

All four are CC0 1.0 (public domain dedication): free for commercial use, no
credit required, compatible with the plugin's GPL-2.0-or-later mirror.

| File | Source | Author | Licence |
| --- | --- | --- | --- |
| `chime.wav` | `sounds/notifications/notification-attention-clear.wav`, SFX Kit, https://github.com/wyzowl/sfx-kit at commit 92780e7ab0cdfb557497a988089c47f46e8c7412 | Wyzowl | CC0 1.0 |
| `marimba.wav` | `sounds/notifications/notification-success-soft.wav`, same kit and commit | Wyzowl | CC0 1.0 |
| `bell.wav` | `sounds/everyday-props/props-shop-bell.wav`, same kit and commit | Wyzowl | CC0 1.0 |
| `train.wav` | "Train Whistle, Foley #7", https://bigsoundbank.com/train-whistle-foley-7-s3321.html (file 3321) | Joseph Sardin, BigSoundBank | CC0 1.0 |

What was done to them, with ffmpeg, so they sit evenly next to each other: the
train whistle was cut to its first 2.05 s (two toots and the start of the tail)
with a 0.5 s fade out; each file was then brought to the same loudest momentary
loudness (-20 LUFS over 400 ms windows), passed through a limiter with its
ceiling at -3 dBFS, and saved as 32 kHz mono 16-bit PCM WAV, the format
`QSoundEffect` and `winsound` both play on every system.

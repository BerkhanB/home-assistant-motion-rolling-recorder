# Motion Rolling Recorder v0.2.0 — experimental public release

Initial public beta of a lightweight Home Assistant OS RTSP motion recorder.

## Included

- rolling RTSP pre-buffer,
- configurable pre-roll and post-roll,
- repeated motion pulses extend one incident,
- one final MP4 per incident,
- video stream copy (no video transcoding),
- G.711 A-law audio converted to AAC for MP4 compatibility,
- automatic temporary-buffer cleanup,
- `/motion` and `/health` endpoints,
- `amd64` and `aarch64` support.

## Known limitations

- Some Tapo RTSP streams emit non-monotonic DTS. FFmpeg repairs these values,
  but long-duration A/V sync is not yet fully validated.
- Incident extension is pulse-based in this release. Cameras that remain
  continuously `on` without emitting repeated pulses can end an incident early.
- RTSP path handling is currently Tapo-oriented (`stream1` / `stream2`).
- Port 8099 is unauthenticated and must remain LAN-only.

Mark this GitHub release as a **pre-release**.

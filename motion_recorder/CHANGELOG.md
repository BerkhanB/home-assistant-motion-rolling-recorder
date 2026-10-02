# Changelog

## 0.2.1

- Add FFmpeg-generated timestamps and ignore problematic input DTS where appropriate.
- Use wall-clock timestamps for the RTSP input to reduce non-monotonic timestamp issues.
- Add asynchronous audio resampling to keep AAC audio synchronized when camera timestamps are discontinuous.
- Continue copying the video stream without re-encoding.
- Keep FFmpeg warning-level logging while the timestamp changes are being validated.

## 0.2.0

- Keep RTSP video as stream copy to avoid video re-encoding.
- Convert G.711 A-law audio to mono AAC (16 kHz / 32 kbps) in the rolling buffer.
- Copy AAC audio into the final MP4.
- Keep approximately one minute of idle rolling buffer.
- Persist incident timestamps across app restarts.
- Provide `/motion` and `/health` HTTP endpoints.
- Mark public release as experimental due to observed non-monotonic RTSP DTS on some Tapo streams.
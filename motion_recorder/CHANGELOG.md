# Changelog

## 0.2.0

- Keep RTSP video as stream copy to avoid video re-encoding.
- Convert G.711 A-law audio to mono AAC (16 kHz / 32 kbps) in the rolling buffer.
- Copy AAC audio into the final MP4.
- Keep approximately one minute of idle rolling buffer.
- Persist incident timestamps across app restarts.
- Provide `/motion` and `/health` HTTP endpoints.
- Mark public release as experimental due to observed non-monotonic RTSP DTS on
  some Tapo streams.

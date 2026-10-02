# Motion Rolling Recorder

A lightweight Home Assistant OS app for RTSP cameras that keeps a short rolling
buffer and creates a single MP4 for each motion incident.

Features:

- configurable pre-roll before the first motion pulse,
- dynamic post-roll: each new pulse postpones the stop deadline,
- one final MP4 per incident instead of fixed-length clip chains,
- video stream copy (no video re-encoding),
- G.711 A-law camera audio converted to AAC for MP4 compatibility,
- automatic cleanup of temporary rolling-buffer segments,
- final recordings stored under `/media/recordings`.

This release is **experimental**. See the Documentation tab before relying on it
for security-critical recording.

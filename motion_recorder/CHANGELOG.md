# Changelog

## 0.3.2

- Switch RTSP socket timeout to `-timeout 10000000` (replacing deprecated `-stimeout` removed in newer FFmpeg builds).

## 0.3.1

- Add FFmpeg RTSP socket timeout (`-timeout 10000000`, 10 s) to automatically abort hung socket reads on silent connection drops or camera reboot.
- Add Python-level stream watchdog (`latest_segment_time`) that monitors segment generation and automatically terminates/restarts FFmpeg if no segment updates appear within `STALL_THRESHOLD` (15+ seconds).
- Add progressive backoff for RTSP reconnections (5 s up to 30 s) when the camera is offline to reduce CPU and log spam.
- Enhance `/health` endpoint with real-time stream status (`ok`, `stream_healthy`, `stream_status`, and `last_segment_age_seconds`).

## 0.3.0

- Move temporary rolling MPEG-TS buffer to memory-backed tmpfs (`/tmp/camera_motion_buffer`, `tmpfs: true`) to eliminate continuous flash drive write churn.
- Automatically clean up any legacy disk-based `.motion_buffer` directory from prior versions.
- Increase default segment target duration from 2 s to 4 s to reduce segment creation/deletion frequency.
- Harden segment detection (`segment_files`): exclude the newest entry positionally (likely still being written by FFmpeg) and skip zero-size files, replacing the previous blind `files[:-1]` slice.
- Add synchronous incident finalization failure detection with automatic retry scheduling (up to 3 attempts with delay) and protected retention of retry segments during buffer cleanup.
- Update restart recovery: cleanly abandon pre-restart incidents on startup since the RAM buffer is wiped across container restarts.
- Store concat manifest on tmpfs (`/tmp/concat.txt`) and remove after remuxing to avoid unnecessary flash writes in `/data`.
- Maintain proven AAC + asynchronous audio resampling pipeline (`aresample=async=1:first_pts=0`) and atomic incident detachment architecture.

## 0.2.3

- Preserve the RTSP demuxer time base during video stream copy with FFmpeg `-copytb 1`.
- Further reduce startup and stream-copy DTS monotonicity issues without re-encoding video.
- Keep wall-clock input timestamps and asynchronous audio resampling unchanged from 0.2.2.

## 0.2.2

- Stop discarding RTSP DTS values during stream copy.
- Retain generated PTS and wall-clock input timestamps.
- Keep asynchronous audio resampling for discontinuous camera timestamps.
- Fix unset video timestamps introduced by the 0.2.1 timestamp experiment.

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
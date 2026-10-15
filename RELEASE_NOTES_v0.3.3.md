# Motion Rolling Recorder v0.3.3

Version 0.3.3 hardens stream recovery, eliminates stale-segment restart loops, and introduces multi-tiered self-healing with Home Assistant Supervisor and Core.

## Root Cause & Fix

In prior versions, when an RTSP stream stalled and the watchdog restarted FFmpeg, the watchdog evaluated the modification timestamps of existing `.ts` files left in the buffer directory from the previous run. Because those files had older timestamps, the newly launched FFmpeg process was immediately flagged as stalled on its very first watchdog tick and killed after ~1 second. This resulted in a repeating 30-second restart loop that could persist indefinitely until the entire add-on container was restarted.

Version 0.3.3 resolves this by treating every FFmpeg launch as an isolated generation:
- Existing buffer segments are snapshotted at launch and never misattributed to the new generation.
- Segment tracking resets cleanly on every launch (`last_segment_at = None`, `stream_status = "starting"`).
- Every newly launched FFmpeg process receives a dedicated 15–20s startup grace window before stall checks engage.

## What's Changed

- **Generation-isolated segment tracking**: Each FFmpeg launch tracks its own new segment writes. Old segments in the RAM buffer can no longer trigger immediate watchdog restarts.
- **Monotonic clock timing**: All watchdog durations, stall timeouts, and startup windows now use `time.monotonic()` instead of wall-clock time, protecting against clock drift and NTP adjustments.
- **Dedicated startup grace window**: Guarantees up to 20 seconds (`STARTUP_TIMEOUT`) for new FFmpeg processes to connect and produce their first segment before stall detection begins.
- **Container restart escalation (3-strike fail-safe)**: If FFmpeg fails 3 consecutive times without regaining healthy streaming, the recorder terminates with `os._exit(1)`. Home Assistant Supervisor automatically restarts the container, resetting all tmpfs and container states. The failure counter resets once a stream runs healthy for 30 seconds.
- **HTTP 503 health responses**: `/health` now returns HTTP 503 Service Unavailable when the stream is stalled or stopped beyond grace periods, while returning HTTP 200 during normal streaming, initial startup, or brief retry backoff.
- **Supervisor watchdog configuration**: Added `watchdog: "http://[HOST]:[PORT:8099]/health"` in `config.yaml` to leverage native Home Assistant Supervisor health monitoring.
- **Home Assistant defense in depth**: Added example REST binary sensor (`motion_recorder_stream_healthy`) and auto-recovery automation (`hassio.app_restart`) in `home_assistant.yaml.example` and `DOCS.md` as an external safety net.

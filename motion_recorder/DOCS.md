# Motion Rolling Recorder documentation

## What it does

The app keeps the RTSP stream open continuously and stores short temporary MPEG-TS
segments in RAM under:

```text
/tmp/camera_motion_buffer/
```

(Memory-backed via `tmpfs: true` in the app configuration, eliminating flash storage wear.)

When Home Assistant sends a motion pulse to `POST /motion`, the app opens (or
extends) an incident. When `post_roll` seconds have passed without another pulse,
it remuxes the required temporary segments into one MP4 under:

```text
/media/recordings/
```

With the defaults, a pulse at T0 starts the saved interval roughly 5 seconds
before T0 and ends roughly 30 seconds after the final pulse. Segment/keyframe
boundaries can make the final file slightly longer.

## Camera requirements

This version is tested around the Tapo-style RTSP layout:

```text
rtsp://USERNAME:PASSWORD@CAMERA_IP:554/stream1
```

`stream1` is normally the high-quality stream and `stream2` the lower-quality
stream on supported Tapo cameras. Other RTSP cameras may work only if they use
this same path convention.

Create a camera-local account in the camera vendor's app and give the camera a
stable LAN IP/DHCP reservation.

## App configuration

- `camera_ip`: LAN IP of the RTSP camera.
- `camera_username`: camera-local RTSP username.
- `camera_password`: camera-local RTSP password.
- `stream`: `stream1` or `stream2`.
- `pre_roll`: seconds retained before the first motion pulse. Default: 5.
- `post_roll`: seconds to wait after the latest pulse. Default: 30.
- `segment_seconds`: target duration of temporary segments. Default: 4.
The host port defaults to 8099. If that port is already in use, change the host
mapping in the app's Network settings and use the same host port in Home
Assistant's `rest_command` URL.

## Home Assistant configuration

Add this to `configuration.yaml`, replacing `HOME_ASSISTANT_IP` with the LAN IP
of the Home Assistant host (do not use `127.0.0.1`, because Home Assistant Core
and apps run in separate container network namespaces):

```yaml
rest_command:
  motion_rolling_recorder_pulse:
    url: "http://HOME_ASSISTANT_IP:8099/motion"
    method: POST
    headers:
      Content-Type: application/json
    payload: "{}"
    timeout: 2

# Optional health sensor (defense in depth):
binary_sensor:
  - platform: rest
    name: Motion Recorder Stream Healthy
    unique_id: motion_recorder_stream_healthy
    resource: "http://HOME_ASSISTANT_IP:8099/health"
    scan_interval: 30
    timeout: 3
    value_template: >
      {{ value_json.ok | default(false) and value_json.stream_healthy | default(false) }}
```

Then create an automation using your actual motion binary sensor:

```yaml
- id: motion_rolling_recorder_on_motion
  alias: "Camera: Motion Rolling Recorder"
  mode: parallel
  max: 20
  triggers:
    - trigger: state
      entity_id: binary_sensor.YOUR_CAMERA_MOTION_SENSOR
      to: "on"
  actions:
    - action: rest_command.motion_rolling_recorder_pulse

# Optional auto-recovery automation:
# Restarts the add-on/app if stream remains unhealthy for 60 seconds.
- id: motion_recorder_auto_recover
  alias: "Motion Recorder: Auto Recover"
  description: Restart Motion Rolling Recorder if its stream remains unhealthy.
  mode: single
  triggers:
    - trigger: state
      entity_id: binary_sensor.motion_recorder_stream_healthy
      to: "off"
      for: "00:01:00"
    - trigger: state
      entity_id: binary_sensor.motion_recorder_stream_healthy
      to: "unavailable"
      for: "00:01:00"
  actions:
    - action: hassio.app_restart
      data:
        app: b85c98ee_motion_recorder
    - delay: "00:01:30"
```

Restart Home Assistant (or reload REST commands where available) after adding
`rest_command`.

## Test procedure

1. Start the app and wait at least 10 seconds.
2. Open the app logs. You should see:

   ```text
   [ffmpeg] starting RTSP rolling segmenter
   [http] listening on port 8099
   ```

3. Check the app logs or `/tmp/camera_motion_buffer/` inside the container.
   New `.ts` segments should appear and old idle segments should be removed automatically.
4. Test the API from another machine:

   ```powershell
   Invoke-RestMethod -Uri "http://HOME_ASSISTANT_IP:8099/health"
   ```

   The endpoint returns **HTTP 200** when healthy, starting up, or briefly reconnecting:
   ```json
   {
     "ok": true,
     "stream_healthy": true,
     "stream_status": "healthy",
     "last_segment_age_seconds": 1.5,
     "incident_active": false,
     "first_motion": null,
     "last_motion": null
   }
   ```
   If the stream stalls or stops beyond the startup/reconnect grace periods, `/health` returns **HTTP 503 Service Unavailable** (with `"ok": false`, `"stream_healthy": false`, `"stream_status": "stalled"`).

5. Trigger a pulse:

   ```powershell
   Invoke-RestMethod `
     -Method Post `
     -Uri "http://HOME_ASSISTANT_IP:8099/motion" `
     -ContentType "application/json" `
     -Body "{}"
   ```

6. `/health` should show `incident_active: true`.
7. Wait more than `post_roll` seconds without another pulse.
8. A new `motion_*.mp4` should appear in `/media/recordings/`.

## Audio

Some Tapo cameras expose G.711 A-law (`pcm_alaw`) audio. MPEG-TS can treat that
as a private stream when copied directly, causing audio to disappear during the
final concat/remux. Version 0.2.0 therefore keeps the video bit-for-bit but
encodes the low-rate mono audio to AAC (16 kHz, 32 kbps) while buffering.

## Known issue: non-monotonic timestamps

Some RTSP cameras, including the tested Tapo stream, can emit DTS values that
move backward. FFmpeg then logs warnings such as:

```text
Non-monotonic DTS ... changing to ...
```

FFmpeg repairs these timestamps and recordings can still be playable, but this
behavior has not yet been validated for very long incidents or every camera.
Possible effects include small timing irregularities or A/V sync drift. This is
the main reason this release is marked experimental.

Please include the relevant FFmpeg log excerpt, camera model, stream choice and
approximate incident duration when reporting timestamp problems.

## Motion semantics

The app extends an incident when it receives another `/motion` pulse. The
recommended Home Assistant automation sends one pulse for every `off -> on`
transition of the camera's motion binary sensor.

If a particular camera holds its motion binary sensor continuously `on` for
longer than `post_roll` without generating new pulses, this beta can close the
incident before that sensor returns to `off`. Cameras that emit repeated motion
pulses do not normally hit this limitation. State-aware `on/off` tracking is
planned for a future release.

## Storage and RAM buffer

- **Temporary rolling buffer**: Temporary MPEG-TS segments are written to
  `/tmp/camera_motion_buffer/`. Because `tmpfs: true` is configured, `/tmp` is
  memory-backed (RAM), eliminating flash storage write cycles and continuous
  filesystem churn.
- **Idle retention**: While idle, only roughly the most recent minute of temporary
  segments is kept.
- **Active incident retention**: During an active incident (or while retrying
  finalization), all segments needed from pre-roll onward are preserved in memory
  until the MP4 is created.
- **App restart behavior**: Because the rolling buffer resides in RAM (tmpfs),
  container restarts clear all in-memory segments. Any unfinished incident
  recorded prior to a restart is cleanly abandoned on startup rather than
  attempting to stitch missing segments.
- **Final MP4 retention**: Final MP4 files are written to `/media/recordings/` on
  persistent storage. Retention of final MP4s is intentionally left to the
  user/Home Assistant.

## Incident finalization & retry

Incident finalization remuxes buffered segments into the final MP4 synchronously:
- If finalization succeeds, the completed MP4 is moved to
  `/media/recordings/motion_<timestamp>.mp4`.
- If finalization encounters a transient failure (e.g., segment muxing delay),
  the incident is preserved and scheduled for retry (up to 3 attempts with a
  delay). Segments needed by the incident remain protected in the buffer until
  rendering succeeds or retries are exhausted.
- If fresh motion pulses arrive while an older incident is rendering, the new
  motion pulse starts a distinct incident cleanly without race conditions.

## Stream recovery & multi-tiered watchdog architecture

The recorder implements four escalating layers of self-healing to prevent transient network drops or camera reboots from causing extended outages:

1. **Level 1 (FFmpeg socket timeout)**: FFmpeg is configured with `-timeout 10000000` (10s) to abort hung RTSP socket reads if the camera stops responding without a clean TCP FIN.
2. **Level 2 (Generation-isolated segment watchdog)**: The internal Python watchdog treats each FFmpeg launch as an isolated generation. Pre-existing buffer files are snapshotted so stale segment timestamps from prior runs can never poison a new FFmpeg process. Durations use `time.monotonic()`, giving each new process a 20-second startup window before stall checks (15s without new segment writes) engage.
3. **Level 3 (Supervisor container restart escalation)**: If FFmpeg fails 3 consecutive times without recovering a healthy stream, the recorder process terminates with `os._exit(1)`. Home Assistant Supervisor automatically restarts the container, resetting all tmpfs and container states. In addition, `watchdog: "http://[HOST]:[PORT:8099]/health"` in `config.yaml` enables Supervisor's HTTP watchdog, which restarts the container if `/health` continues returning HTTP 503.
4. **Level 4 (Home Assistant Core auto-recovery automation)**: As an independent safety net, Home Assistant Core polls `/health` via a REST binary sensor. If the sensor remains `off` or `unavailable` for 60 seconds, an automation issues `action: hassio.app_restart` to recover the add-on.

## Security

Port 8099 exposes unauthenticated `/motion` and `/health` endpoints on the Home
Assistant host's LAN address. **Do not port-forward this port to the Internet.**
See the repository `SECURITY.md` for details.

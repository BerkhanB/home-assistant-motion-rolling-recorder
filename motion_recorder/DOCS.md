# Motion Rolling Recorder documentation

## What it does

The app keeps the RTSP stream open continuously and stores short temporary MPEG-TS
segments under:

```text
/media/recordings/.motion_buffer/
```

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
- `segment_seconds`: target duration of temporary segments. Default: 2.
The host port defaults to 8099. If that port is already in use, change the host
mapping in the app's Network settings and use the same host port in Home
Assistant's `rest_command` URL.

## Home Assistant configuration

Add this to `configuration.yaml`, replacing `HOME_ASSISTANT_IP` with the LAN IP
of the Home Assistant host:

```yaml
rest_command:
  motion_rolling_recorder_pulse:
    url: "http://HOME_ASSISTANT_IP:8099/motion"
    method: POST
    headers:
      Content-Type: application/json
    payload: "{}"
    timeout: 2
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

3. Check `/media/recordings/.motion_buffer/`. New `.ts` segments should appear
   and old idle segments should be removed automatically.
4. Test the API from another machine:

   ```powershell
   Invoke-RestMethod -Uri "http://HOME_ASSISTANT_IP:8099/health"
   ```

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

## Storage

While idle, only roughly the most recent minute of temporary segments is kept.
During an active incident, all segments needed to build that incident are
preserved until the MP4 is created.

Final MP4 retention is intentionally left to the user/Home Assistant. If you
already have a size-based cleanup job for `/media/recordings/*.mp4`, it can
continue to manage the final files.

## Security

Port 8099 exposes unauthenticated `/motion` and `/health` endpoints on the Home
Assistant host's LAN address. **Do not port-forward this port to the Internet.**
See the repository `SECURITY.md` for details.

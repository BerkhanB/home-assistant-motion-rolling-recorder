# Home Assistant Motion Rolling Recorder

A lightweight Home Assistant OS app for **motion-triggered RTSP recording with
pre-roll, dynamic post-roll extension, and one final MP4 per incident**.

Instead of asking Home Assistant's fixed-duration `camera.record` action to make
multiple clips, Motion Rolling Recorder continuously keeps a small local RTSP
buffer. Motion pulses update the incident's stop deadline; when the quiet period
expires, the app remuxes the relevant segments into one MP4.

> **Status: experimental.** The core recording path has been tested on a Tapo
> C200C-style RTSP stream, but non-monotonic camera timestamps remain a known
> issue and broad camera compatibility has not yet been validated.

## Highlights

- 5-second pre-roll by default.
- 30-second post-roll after the latest motion pulse by default.
- Repeated pulses extend the same incident.
- One MP4 per incident.
- No video transcoding (`-c:v copy`).
- G.711 A-law audio converted to AAC for MP4 compatibility.
- Automatic temporary-buffer cleanup.
- Final files written to `/media/recordings`.
- `amd64` and `aarch64` Home Assistant OS architectures.

## Install in Home Assistant

This repository is structured as a Home Assistant **App repository** (formerly
called an add-on repository).

1. Copy this repository's GitHub URL.
2. In Home Assistant, open **Settings -> Apps -> App store**.
3. Open the repository menu and add this GitHub repository URL as a custom
   repository.
4. Refresh/check for updates.
5. Install **Motion Rolling Recorder**.
6. Configure the camera IP, camera-local username/password and stream.
7. Start the app and enable start on boot.
8. Follow the app's **Documentation** tab to add the `rest_command` and motion
   automation.

The app exposes host port `8099` by default so Home Assistant can call the motion
endpoint independently of the repository-specific internal DNS name. Do not
forward this port from your router to the public Internet.

## How it works

```text
RTSP camera
    |
    v
continuous FFmpeg segment buffer (~2 s pieces)
    |
    +--- idle: retain ~60 s only
    |
Home Assistant motion pulse ---> POST /motion
    |
    v
first pulse: incident starts (includes pre-roll)
new pulse:   post-roll deadline moves later
    |
    v
quiet period expires
    |
    v
FFmpeg concat/remux
    |
    v
/media/recordings/motion_YYYYMMDDTHHMMSSZ.mp4
```

## Known limitations

- This beta currently extends from **motion pulses**, not explicit ON/OFF state.
  A camera that stays continuously `on` without repeated pulses for longer than
  the configured post-roll can end an incident early.
- Some Tapo RTSP streams emit non-monotonic DTS. FFmpeg repairs the timestamps,
  but very long incidents and A/V sync still need broader validation.
- The current RTSP URL layout is Tapo-oriented (`/stream1` or `/stream2`).
- The HTTP API is unauthenticated and intended for trusted LAN use only.

See [`motion_recorder/DOCS.md`](motion_recorder/DOCS.md) for complete setup and
troubleshooting information.

## Development status

The initial public release deliberately uses **local builds on the user's Home
Assistant host**. Home Assistant supports this distribution model for early
projects. If the app gains users, the next publishing step should be pre-built
multi-architecture images on GHCR via the official Home Assistant builder
GitHub Actions.

## License

MIT. See [`LICENSE`](LICENSE).

# Contributing

Bug reports and focused pull requests are welcome.

For camera compatibility reports, include:

- camera manufacturer and model,
- Home Assistant architecture (`amd64` or `aarch64`),
- `stream1` or `stream2`,
- whether the final MP4 has video and audio,
- approximate incident duration,
- a short FFmpeg log excerpt around the failure.

Do not include camera passwords, Home Assistant access tokens, public IP
addresses, or other secrets.

Before opening a pull request:

1. Run `python -m py_compile motion_recorder/recorder.py`.
2. Ensure `motion_recorder/config.yaml` and `repository.yaml` parse as YAML.
3. Build the app Dockerfile on at least `amd64` when possible.
4. Explain any changes to FFmpeg timestamp handling carefully, since stream-copy
   timing behavior is a known compatibility area.

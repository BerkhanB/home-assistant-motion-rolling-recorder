# Motion Rolling Recorder v0.3.0

Version 0.3.0 transitions the continuous rolling RTSP segment buffer to RAM via Home Assistant OS `tmpfs`, eliminating flash storage write cycles and continuous filesystem churn on host drives.

## What's Changed

- **RAM-backed rolling ring buffer**: The temporary MPEG-TS ring buffer now resides in container memory (`/tmp/camera_motion_buffer`) via `tmpfs: true` in `config.yaml`.
- **Reduced storage wear**: Eliminates continuous flash disk writes and unlinks (~21,600+ file creations/deletions per day at 4s segment target).
- **Automatic legacy buffer cleanup**: Upgrades will automatically purge any leftover `.ts` segments from `/media/recordings/.motion_buffer/`.
- **4-second segment target**: Increased default `segment_seconds` from 2s to 4s to further reduce segment churn while maintaining conservative boundary coverage.
- **Hardened segment file validation**: `segment_files` now inspects file size (`st_size > 0`) and modification age (`mtime < now - 0.5s`) instead of relying on naive list position slicing (`files[:-1]`).
- **Synchronous finalization retry logic**: Incident finalization now detects failures and automatically retries (up to 3 attempts with 3s delays), while actively protecting required buffered segments in memory during the retry window.
- **Clean restart recovery**: Pre-restart incidents are cleanly abandoned on container startup since RAM-backed buffers reset across container lifecycles.
- **Retained proven audio & state architecture**: Preserved the AAC audio conversion + asynchronous resampling (`aresample=async=1:first_pts=0`) and the race-free atomic incident detachment model.
- **Network documentation**: Clarified that Home Assistant Core and apps run in separate container namespaces, requiring the Home Assistant host LAN IP (not `127.0.0.1`) for REST commands.

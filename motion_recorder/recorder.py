#!/usr/bin/env python3

import json
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

OPTIONS_FILE = Path("/data/options.json")
STATE_FILE = Path("/data/incident_state.json")

with OPTIONS_FILE.open("r", encoding="utf-8") as f:
    cfg = json.load(f)

CAMERA_IP = cfg["camera_ip"]
USERNAME = cfg["camera_username"]
PASSWORD = cfg["camera_password"]
STREAM = cfg.get("stream", "stream1")
PRE_ROLL = float(cfg.get("pre_roll", 5))
POST_ROLL = float(cfg.get("post_roll", 30))
SEGMENT_SECONDS = float(cfg.get("segment_seconds", 4))
PORT = 8099

OUTPUT_DIR = Path("/media/recordings")
BUFFER_DIR = Path("/tmp/camera_motion_buffer")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
BUFFER_DIR.mkdir(parents=True, exist_ok=True)

# Clean up legacy disk-based buffer directory from pre-0.3.0 versions if present
legacy_buffer = OUTPUT_DIR / ".motion_buffer"
if legacy_buffer.exists():
    try:
        for old_file in legacy_buffer.glob("*.ts"):
            try:
                old_file.unlink()
            except OSError:
                pass
        legacy_buffer.rmdir()
    except OSError:
        pass

RTSP_URL = (
    f"rtsp://{quote(USERNAME, safe='')}:{quote(PASSWORD, safe='')}"
    f"@{CAMERA_IP}:554/{STREAM}"
)

lock = threading.Lock()
stop_event = threading.Event()

first_motion = None
last_motion = None
ffmpeg_proc = None
proc_started_at = time.time()

INITIAL_RETRY_DELAY = 5.0
MAX_RETRY_DELAY = 30.0
STALL_THRESHOLD = max(15.0, SEGMENT_SECONDS * 3 + 5)
STARTUP_GRACE_PERIOD = max(20.0, SEGMENT_SECONDS * 3 + 10)


def log(msg):
    print(msg, flush=True)


def save_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "first_motion": first_motion,
                "last_motion": last_motion,
            },
            f,
        )
    tmp.replace(STATE_FILE)


def load_state():
    global first_motion, last_motion

    try:
        if STATE_FILE.exists():
            with STATE_FILE.open("r", encoding="utf-8") as f:
                state = json.load(f)

            recovered_first = state.get("first_motion")
            recovered_last = state.get("last_motion")

            if recovered_first is not None:
                log(
                    "[state] abandoned unfinished incident from prior run "
                    f"(first={recovered_first} last={recovered_last}); "
                    "RAM buffer was cleared on restart"
                )

        first_motion = None
        last_motion = None
        save_state()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        first_motion = None
        last_motion = None


def register_motion(ts=None):
    global first_motion, last_motion

    now = time.time()

    try:
        ts = float(ts) if ts is not None else now
    except (TypeError, ValueError):
        ts = now

    # Avoid a malformed client timestamp corrupting the incident.
    if abs(ts - now) > 300:
        ts = now

    with lock:
        if first_motion is None:
            first_motion = ts
            log(
                "[event] new incident "
                f"{datetime.fromtimestamp(ts).isoformat(timespec='seconds')}"
            )

        last_motion = ts
        save_state()

        log(
            "[event] motion pulse; stop deadline "
            f"{datetime.fromtimestamp(ts + POST_ROLL).isoformat(timespec='seconds')}"
        )


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/motion":
            self.send_response(404)
            self.end_headers()
            return

        ts = None

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                body = json.loads(self.rfile.read(length))
                ts = body.get("ts")
        except Exception:
            ts = None

        register_motion(ts)

        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        now = time.time()
        last_seg = latest_segment_time()

        with lock:
            ffmpeg_running = (ffmpeg_proc is not None) and (ffmpeg_proc.poll() is None)
            seg_age = round(max(0.0, now - last_seg), 1) if last_seg > 0 else None

            if not ffmpeg_running:
                stream_healthy = False
                stream_status = "stopped"
            elif last_seg > 0 and (now - last_seg <= STALL_THRESHOLD):
                stream_healthy = True
                stream_status = "healthy"
            elif last_seg == 0 and (now - proc_started_at <= STARTUP_GRACE_PERIOD):
                stream_healthy = True
                stream_status = "starting"
            else:
                stream_healthy = False
                stream_status = "stalled"

            body = {
                "ok": stream_healthy,
                "stream_healthy": stream_healthy,
                "stream_status": stream_status,
                "last_segment_age_seconds": seg_age,
                "incident_active": first_motion is not None,
                "first_motion": first_motion,
                "last_motion": last_motion,
            }

        data = json.dumps(body).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def latest_segment_time():
    latest = 0.0
    for path in BUFFER_DIR.glob("*.ts"):
        try:
            stat = path.stat()
            if stat.st_size > 0 and stat.st_mtime > latest:
                latest = stat.st_mtime
        except OSError:
            pass
    return latest


def segment_files(include_open=False):
    entries = []

    for path in BUFFER_DIR.glob("*.ts"):
        try:
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            pass

    entries.sort(key=lambda item: item[0])

    if not entries:
        return []

    # The newest segment is the one FFmpeg may still be writing.
    open_path = entries[-1][2] if not include_open else None

    files = []

    for mtime, size, path in entries:
        if path == open_path:
            continue
        if size == 0:
            continue
        files.append((mtime, path))

    return files


def segmenter():
    global ffmpeg_proc, proc_started_at

    pattern = str(BUFFER_DIR / "%Y%m%dT%H%M%S.ts")
    retry_delay = INITIAL_RETRY_DELAY

    while not stop_event.is_set():
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-nostdin",

            "-fflags", "+genpts",
            "-use_wallclock_as_timestamps", "1",
            "-rtsp_transport", "tcp",
            "-timeout", "10000000",
            "-i", RTSP_URL,

            "-copytb", "1",

            "-map", "0:v:0",
            "-map", "0:a?",

            "-c:v", "copy",
            "-af", "aresample=async=1:first_pts=0",
            "-c:a", "aac",
            "-b:a", "32k",
            "-ac", "1",
            "-ar", "16000",

            "-f", "segment",
            "-segment_time", str(SEGMENT_SECONDS),
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-y",
            pattern,
        ]

        log("[ffmpeg] starting RTSP rolling segmenter")
        with lock:
            proc_started_at = time.time()
            ffmpeg_proc = subprocess.Popen(cmd)

        stalled = False
        while not stop_event.is_set():
            rc = ffmpeg_proc.poll()
            if rc is not None:
                break

            now = time.time()
            last_seg = latest_segment_time()

            if last_seg > 0:
                if now - last_seg > STALL_THRESHOLD:
                    log(
                        f"[watchdog] WARNING: stream stalled (no segment updates for "
                        f"{int(now - last_seg)}s); killing FFmpeg"
                    )
                    stalled = True
                    break
            elif now - proc_started_at > STARTUP_GRACE_PERIOD:
                log(
                    f"[watchdog] WARNING: stream startup timed out (no segments produced in "
                    f"{int(now - proc_started_at)}s); killing FFmpeg"
                )
                stalled = True
                break

            time.sleep(1)

        if stop_event.is_set():
            if ffmpeg_proc.poll() is None:
                ffmpeg_proc.terminate()
                try:
                    ffmpeg_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    ffmpeg_proc.kill()
            return

        if stalled and ffmpeg_proc.poll() is None:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log("[watchdog] FFmpeg did not exit on SIGTERM; killing with SIGKILL")
                ffmpeg_proc.kill()
                try:
                    ffmpeg_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        rc = ffmpeg_proc.poll()

        run_duration = time.time() - proc_started_at
        if run_duration >= 30.0 and latest_segment_time() > proc_started_at:
            retry_delay = INITIAL_RETRY_DELAY

        log(f"[ffmpeg] exited rc={rc}; retrying in {int(retry_delay)} seconds")
        stop_event.wait(retry_delay)
        retry_delay = min(MAX_RETRY_DELAY, retry_delay * 2)


def wait_for_closed_segment_covering(target_ts, timeout=20):
    deadline = time.time() + timeout

    while not stop_event.is_set() and time.time() < deadline:
        files = segment_files(include_open=False)

        if files and files[-1][0] >= target_ts:
            return True

        time.sleep(0.5)

    return False


def choose_segments(start_ts, end_ts):
    files = segment_files(include_open=False)

    if not files:
        return []

    start_index = None
    end_index = None

    # mtime is approximately the closing time of each segment.
    # First selected segment therefore begins slightly before start_ts.
    for i, (mtime, _) in enumerate(files):
        if start_index is None and mtime >= start_ts:
            start_index = i

        if mtime >= end_ts:
            end_index = i
            break

    if start_index is None:
        return []

    if end_index is None:
        end_index = len(files) - 1

    return [path for _, path in files[start_index : end_index + 1]]


def unique_output(start_ts):
    stamp = datetime.fromtimestamp(
        start_ts, tz=timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    candidate = OUTPUT_DIR / f"motion_{stamp}.mp4"

    if not candidate.exists():
        return candidate

    n = 2
    while True:
        candidate = OUTPUT_DIR / f"motion_{stamp}_{n}.mp4"
        if not candidate.exists():
            return candidate
        n += 1


def finalize_incident(start_motion, end_motion):
    desired_start = start_motion - PRE_ROLL
    desired_end = end_motion + POST_ROLL

    # Wait until FFmpeg has CLOSED a segment whose end is beyond
    # the requested post-roll boundary.
    covered = wait_for_closed_segment_covering(
        desired_end,
        timeout=max(20, int(SEGMENT_SECONDS * 5)),
    )

    if not covered:
        log("[event] WARNING: timed out waiting for a closed tail segment")

    segments = choose_segments(desired_start, desired_end)

    if not segments:
        log("[event] ERROR: no closed buffered segments found")
        return False

    concat_file = Path("/tmp/concat.txt")

    try:
        with concat_file.open("w", encoding="utf-8") as f:
            for path in segments:
                # Generated buffer paths contain no single quotes.
                f.write(f"file '{path}'\n")

        final_path = unique_output(start_motion)
        temp_path = final_path.with_suffix(".tmp.mp4")

        # Video and audio were already normalized in the rolling buffer.
        # Copy both streams into the final MP4 without re-encoding.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-nostdin",

            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),

            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "copy",
            "-c:a", "copy",

            "-movflags", "+faststart",
            "-y",
            str(temp_path),
        ]

        log(
            f"[event] combining {len(segments)} segments "
            f"into {final_path.name}"
        )

        result = subprocess.run(cmd)

        if result.returncode == 0 and temp_path.exists():
            temp_path.replace(final_path)
            log(f"[event] saved {final_path}")
            return True
        else:
            log(f"[event] ERROR: final ffmpeg returned {result.returncode}")
            try:
                temp_path.unlink()
            except OSError:
                pass
            return False
    finally:
        try:
            concat_file.unlink()
        except OSError:
            pass


def cleanup_buffer(protected_start=None):
    with lock:
        active_start = first_motion

    if active_start is not None and protected_start is not None:
        candidate_start = min(active_start, protected_start)
    elif active_start is not None:
        candidate_start = active_start
    elif protected_start is not None:
        candidate_start = protected_start
    else:
        candidate_start = None

    # Normal idle buffer: ~60 seconds.
    # During an incident or retry, retain every segment needed from pre-roll onward,
    # no matter how long the incident lasts.
    if candidate_start is None:
        cutoff = time.time() - max(60, PRE_ROLL + 20)
    else:
        cutoff = (
            candidate_start
            - PRE_ROLL
            - max(5, SEGMENT_SECONDS * 2)
        )

    files = segment_files(include_open=False)

    for mtime, path in files:
        if mtime >= cutoff:
            break

        try:
            path.unlink()
        except OSError:
            pass


MAX_RETRIES = 3
RETRY_DELAY = 3.0


def monitor():
    global first_motion, last_motion

    pending_retry = None  # (start_motion, end_motion)
    retry_count = 0
    next_retry_time = 0.0

    while not stop_event.is_set():
        now = time.time()
        job = None

        with lock:
            if (
                first_motion is not None
                and last_motion is not None
                and now >= last_motion + POST_ROLL
            ):
                job = (first_motion, last_motion)

                # Clear the incident BEFORE rendering it.
                # If a fresh pulse arrives during or after rendering,
                # it correctly becomes a new incident.
                first_motion = None
                last_motion = None
                save_state()

                # If an older failed job was still pending retry, drop it in favor of the newer incident
                if pending_retry is not None:
                    log(
                        "[event] WARNING: discarding previous failed incident "
                        f"to prioritize newer incident {job[0]}"
                    )
                    pending_retry = None
                    retry_count = 0

        # If no new incident is ready to render, check if a retry is scheduled
        if job is None and pending_retry is not None and now >= next_retry_time:
            job = pending_retry

        if job is not None:
            is_retry = (pending_retry is not None and job == pending_retry)
            success = finalize_incident(*job)

            if success:
                if is_retry:
                    log(f"[event] retry succeeded for incident {job[0]}")
                    pending_retry = None
                    retry_count = 0
            else:
                # Rendering failed
                if is_retry:
                    retry_count += 1
                else:
                    pending_retry = job
                    retry_count = 1

                if retry_count < MAX_RETRIES:
                    next_retry_time = time.time() + RETRY_DELAY
                    log(
                        f"[event] scheduling retry {retry_count}/{MAX_RETRIES} "
                        f"for incident in {RETRY_DELAY:.1f}s"
                    )
                else:
                    log(
                        f"[event] ERROR: incident failed after {MAX_RETRIES} attempts; "
                        "abandoning"
                    )
                    pending_retry = None
                    retry_count = 0

        protected_start = pending_retry[0] if pending_retry is not None else None
        cleanup_buffer(protected_start=protected_start)
        time.sleep(1)


def handle_signal(signum, frame):
    stop_event.set()


def main():
    load_state()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    threading.Thread(
        target=segmenter,
        daemon=True,
        name="segmenter",
    ).start()

    threading.Thread(
        target=monitor,
        daemon=True,
        name="monitor",
    ).start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

    threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="http",
    ).start()

    log(f"[http] listening on port {PORT}")

    try:
        while not stop_event.wait(1):
            pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

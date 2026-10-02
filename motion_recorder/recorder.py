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
SEGMENT_SECONDS = float(cfg.get("segment_seconds", 2))
PORT = 8099

OUTPUT_DIR = Path("/media/recordings")
BUFFER_DIR = OUTPUT_DIR / ".motion_buffer"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
BUFFER_DIR.mkdir(parents=True, exist_ok=True)

RTSP_URL = (
    f"rtsp://{quote(USERNAME, safe='')}:{quote(PASSWORD, safe='')}"
    f"@{CAMERA_IP}:554/{STREAM}"
)

lock = threading.Lock()
stop_event = threading.Event()

first_motion = None
last_motion = None
ffmpeg_proc = None


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
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)

        first_motion = state.get("first_motion")
        last_motion = state.get("last_motion")

        if first_motion is not None:
            log(
                "[state] recovered unfinished incident "
                f"first={first_motion} last={last_motion}"
            )
    except (FileNotFoundError, json.JSONDecodeError):
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

        with lock:
            body = {
                "ok": True,
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


def segment_files(include_open=False):
    files = []

    for path in BUFFER_DIR.glob("*.ts"):
        try:
            files.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            pass

    files.sort(key=lambda item: item[0])

    # FFmpeg is normally writing the newest file right now.
    # Never concatenate/delete that file until a newer segment exists.
    if not include_open and len(files) >= 1:
        files = files[:-1]

    return files


def segmenter():
    global ffmpeg_proc

    pattern = str(BUFFER_DIR / "%Y%m%dT%H%M%S.ts")

    while not stop_event.is_set():
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-nostdin",

            "-fflags", "+genpts",
            "-use_wallclock_as_timestamps", "1",
            "-rtsp_transport", "tcp",
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
        ffmpeg_proc = subprocess.Popen(cmd)

        while not stop_event.is_set():
            rc = ffmpeg_proc.poll()
            if rc is not None:
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

        log(f"[ffmpeg] exited rc={rc}; retrying in 5 seconds")
        time.sleep(5)


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
        timeout=max(20, SEGMENT_SECONDS * 5),
    )

    if not covered:
        log("[event] WARNING: timed out waiting for a closed tail segment")

    segments = choose_segments(desired_start, desired_end)

    if not segments:
        log("[event] ERROR: no closed buffered segments found")
        return

    concat_file = Path("/data/concat.txt")

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
    else:
        log(f"[event] ERROR: final ffmpeg returned {result.returncode}")
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def cleanup_buffer():
    with lock:
        active_start = first_motion

    # Normal idle buffer: ~60 seconds.
    # During an incident, retain every segment needed from pre-roll onward,
    # no matter how long the incident lasts.
    if active_start is None:
        cutoff = time.time() - max(60, PRE_ROLL + 20)
    else:
        cutoff = (
            active_start
            - PRE_ROLL
            - max(5, SEGMENT_SECONDS * 2)
        )

    files = segment_files(include_open=False)

    for mtime, path in files:
        if mtime >= cutoff:
            break

        try:
            path.unlink()
        except FileNotFoundError:
            pass


def monitor():
    global first_motion, last_motion

    while not stop_event.is_set():
        job = None

        with lock:
            if (
                first_motion is not None
                and last_motion is not None
                and time.time() >= last_motion + POST_ROLL
            ):
                job = (first_motion, last_motion)

                # Clear the incident BEFORE rendering it.
                # If a fresh pulse now arrives after the 30-second gap,
                # it correctly becomes a new incident.
                first_motion = None
                last_motion = None
                save_state()

        if job is not None:
            finalize_incident(*job)

        cleanup_buffer()
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

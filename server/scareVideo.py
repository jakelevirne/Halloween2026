#!/usr/bin/env python3
"""
Jump-scare video player (spike).

Loops a "calm" video fullscreen until a scare is triggered, then plays the
scare video (starting partway in), then goes back to the calm video.

Two scare modes:
    blend (default)  The scare video is blended over the calm video, so the
                     skeleton looks like it is pushing through the Bee Gees.
                     The calm video keeps playing underneath. Audio hard cuts
                     to the scare audio, then back.
    cut              Hard cut to the scare video, then back to the calm video
                     where it left off.

Playback is done by mpv, controlled over its JSON IPC socket. One fullscreen
window stays open the whole time. Blend mode swaps mpv's lavfi-complex filter
graph at runtime for the video. In both modes the scare audio comes from a
second, audio-only mpv that sits paused at the start point until the trigger.

Setup:
    brew install mpv     (also installs ffmpeg/ffprobe)

Usage:
    uv run scareVideo.py                 # fullscreen on the main display
    uv run scareVideo.py --screen 1      # fullscreen on another display
    uv run scareVideo.py --windowed      # in a window, for testing
    uv run scareVideo.py --mode cut      # hard cut instead of blending
    uv run scareVideo.py --blend screen  # try another ffmpeg blend mode

Keys:
    s or space   trigger the scare (in the terminal)
    s            trigger the scare (in the mpv window)
    q            quit (terminal or mpv window)

Using it from hauntedHouseLoop2025.py:
    from scareVideo import ScareVideoPlayer
    player = ScareVideoPlayer()
    await player.start()            # once, inside the asyncio loop
    await player.trigger_scare()    # when the sensor trips
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"
LOOP_VIDEO = MEDIA_DIR / "BeeGeesLive.mp4"
SCARE_VIDEO = MEDIA_DIR / "PP_StartleScare1_Wall_Spotlight_H.mp4"
SCARE_START_SECONDS = 2.6      # about half a second of near-silence before the startle hits at ~3.1s
SCARE_MODE = "blend"           # "blend" or "cut"
BLEND_MODE = "lighten"         # any ffmpeg blend mode: lighten, screen, addition, ...
LOOP_DIM = 0.35                # blend mode: loop video brightness during the scare (1 = normal, 0 = black)
SCARE_AUDIO_DELAY = 0.1        # the scare video takes ~0.1s longer to appear than the audio
# The Bee Gees track is mastered near full scale (-8.5 LUFS) and the startle is ~6 dB quieter,
# so turn the loop down and the scare up (with a limiter so the boost doesn't clip).
LOOP_GAIN_DB = -8
SCARE_GAIN_DB = 6
RESUME_WHERE_LEFT_OFF = True   # cut mode only. False = restart the loop video after a scare

# Filter graph used when no scare is playing: loop video and its audio, untouched
PASSTHROUGH_GRAPH = "[vid1]null[vo];[aid1]anull[ao]"


# Simple logging function with timestamp
def log(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
    print(f"[{timestamp}] {message}")


def probe_duration(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, check=True)
    return float(result.stdout)


class Mpv:
    """One mpv process, controlled over its JSON IPC socket."""

    def __init__(self, name, on_event=None):
        self.name = name
        self.on_event = on_event  # called with each event message (a dict). Must not await mpv commands.
        self.closed = asyncio.Event()  # set when mpv exits
        self._proc = None
        self._reader = None
        self._writer = None
        self._socket_dir = None
        self._request_id = 0
        self._pending = {}

    async def start(self, args):
        # Short temp dir: macOS limits Unix socket paths to 104 characters
        self._socket_dir = tempfile.mkdtemp(prefix="scareVideo-")
        socket_path = os.path.join(self._socket_dir, "mpv.sock")
        args = ["mpv", f"--input-ipc-server={socket_path}", "--input-terminal=no", "--really-quiet", *args]
        log(f"Starting {self.name} mpv: {' '.join(args)}")
        self._proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL)
        asyncio.create_task(self._watch_process())

        # Wait for mpv to create its IPC socket
        for _ in range(100):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(socket_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError(f"Timed out connecting to {self.name} mpv IPC socket")
        asyncio.create_task(self._read_loop())

    async def command(self, *command):
        if self._writer is None or self.closed.is_set():
            raise RuntimeError(f"{self.name} mpv is not running")
        self._request_id += 1
        request_id = self._request_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._writer.write((json.dumps({"command": list(command), "request_id": request_id}) + "\n").encode())
        await self._writer.drain()
        response = await future
        if response.get("error") != "success":
            log(f"{self.name} mpv command {command} failed: {response.get('error')}")
        return response

    async def get(self, name):
        response = await self.command("get_property", name)
        return response.get("data")

    async def set(self, name, value):
        await self.command("set_property", name, value)

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            try:
                await self.command("quit")
            except (ConnectionError, RuntimeError):
                self._proc.terminate()
            await self._proc.wait()
        if self._socket_dir:
            shutil.rmtree(self._socket_dir, ignore_errors=True)

    async def _read_loop(self):
        while line := await self._reader.readline():
            message = json.loads(line)
            if "request_id" in message:
                future = self._pending.pop(message["request_id"], None)
                if future and not future.done():
                    future.set_result(message)
            elif self.on_event:
                self.on_event(message)

        # Socket closed: mpv is gone
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(f"{self.name} mpv closed"))
        self._pending.clear()

    async def _watch_process(self):
        await self._proc.wait()
        log(f"{self.name} mpv exited (code {self._proc.returncode})")
        self.closed.set()


class ScareVideoPlayer:
    def __init__(self, loop_video=LOOP_VIDEO, scare_video=SCARE_VIDEO, scare_start=SCARE_START_SECONDS,
                 mode=SCARE_MODE, blend_mode=BLEND_MODE, screen=None, fullscreen=True, extra_mpv_args=()):
        self.loop_video = Path(loop_video)
        self.scare_video = Path(scare_video)
        self.scare_start = scare_start
        self.mode = mode
        self.blend_mode = blend_mode
        self.screen = screen
        self.fullscreen = fullscreen
        self.extra_mpv_args = list(extra_mpv_args)
        self.scaring = False  # True from trigger until the loop video is back to normal
        self.video = Mpv("video", on_event=self._on_video_event)
        self.audio = Mpv("audio", on_event=self._on_audio_event)  # blend mode only
        self.closed = self.video.closed
        self._trigger_time = None
        self._loop_position = 0
        self._scare_length = None

    async def start(self):
        if shutil.which("mpv") is None:
            raise RuntimeError("mpv not found. Install it with: brew install mpv")
        for video in (self.loop_video, self.scare_video):
            if not video.exists():
                raise FileNotFoundError(video)

        video_args = [
            "--idle=yes",
            "--force-window=immediate",
            "--keep-open=yes",        # hold the last frame at EOF instead of unloading
            "--loop-file=inf",        # the scare video overrides this per file
            "--osd-level=0",
            "--no-osc",
            "--cursor-autohide=always",
            "--hwdec=auto-copy",      # hardware decode that still works with filters
            f"--af=lavfi=[volume={LOOP_GAIN_DB}dB]",
        ]
        if self.mode == "blend":
            # The scare video is opened inside an ffmpeg filter graph, where these characters need escaping
            if any(c in str(self.scare_video) for c in ":,;[]'\\"):
                raise ValueError(f"Blend mode can't handle : , ; [ ] ' or \\ in the scare video path: {self.scare_video}")
            self._scare_length = probe_duration(self.scare_video) - self.scare_start
            video_args.append(f"--lavfi-complex={PASSTHROUGH_GRAPH}")
        if self.fullscreen:
            video_args.append("--fs")
        if self.screen is not None:
            video_args += [f"--screen={self.screen}", f"--fs-screen={self.screen}"]

        await self.video.start(video_args + self.extra_mpv_args)
        await self.video.command("observe_property", 1, "eof-reached")
        await self.video.command("keybind", "s", "script-message scare")
        await self._load_loop_video(0)

        # Audio-only player for the scare sound, waiting paused at the start point for the trigger
        await self.audio.start(["--idle=yes", "--no-video", "--keep-open=yes", "--pause",
                                f"--af=lavfi=[volume={SCARE_GAIN_DB}dB,alimiter=limit=0.95:level=disabled]"]
                               + self.extra_mpv_args)
        await self.audio.command("observe_property", 1, "eof-reached")
        await self.audio.command("loadfile", str(self.scare_video), "replace", -1, f"start={self.scare_start}")

    async def trigger_scare(self):
        """Start the scare. Returns False if a scare is already playing."""
        if self.scaring or self.closed.is_set():
            log("Scare ignored (already scaring)")
            return False
        self.scaring = True
        self._trigger_time = time.monotonic()
        if self.mode == "blend":
            await self._start_blend_scare()
        else:
            await self._start_cut_scare()
        return True

    async def stop(self):
        await self.video.stop()
        await self.audio.stop()

    # --- blend mode ---

    async def _start_blend_scare(self):
        position = await self.video.get("time-pos") or 0
        duration = await self.video.get("duration") or 0
        if duration - position < self._scare_length + 1:
            # Looping mid-scare would reset the filter graph, so restart the loop video first
            await self.video.command("seek", 0, "absolute", "exact")
            position = 0
        log(f"SCARE! (blending over loop video at {position:.1f}s)")
        graph = await self._blend_graph(position)
        await asyncio.gather(
            self.video.set("lavfi-complex", graph),
            self.video.set("mute", True),
            self._start_scare_audio(),
        )
        log(f"Scare started {(time.monotonic() - self._trigger_time) * 1000:.0f} ms after trigger")
        self._trigger_time = None

    async def _end_blend_scare(self):
        # The graph already shows the plain loop video by now. Swapping it out keeps a later
        # loop or seek from rebuilding the graph and replaying the scare.
        await self.video.set("lavfi-complex", PASSTHROUGH_GRAPH)
        await self.video.set("mute", False)
        await self._reset_scare_audio()
        log("Scare finished")
        self.scaring = False

    async def _blend_graph(self, position):
        width = await self.video.get("width")
        height = await self.video.get("height")
        return (
            # movie= starts at the keyframe before seek_point, so trim to the exact start
            f"movie={self.scare_video}:seek_point={self.scare_start},trim=start={self.scare_start},"
            # Shift the scare's timestamps so its first frame lines up with the loop video's current frame
            f"setpts=PTS-STARTPTS+{position}/TB,scale={width}:{height},format=gbrp[top];"
            # Dim the loop video so the scare stands out
            f"[vid1]format=gbrp,colorchannelmixer=rr={LOOP_DIM}:gg={LOOP_DIM}:bb={LOOP_DIM}[bottom];"
            # Blend in RGB so the loop video keeps its colors. Once the scare ends, pass the loop video through.
            f"[bottom][top]blend=all_mode={self.blend_mode}:eof_action=pass[vo];"
            # Audio stays connected so the track never changes (that stalls playback). It's muted instead.
            f"[aid1]anull[ao]"
        )

    def _on_audio_event(self, message):
        if message.get("event") == "property-change" and message.get("name") == "eof-reached":
            if message.get("data") and self.scaring and self.mode == "blend":
                asyncio.create_task(self._end_blend_scare())

    # --- cut mode ---

    async def _start_cut_scare(self):
        position = await self.video.get("time-pos") if RESUME_WHERE_LEFT_OFF else None
        self._loop_position = position or 0
        log(f"SCARE! (loop video was at {self._loop_position:.1f}s)")
        # The scare video's own audio is muted: the audio player plays it, with SCARE_GAIN_DB applied
        await self.video.set("mute", True)
        await asyncio.gather(
            self.video.command("loadfile", str(self.scare_video), "replace", -1,
                               f"start={self.scare_start},loop-file=no,pause=no"),
            self._start_scare_audio(),
        )

    async def _end_cut_scare(self):
        log("Scare finished, back to the loop video")
        await self._load_loop_video(self._loop_position)
        await self.video.set("mute", False)
        await self._reset_scare_audio()
        self.scaring = False

    # --- shared ---

    async def _start_scare_audio(self):
        if SCARE_AUDIO_DELAY:
            await asyncio.sleep(SCARE_AUDIO_DELAY)
        await self.audio.set("pause", False)

    async def _reset_scare_audio(self):
        # Rewind the audio player and leave it paused, ready for the next scare
        await self.audio.set("pause", True)
        await self.audio.command("seek", self.scare_start, "absolute", "exact")

    async def _load_loop_video(self, position):
        await self.video.command("loadfile", str(self.loop_video), "replace", -1, f"start={position},pause=no")

    def _on_video_event(self, message):
        event = message.get("event")
        if event == "property-change" and message.get("name") == "eof-reached":
            if message.get("data") and self.scaring and self.mode == "cut":
                asyncio.create_task(self._end_cut_scare())
        elif event == "client-message" and message.get("args") == ["scare"]:
            asyncio.create_task(self.trigger_scare())
        elif event == "playback-restart" and self._trigger_time is not None and self.mode == "cut":
            log(f"Scare on screen {(time.monotonic() - self._trigger_time) * 1000:.0f} ms after trigger")
            self._trigger_time = None


# Read single keypresses from the terminal: s/space = scare, q = quit
async def keyboard_loop(player, stop_event):
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        await stop_event.wait()
        return
    loop = asyncio.get_running_loop()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def on_key():
        key = os.read(fd, 1).decode(errors="ignore")
        if key in ("s", " "):
            loop.create_task(player.trigger_scare())
        elif key == "q":
            stop_event.set()

    loop.add_reader(fd, on_key)
    try:
        await stop_event.wait()
    finally:
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


async def main():
    parser = argparse.ArgumentParser(description="Loop a video and trigger a jump scare on a keypress.")
    parser.add_argument("--screen", type=int, help="Display number for fullscreen (0 = main)")
    parser.add_argument("--windowed", action="store_true", help="Play in a window instead of fullscreen")
    parser.add_argument("--mode", choices=["blend", "cut"], default=SCARE_MODE, help="Blend the scare over the loop video, or hard cut to it")
    parser.add_argument("--blend", default=BLEND_MODE, help="ffmpeg blend mode for blend mode (lighten, screen, addition, ...)")
    args = parser.parse_args()

    player = ScareVideoPlayer(mode=args.mode, blend_mode=args.blend, screen=args.screen, fullscreen=not args.windowed)
    await player.start()
    log(f"Looping ({args.mode} mode). Press s or space to scare, q to quit.")

    stop_event = asyncio.Event()
    asyncio.create_task(player.closed.wait()).add_done_callback(lambda _: stop_event.set())
    try:
        await keyboard_loop(player, stop_event)
    finally:
        await player.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

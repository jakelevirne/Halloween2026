"""
Plays the show's media for ShowDirector cues.

Screen (mpv, picture only):
    the still image whenever the show isn't running, the Bee Gees during the
    show, the startle blended over them for the scare, and back to the still
    when the Bee Gees clip ends or the house resets.
Sound (the audio interface, through audioOut.py):
    output 6: the Bee Gees, silenced while the startle plays, turned down for
              the final announcement
    output 5: the announcements in media/announcements/
    outputs 1-4: the chair bass shakers, a 45 Hz shake starting with the startle's roar

    player = ShowPlayer()
    await player.start()
    await player.cue("welcome")     # cue names from showDirector.py

start_in_thread() runs a player on its own thread for apps that aren't asyncio,
like the radar map.
"""

import asyncio
import threading

import numpy as np

from audioOut import AudioOut, load_audio
from scareVideo import (BLEND_MODE, LOOP_DIM, LOOP_VIDEO, MEDIA_DIR, SCARE_AUDIO_DELAY, SCARE_GAIN_DB,
                        SCARE_START_SECONDS, SCARE_VIDEO, Mpv, log)

AUDIO_DEVICE = "UMC1820"
SHOW_CHANNEL = 6             # powered speaker for the Bee Gees and the startle
ANNOUNCE_CHANNEL = 5         # powered speaker for announcements
BASS_CHANNELS = (1, 2, 3, 4) # chair bass shakers, one per chair
BASS_HZ = 45                 # what the shakers respond to
BASS_LEVEL = 0.5             # the strength that felt right in the audioTest.py --tone test
BASS_ROAR_SECONDS = 0.2      # start shaking 0.3 s before the startle's roar (0.5 s in), since the
                             # shakers take a moment to build up and it felt late starting right on it
STILL_IMAGE = MEDIA_DIR / "BeeGeesStill.jpg"
ANNOUNCEMENTS_DIR = MEDIA_DIR / "announcements"
ANNOUNCEMENTS = {"welcome": "welcome.aiff", "take_seats": "take_seats.aiff", "outro": "outro.aiff"}
BEE_GEES_GAIN_DB = -20       # the Bee Gees track is mastered very loud; this keeps it well under the startle
DUCKED_GAIN = 0.25           # Bee Gees under the final announcement (a further -12 dB)
FULLSCREEN = False           # True: always fullscreen (the projector)
FULLSCREEN_DURING_SHOW = True  # otherwise fullscreen while the Bee Gees play, a window for the still
PASSTHROUGH_GRAPH = "[vid1]null[vo]"  # picture only: the audio is played separately


class ShowPlayer:
    def __init__(self, fullscreen=FULLSCREEN, screen=None):
        self.fullscreen = fullscreen
        self.screen = screen
        self.video = Mpv("show video", on_event=self._on_video_event)
        self.audio = None
        self._ready = asyncio.Event()
        self._showing = None          # "still" or "bee_gees"
        self._bee_gees = None         # the playing Bee Gees Sound
        self._bee_gees_level = 1.0    # its gain when the startle isn't playing
        self._scare = None            # the playing startle Sound
        self._shake = None            # the playing chair shake Sound
        self._waiting_for_bee_gees = False

    async def start(self):
        self.audio = AudioOut(AUDIO_DEVICE)
        rate = self.audio.rate
        log(f"Loading show audio for {self.audio.name} ({rate} Hz)")
        self._bee_gees_audio = load_audio(LOOP_VIDEO, rate, filters=f"volume={BEE_GEES_GAIN_DB}dB")
        self._scare_audio = load_audio(SCARE_VIDEO, rate, start=SCARE_START_SECONDS,
                                       filters=f"volume={SCARE_GAIN_DB}dB,alimiter=limit=0.95:level=disabled")
        self._bass = bass_shake(rate)
        self._announcements = {}
        for cue_name, file in ANNOUNCEMENTS.items():
            samples = load_audio(ANNOUNCEMENTS_DIR / file, rate)
            self._announcements[cue_name] = samples * (0.9 / max(abs(samples).max(), 1e-6))  # normalize the level
        self.audio.start()

        args = ["--idle=yes", "--force-window=immediate", "--keep-open=yes", "--no-audio",
                "--image-display-duration=inf", "--osd-level=0", "--no-osc", "--cursor-autohide=always",
                "--hwdec=auto-copy", f"--lavfi-complex={PASSTHROUGH_GRAPH}", "--autofit=50%",
                "--title=Show", "--native-fs=no"]  # instant fullscreen, not macOS's animated one
        if self.fullscreen:
            args.append("--fs")
        if self.screen is not None:
            args += [f"--screen={self.screen}", f"--fs-screen={self.screen}"]
        await self.video.start(args)
        await self.video.command("observe_property", 1, "eof-reached")
        await self._show_still()
        self._ready.set()
        log("Show player ready")

    async def cue(self, name):
        """Act on a ShowDirector cue."""
        await self._ready.wait()
        if name in self._announcements:
            self.audio.play(self._announcements[name], ANNOUNCE_CHANNEL)
        if name == "show_start":
            await self._start_bee_gees()
        elif name == "scare":
            await self._start_scare()
        elif name == "outro":
            self._bee_gees_level = DUCKED_GAIN
            if self._bee_gees and not self._scare:
                self._bee_gees.gain = DUCKED_GAIN
        elif name == "reset":
            await self._stop_show()

    async def stop(self):
        await self.video.stop()
        if self.audio:
            self.audio.close()

    async def _show_still(self):
        self._showing = "still"
        await self.video.command("loadfile", str(STILL_IMAGE), "replace")
        if FULLSCREEN_DURING_SHOW and not self.fullscreen:
            await self.video.set("fullscreen", False)

    async def _start_bee_gees(self):
        await self._stop_show(still=False)
        self._bee_gees_level = 1.0
        self._showing = "bee_gees"
        self._waiting_for_bee_gees = True  # the audio starts when the first frame is up (playback-restart)
        await self.video.command("loadfile", str(LOOP_VIDEO), "replace", -1, "pause=no")
        if FULLSCREEN_DURING_SHOW:
            await self.video.set("fullscreen", True)

    async def _start_scare(self):
        if self._showing != "bee_gees" or self._scare:
            log("Scare skipped: the Bee Gees aren't playing")
            return
        position = await self.video.get("time-pos") or 0
        await self.video.set("lavfi-complex", await self._blend_graph(position))
        await asyncio.sleep(SCARE_AUDIO_DELAY)  # the blended picture takes ~0.1 s to show up
        if self._bee_gees:
            self._bee_gees.gain = 0  # hard cut to the startle
        self._scare = self.audio.play(self._scare_audio, SHOW_CHANNEL)
        self._shake = self.audio.play(self._bass, BASS_CHANNELS)  # same instant, so it lines up with the roar
        asyncio.create_task(self._end_scare(self._scare))

    async def _end_scare(self, scare):
        await asyncio.sleep(len(self._scare_audio) / self.audio.rate)
        if self._scare is not scare:
            return  # the show was reset meanwhile
        self._scare = None
        await self.video.set("lavfi-complex", PASSTHROUGH_GRAPH)
        if self._bee_gees:
            # Swapping the blend in and out stalls the picture for a moment, so the Bee Gees' audio has run
            # ahead. While it's still muted, move it to where the picture is, then unmute.
            await asyncio.sleep(0.3)
            position = await self.video.get("time-pos")
            if position is not None and self._bee_gees:
                self._bee_gees.seek(position, self.audio.rate)
            await asyncio.sleep(0.05)
            if self._bee_gees:
                self._bee_gees.gain = self._bee_gees_level

    async def _stop_show(self, still=True):
        for sound in (self._bee_gees, self._scare, self._shake):
            if sound:
                sound.stop()
        self._bee_gees = self._scare = self._shake = None
        self._waiting_for_bee_gees = False
        await self.video.set("lavfi-complex", PASSTHROUGH_GRAPH)
        if still:
            await self._show_still()

    async def _blend_graph(self, position):
        width = await self.video.get("width")
        height = await self.video.get("height")
        return (
            # movie= starts at the keyframe before seek_point, so trim to the exact start
            f"movie={SCARE_VIDEO}:seek_point={SCARE_START_SECONDS},trim=start={SCARE_START_SECONDS},"
            # Line the startle's first frame up with the Bee Gees' current frame
            f"setpts=PTS-STARTPTS+{position}/TB,scale={width}:{height},format=gbrp[top];"
            f"[vid1]format=gbrp,colorchannelmixer=rr={LOOP_DIM}:gg={LOOP_DIM}:bb={LOOP_DIM}[bottom];"
            f"[bottom][top]blend=all_mode={BLEND_MODE}:eof_action=pass[vo]"
        )

    def _on_video_event(self, message):
        event = message.get("event")
        if event == "playback-restart" and self._waiting_for_bee_gees:
            self._waiting_for_bee_gees = False
            self._bee_gees = self.audio.play(self._bee_gees_audio, SHOW_CHANNEL, gain=self._bee_gees_level)
        elif event == "property-change" and message.get("name") == "eof-reached":
            if message.get("data") and self._showing == "bee_gees":
                log("Bee Gees clip ended, back to the still")
                asyncio.create_task(self._stop_show())


def bass_shake(rate):
    """45 Hz for the chair shakers, lined up with the startle audio: nothing until the roar,
    a steady shake through the loudest 2 s, then four 0.4 s pulses that fade out."""
    t = np.arange(int(rate * (BASS_ROAR_SECONDS + 6))) / rate
    envelope = ((t >= BASS_ROAR_SECONDS) & (t < BASS_ROAR_SECONDS + 2)).astype(np.float32)
    for pulse in range(4):
        start = BASS_ROAR_SECONDS + 2.4 + pulse * 0.8
        envelope[(t >= start) & (t < start + 0.4)] = 0.8 - pulse * 0.2
    ramp = int(rate * 0.02)  # soften every on/off edge so the shakers don't click
    envelope = np.convolve(envelope, np.ones(ramp) / ramp, mode="same")
    return (np.sin(2 * np.pi * BASS_HZ * t) * envelope * BASS_LEVEL).astype(np.float32)


def start_in_thread(**kwargs):
    """Run a ShowPlayer on its own asyncio loop in a background thread.
    Returns (send, stop): send(cue_name) queues a cue, stop() shuts the player down."""
    loop = asyncio.new_event_loop()
    player = None

    def run():
        nonlocal player
        asyncio.set_event_loop(loop)
        player = ShowPlayer(**kwargs)
        try:
            loop.run_until_complete(player.start())
        except Exception as e:
            log(f"Show player failed to start ({type(e).__name__}: {e}). Cues will only be shown, not played.")
            player = None
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()

    def send(cue_name):
        if player:
            asyncio.run_coroutine_threadsafe(player.cue(cue_name), loop)

    def stop():
        if player:
            asyncio.run_coroutine_threadsafe(player.stop(), loop).result(timeout=5)

    return send, stop

"""
Plays sounds on chosen outputs of the multichannel audio interface (the UMC1820).

One output stream stays open and mixes everything that's playing, so sounds can
overlap on the same or different outputs (the Bee Gees on one speaker while an
announcement plays on another). Each sound's volume can change while it plays.

    out = AudioOut("UMC1820")
    out.start()
    music = out.play(load_audio("song.mp4", out.rate), channel=5)
    rumble = out.play(samples, channel=(1, 2, 3, 4))   # the same on several outputs
    music.gain = 0.25      # turn it down (smoothly, over one audio block, so it doesn't click)
    music.stop()
    radio = out.play(song, channel=5, loop=True)
    radio.paused = True    # holds its place until set back to False
"""

import subprocess
import threading

import numpy as np
import sounddevice as sd


def load_audio(path, rate, start=0.0, filters=None):
    """A media file's audio as mono float32 samples at rate, from start seconds in,
    through optional ffmpeg audio filters (like "volume=-8dB")."""
    command = ["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path), "-vn", "-ac", "1", "-ar", str(int(rate))]
    if filters:
        command += ["-af", filters]
    command += ["-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(command, capture_output=True, check=True).stdout, dtype=np.float32)


class Sound:
    def __init__(self, samples, channels, gain, loop=False):
        self.samples = samples
        self.channels = channels  # counting from 1, like the labels on the interface
        self.gain = gain
        self.loop = loop
        self.paused = False
        self.position = 0
        self._last_gain = gain
        self.seek_to = None  # set to a sample index to jump there (applied on the audio thread)
        self.done = threading.Event()

    def stop(self):
        self.done.set()

    def seek(self, seconds, rate):
        self.seek_to = max(0, int(seconds * rate))

    @property
    def playing(self):
        return not self.done.is_set()


class AudioOut:
    def __init__(self, device_name):
        for index, device in enumerate(sd.query_devices()):
            if device_name.lower() in device["name"].lower() and device["max_output_channels"] > 0:
                break
        else:
            raise RuntimeError(f"Audio device '{device_name}' not found. Is it plugged in?")
        self.name = device["name"]
        self.channels = device["max_output_channels"]
        self.rate = int(device["default_samplerate"])
        self._sounds = []
        self._lock = threading.Lock()
        self._stream = sd.OutputStream(device=index, channels=self.channels, samplerate=self.rate,
                                       dtype="float32", callback=self._mix)

    def start(self):
        self._stream.start()

    def close(self):
        self._stream.close()

    def play(self, samples, channel, gain=1.0, loop=False):
        """Play samples on one output (channel=5) or the same on several (channel=(1, 2, 3, 4)),
        once or, with loop=True, over and over until stopped."""
        channels = (channel,) if isinstance(channel, int) else tuple(channel)
        for c in channels:
            if not 1 <= c <= self.channels:
                raise ValueError(f"{self.name} has outputs 1 to {self.channels}, not {c}")
        sound = Sound(samples, channels, gain, loop)
        with self._lock:
            self._sounds.append(sound)
        return sound

    def stop_all(self):
        with self._lock:
            for sound in self._sounds:
                sound.stop()

    def _mix(self, outdata, frames, _time, _status):
        # Runs on the audio thread: keep it quick
        outdata.fill(0)
        with self._lock:
            for sound in self._sounds:
                if not sound.playing or sound.paused:
                    continue
                if sound.seek_to is not None:
                    sound.position, sound.seek_to = sound.seek_to, None
                end = sound.position + frames
                chunk = sound.samples[sound.position:end]
                if sound.loop and len(chunk) < frames:
                    end = frames - len(chunk)
                    chunk = np.concatenate((chunk, sound.samples[:end]))
                sound.position = end
                gain = sound.gain
                if gain != sound._last_gain:
                    gain = np.linspace(sound._last_gain, gain, len(chunk), dtype=np.float32)  # no click
                    sound._last_gain = sound.gain
                for channel in sound.channels:
                    outdata[:len(chunk), channel - 1] += chunk * gain
                if sound.position >= len(sound.samples):
                    sound.done.set()
            self._sounds = [s for s in self._sounds if s.playing]

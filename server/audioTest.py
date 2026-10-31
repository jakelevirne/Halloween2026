#!/usr/bin/env python3
"""
Check the wiring of the audio interface's outputs, one at a time.

By default each output speaks its own number. With --tone, each output plays a
tone instead (45 Hz for the chair bass shakers) and, with --announce, a voice on
another output says which one is about to play. The voice is macOS's say.

Usage:
    uv run audioTest.py 1 2                         # "channel 1" on output 1, then "channel 2" on output 2
    uv run audioTest.py 5 6 --volume 0.6
    uv run audioTest.py 1 2 3 4 --tone 45 --announce 5 --label "bass channel"
"""

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

AUDIO_DEVICE = "UMC1820"


def find_device(name):
    for index, device in enumerate(sd.query_devices()):
        if name.lower() in device["name"].lower() and device["max_output_channels"] > 0:
            return index, device
    raise SystemExit(f"Audio device '{name}' not found. Is it plugged in?")


def spoken(text, samplerate):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "speech.wav"
        subprocess.run(["say", "-o", str(path), f"--data-format=LEF32@{int(samplerate)}", text], check=True)
        samples, _ = sf.read(path, dtype="float32")
    samples = samples if samples.ndim == 1 else samples[:, 0]
    return samples / max(abs(samples).max(), 1e-6)


def tone(hz, seconds, samplerate):
    t = np.arange(int(seconds * samplerate)) / samplerate
    fade = np.minimum(1, np.minimum(t, seconds - t) / 0.1)  # 0.1 s fade in and out, so the shakers don't thump
    return (np.sin(2 * np.pi * hz * t) * fade).astype("float32")


def play_on(samples, channels, outputs, index, rate):
    frames = np.zeros((len(samples) + int(rate * 0.4), outputs), dtype="float32")  # trailing silence
    for channel in [channels] if isinstance(channels, int) else channels:
        frames[:len(samples), channel - 1] = samples
    sd.play(frames, samplerate=rate, device=index, blocking=True)


def main():
    parser = argparse.ArgumentParser(description="Check the audio interface's outputs one at a time.")
    parser.add_argument("channels", type=int, nargs="+", help="Output channels to test, counting from 1")
    parser.add_argument("--volume", type=float, default=0.3, help="0 to 1 (default 0.3)")
    parser.add_argument("--tone", type=float, help="Play a tone of this many Hz instead of speaking")
    parser.add_argument("--seconds", type=float, default=2, help="How long the tone plays (default 2)")
    parser.add_argument("--announce", type=int, help="Output that says which channel is next (with --tone)")
    parser.add_argument("--label", default="channel", help='What to call each output, e.g. "bass channel"')
    parser.add_argument("--together", action="store_true", help="Play the tone on all the channels at once")
    args = parser.parse_args()

    index, device = find_device(AUDIO_DEVICE)
    outputs = device["max_output_channels"]
    rate = device["default_samplerate"]
    print(f"Using {device['name']} ({outputs} outputs, {rate:.0f} Hz)")
    channels = [c for c in args.channels if 1 <= c <= outputs]
    for channel in sorted(set(args.channels) - set(channels)):
        print(f"Skipping channel {channel}: the device has outputs 1 to {outputs}")
    if args.together and args.tone:
        if args.announce:
            print(f"Announcing 'all {args.label}s' on output {args.announce}")
            play_on(spoken(f"all {args.label}s", rate) * 0.9, args.announce, outputs, index, rate)
        print(f"Playing {args.tone:g} Hz for {args.seconds:g} s on outputs {', '.join(map(str, channels))} together")
        play_on(tone(args.tone, args.seconds, rate) * args.volume, channels, outputs, index, rate)
        time.sleep(0.5)
        return
    for channel in channels:
        name = f"{args.label} {channel}"
        if args.tone:
            if args.announce:
                print(f"Announcing '{name}' on output {args.announce}")
                play_on(spoken(name, rate) * 0.9, args.announce, outputs, index, rate)
            print(f"Playing {args.tone:g} Hz for {args.seconds:g} s on output {channel}")
            play_on(tone(args.tone, args.seconds, rate) * args.volume, channel, outputs, index, rate)
            time.sleep(0.5)
        else:
            print(f"Playing '{name}' on output {channel}")
            play_on(spoken(name, rate) * args.volume, channel, outputs, index, rate)


if __name__ == "__main__":
    main()

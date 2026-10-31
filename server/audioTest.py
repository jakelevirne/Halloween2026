#!/usr/bin/env python3
"""
Speak the channel number on each output of the audio interface, one at a time,
to check wiring. Uses macOS's say command for the voice.

Usage:
    uv run audioTest.py 1 2            # "channel 1" on output 1, then "channel 2" on output 2
    uv run audioTest.py 1 2 3 4 5 6 7 8
    uv run audioTest.py --volume 0.6 3
"""

import argparse
import subprocess
import tempfile
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
    return samples if samples.ndim == 1 else samples[:, 0]


def main():
    parser = argparse.ArgumentParser(description="Speak the channel number on each output, one at a time.")
    parser.add_argument("channels", type=int, nargs="+", help="Output channels to test, counting from 1")
    parser.add_argument("--volume", type=float, default=0.3, help="0 to 1 (default 0.3)")
    args = parser.parse_args()

    index, device = find_device(AUDIO_DEVICE)
    outputs = device["max_output_channels"]
    rate = device["default_samplerate"]
    print(f"Using {device['name']} ({outputs} outputs, {rate:.0f} Hz)")
    for channel in args.channels:
        if not 1 <= channel <= outputs:
            print(f"Skipping channel {channel}: the device has outputs 1 to {outputs}")
            continue
        voice = spoken(f"channel {channel}", rate) * args.volume
        frames = np.zeros((len(voice) + int(rate * 0.4), outputs), dtype="float32")  # trailing silence between channels
        frames[:len(voice), channel - 1] = voice
        print(f"Playing 'channel {channel}' on output {channel}")
        sd.play(frames, samplerate=rate, device=index, blocking=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
HLK-LD2460 radar over Bluetooth LE (experiment).

Connects to the radar by name and prints the X/Y position of each tracked
person. No pairing or password needed: the radar streams target reports on
characteristic FFF1 as soon as you subscribe (~7 per second).

Report frame (same as the UART protocol):
    F4 F3 F2 F1 | 04 | length (u16 LE, whole frame) | per target: x, y (i16 LE, 0.1 m) | F8 F7 F6 F5

Usage:
    uv run ld2460Radar.py                     # first device named LD2460*
    uv run ld2460Radar.py --name LD2460-2BF6
"""

import argparse
import asyncio
import struct
import time

from bleak import BleakClient, BleakScanner

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
HEADER = bytes.fromhex("f4f3f2f1")
TAIL = bytes.fromhex("f8f7f6f5")


# Simple logging function with timestamp
def log(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
    print(f"[{timestamp}] {message}")


def parse_frames(buffer):
    """Pull complete target reports out of buffer (a bytearray, consumed in place).
    Returns a list of reports, each a list of (x, y) in meters."""
    reports = []
    while True:
        start = buffer.find(HEADER)
        if start < 0:
            del buffer[:-3]  # keep a possible partial header
            return reports
        del buffer[:start]
        if len(buffer) < 7:
            return reports
        length = int.from_bytes(buffer[5:7], "little")
        if len(buffer) < length:
            return reports
        frame = bytes(buffer[:length])
        del buffer[:length]
        if frame[4] != 0x04 or frame[-4:] != TAIL or length < 11 or (length - 11) % 4:
            continue
        reports.append([(x / 10, y / 10) for x, y in struct.iter_unpack("<hh", frame[7:-4])])


async def watch(on_targets, name_prefix="LD2460"):
    """Connect to the radar and call on_targets(list of (x, y)) for every report. Reconnects forever."""
    while True:
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: (adv.local_name or d.name or "").startswith(name_prefix), timeout=10)
        if device is None:
            log(f"No {name_prefix}* radar found, retrying")
            continue
        disconnected = asyncio.Event()
        buffer = bytearray()

        def on_notify(_, data):
            buffer.extend(data)
            for targets in parse_frames(buffer):
                on_targets(targets)

        try:
            async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
                log(f"Connected to {device.name}")
                await client.start_notify(NOTIFY_UUID, on_notify)
                await disconnected.wait()
            log("Radar disconnected, reconnecting")
        except Exception as e:
            log(f"Radar connection failed ({e}), retrying")
            await asyncio.sleep(2)


async def main():
    parser = argparse.ArgumentParser(description="Print people positions from an HLK-LD2460 radar over BLE.")
    parser.add_argument("--name", default="LD2460", help="Device name or prefix (default: LD2460)")
    args = parser.parse_args()

    last = None

    def show(targets):
        nonlocal last
        if targets:
            log("  ".join(f"#{i + 1} x={x:+.1f}m y={y:+.1f}m" for i, (x, y) in enumerate(targets)))
        elif last:
            log("nobody")
        last = targets

    await watch(show, args.name)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

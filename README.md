# Halloween2025 🎃👻

A fully automated, sensor-driven haunted house experience powered by MQTT messaging and Python orchestration.

## [Watch the Complete Haunted House in Action](https://drive.google.com/drive/u/0/folders/1WyggTMg8_1pG_iuc28zmMbScnxubEyM8)

## What is This?

This project demonstrates a scalable, modular architecture for creating immersive haunted house experiences. Instead of hardcoding interactions between sensors and props, we use a message-based system where:

- **Sensors** (PIR motion detectors) publish events when triggered
- A **central server** orchestrates the response - coordinating multiple props, sounds, and effects
- **Props** (pneumatic actuators, motors, audio players) respond to commands from the server

The result? Complex, synchronized scares that respond dynamically to visitor movement throughout the space.

## Architecture Overview

### The Hardware: Identical ESP32-C3 Controllers

Every device in the haunted house runs the same firmware. Whether it's monitoring a door sensor or controlling a coffin actuator, they all:
- Connect to a local WiFi network
- Publish sensor readings to MQTT topics (`device/{MAC_ADDRESS}/sensor`)
- Listen for actuation commands on MQTT topics (`device/{MAC_ADDRESS}/actuator`)

This uniformity means:
- ✅ Flash once, deploy anywhere
- ✅ Add new props without custom code
- ✅ Swap devices easily if hardware fails
- ✅ Debug with consistent behavior across all devices

### The Brain: Python Server with MQTT

Two Python scripts run on a central server (a Mac Mini in our case):

**`hauntedHouseLoop2025.py`** - Physical prop orchestration
- Listens to all sensor topics via MQTT
- Detects consecutive sensor readings to filter false triggers
- Sends actuation commands to props (werewolf, scarecrow, coffin, etc.)
- Enforces cooldowns so props don't over-trigger
- Ensures only one prop runs at a time to maximize impact

**`hauntedHouseSounds2025.py`** - Surround sound effects
- Runs independently from prop control (separate MQTT client)
- Plays different audio files on 5 speaker channels simultaneously
- Can interrupt sounds after a minimum play time for dynamic transitions
- Per-sensor cooldowns prevent audio spam

Both scripts use the same sensor data but orchestrate independently. This separation means:
- Props and sounds can trigger at different times
- Audio interruptions don't affect physical props
- Easy to test and tune each system separately

### The Protocol: MQTT for Everything

MQTT provides a lightweight, pub-sub messaging layer:
```
Sensor triggers → device/{MAC}/sensor → Python server processes → device/{MAC}/actuator → Prop activates
```

Benefits:
- **Decoupled**: Sensors don't know about props, props don't know about sensors
- **Observable**: Monitor all events in real-time with an MQTT client
- **Resilient**: Devices reconnect automatically if connection drops
- **Flexible**: Add new behaviors server-side without reflashing hardware

### The Experience: Multi-Channel Surround Sound

Each scare has 5 unique audio tracks that play simultaneously:
```
sound/2025/
├── 1_Speaker1.mp3  # Door sensor - Channel 1 (door speaker)
├── 1_Speaker2.mp3  # Door sensor - Channel 2 (werewolf speaker)
├── 1_Speaker3.mp3  # Door sensor - Channel 3 (bubba speaker)
├── 1_Speaker4.mp3  # Door sensor - Channel 4 (coffin speaker)
├── 1_Speaker5.mp3  # Door sensor - Channel 5 (witches speaker)
├── 2_Speaker*.mp3  # Witches sensor - all channels
├── 3_Speaker*.mp3  # Coffin sensor - all channels
... and so on
```

When a sensor triggers, visitors hear different audio from each speaker location, creating an immersive surround-sound environment that moves through the space.

## Key Features

### Smart Triggering
- **Consecutive reading detection**: Requires 2+ high sensor readings to filter false positives
- **Cross-cycle memory**: Remembers last reading between processing cycles to catch true motion
- **Queue processing**: Accumulates sensor data over 300ms intervals for reliable detection

### Cooldown Management
- **Per-prop cooldowns**: Each sensor has its own cooldown timer (typically 120 seconds)
- **Global prop lock** (hauntedHouseLoop): Only one physical prop runs at a time for maximum scare impact
- **Minimum play time** (hauntedHouseSounds): Sounds must play for 5 seconds before being interrupted

### Auto-Recovery
Wrapper scripts (`runLoop.sh`, `runSounds.sh`) automatically restart crashed processes:
```bash
./runLoop.sh      # Starts prop control with auto-restart
./runSounds.sh    # Starts audio control with auto-restart
```

## Technical Stack

- **Microcontrollers**: ESP32-C3 (WiFi-enabled, low cost, plenty of GPIO)
- **Messaging**: MQTT broker (Mosquitto) running locally
- **Server**: Python 3 with asyncio for concurrent sensor processing
- **Audio**: Multi-channel USB audio interface (Behringer UMC1820)
- **Libraries**: `paho-mqtt` for MQTT, `sounddevice`/`soundfile` for audio playback

## Lessons Learned

### What Worked Well
1. **Identical firmware** made setup and maintenance trivial
2. **MQTT's pub-sub model** made it easy to add new sensors/props without changing existing code
3. **Separate processes** for props and sounds allowed independent tuning
4. **Multi-channel audio** created a much more immersive experience than mono sound
5. **Auto-restart wrappers** kept everything running reliably on Halloween night

### What We'd Do Differently
1. **Better cable management** - with 5 props, 5 sensors, and 5 speakers, wire organization matters!
2. **More granular audio control** - per-channel volume adjustment would help balance speaker levels
3. **Web dashboard** - real-time monitoring of sensor states and prop status would be helpful for debugging
4. **Event logging** - capture all triggers to analyze visitor patterns and optimize placement

## Inspiration for Your Build

If you're building something similar:

1. **Start simple**: One sensor, one prop, MQTT in between. Prove the architecture before scaling.
2. **Use identical devices**: The consistency is worth more than specialized hardware.
3. **Test cooldowns carefully**: Too short = annoying, too long = missed scares. Tune based on visitor flow.
4. **Separate concerns**: Don't try to do everything in one script. Props, sounds, and lights can be independent.
5. **Plan for failure**: Hardware will fail. Make it easy to swap devices and restart processes.

## Project Structure

```
Halloween2025/
├── server/
│   ├── hauntedHouseLoop2025.py    # Prop actuator orchestration
│   ├── hauntedHouseSounds2025.py   # Multi-channel audio playback
│   ├── runLoop.sh                  # Auto-restart wrapper for props
│   ├── runSounds.sh                # Auto-restart wrapper for sounds
│   └── sound/
│       └── 2025/
│           ├── 1_Speaker1.mp3      # Door sensor sounds
│           ├── 2_Speaker1.mp3      # Witches sensor sounds
│           ├── 3_Speaker1.mp3      # Coffin sensor sounds
│           ├── 4_Speaker1.mp3      # Bubba sensor sounds
│           └── 6_Speaker1.mp3      # Scarecrow sensor sounds
└── firmware/
    └── (ESP32 Arduino sketches - common firmware for all devices)
```

## License

This project is provided as inspiration for your own haunted house builds. Use, modify, and adapt as you see fit. No warranty - if your pneumatic werewolf goes rogue, that's on you! 🐺

---

Built with ❤️ and way too much Halloween enthusiasm. Happy haunting! 👻

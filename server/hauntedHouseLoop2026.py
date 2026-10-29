# See README.md for setup and usage instructions
# OLD2025hauntedHouseLoop.py has last year's full set of props for reference


import asyncio
import sys
import time
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from scareVideo import ScareVideoPlayer

# Constants for device names
PROP1 = "60:55:F9:7B:63:88" # BEEGEES SENSOR

SENSOR_THRESHOLD = 0
COOLDOWN_SECONDS = 40  # Minimum time between runs for each prop
VIDEO_SCREEN = None  # Display number for the BEEGEES video (None = main display)
prop_active = False  # Track if any prop is currently running
last_run_time = {PROP1: 0}  # Track last run time for each prop

# Dictionary to store lists for each device
queues = {
    PROP1: []
}

# Define MQTT parameters
mqtt_broker = "192.168.86.2"
client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="server_props")
client.connect(mqtt_broker)

# BEEGEES video: loops until the sensor trips, then the skeleton scare (see scareVideo.py)
player = ScareVideoPlayer(screen=VIDEO_SCREEN)

# Simple logging function with timestamp
def log(message):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
    print(f"[{timestamp}] {message}")

# Function to publish MQTT events
def publish_event(topic, message):
    client.publish(topic, message)
    log(f"Published event: {message} to topic {topic}")

# Function to handle MQTT messages
def on_message(client, userdata, message, properties=None):
    device_id = message.topic.split("/")[1]  # Extract device ID from the topic
    if device_id in queues:
        queues[device_id].append(message)

# Set up MQTT subscription
for device_id in queues:
    client.subscribe(f"device/{device_id}/sensor")
client.on_message = on_message


# BEEGEES
async def process_queue_PROP1():
    global prop_active
    while True:
        await asyncio.sleep(0.3)
        if len(queues[PROP1]) >= 2:
            # Copy the queue and keep the last message for next cycle
            messages = queues[PROP1][:]
            queues[PROP1] = [messages[-1]]  # Keep last message to check consecutive across cycles

            payloads = [int(message.payload.decode()) for message in messages]  # Extract payloads as integers
            log(f"PROP1 Payloads: {payloads}  # BEEGEES")

            # Check for two consecutive payloads > SENSOR_THRESHOLD
            consecutive_high = False
            for i in range(len(payloads) - 1):
                if payloads[i] > SENSOR_THRESHOLD and payloads[i + 1] > SENSOR_THRESHOLD:
                    consecutive_high = True
                    break

            # Check cooldown and prop_active before triggering
            current_time = time.time()
            time_since_last_run = current_time - last_run_time[PROP1]
            if consecutive_high and not prop_active and time_since_last_run >= COOLDOWN_SECONDS:
                prop_active = True
                last_run_time[PROP1] = current_time
                await player.trigger_scare()
                while player.scaring:  # Wait for the scare video to finish
                    await asyncio.sleep(0.3)
                queues[PROP1] = []  # Clear all events that came in during the scare
                prop_active = False


async def main():
    await player.start()
    client.loop_start()
    asyncio.create_task(process_queue_PROP1())
    log("Haunted house loop running. Press s in the video window to test the scare.")
    try:
        await player.closed.wait()
    finally:
        client.loop_stop()
        await player.stop()
    # mpv closed or crashed: exit non-zero so runLoop.sh restarts everything
    log("Video player closed, exiting")
    sys.exit(1)


# Start the event loop
if __name__ == "__main__":
    asyncio.run(main())

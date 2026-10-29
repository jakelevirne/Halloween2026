# Setup steps:
# python3 -m venv myenv
# source myenv/bin/activate
# pip install -r requirements.txt
# python3 hauntedHouseLoop.py


import asyncio
import time
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
import random

# Constants for device names
PROP1 = "60:55:F9:7B:5F:2C" # PHONE HALL SENSOR
PROP2 = "60:55:F9:7B:82:40" # COFFIN
PROP3 = "60:55:F9:7B:5F:2C" # WEREWOLF
PROP4 = "60:55:F9:7B:82:30" # COFFIN HALL SENSOR
PROP5 = "60:55:F9:7B:60:BC" # SCARECROW
PROP6 = "60:55:F9:7B:7F:98" # PHONE  

SENSOR_THRESHOLD = 1000
fogFlipper = True

# Dictionary to store lists for each device
queues = {
    PROP1: [],
    PROP2: [],
    PROP3: [],
    PROP4: [],
    PROP5: [],
    PROP6: []
}

# Define MQTT parameters
mqtt_broker = "192.168.86.2"
client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="server")
client.connect(mqtt_broker)

# Function to publish MQTT events
def publish_event(topic, message):
    client.publish(topic, message)
    print(f"Published event: {message} to topic {topic}")

# Function to handle MQTT messages
def on_message(client, userdata, message, properties=None):
    device_id = message.topic.split("/")[1]  # Extract device ID from the topic
    if device_id in queues:
        queues[device_id].append(message)

# Set up MQTT subscription with updated topic names
for device_id in queues:
    client.subscribe(f"device/{device_id}/sensor")  # Updated topic for subscription
client.on_message = on_message

""" 

async def process_queue_PROP1():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP1]:
            payloads = [int(message.payload.decode()) for message in queues[PROP1]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP 1 Max payload is: {max_payload}")

            # if max_payload > SENSOR_THRESHOLD:
            if max_payload % 2 == 0: # if max_payload is even
                publish_event(f"device/{PROP1}/actuator", "X2")  # Publish event when the maximum threshold is exceeded
                await asyncio.sleep(4)  # Adjust the delay as needed
        queues[PROP1] = []  # Clear the list
        
  
# FOG MACHINE AND CAULDRON AIR PUMP
async def process_queue_PROP2():
    global fogFlipper
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP2]:
            payloads = [int(message.payload.decode()) for message in queues[PROP2]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP2 Max payload is: {max_payload}")

            if max_payload > SENSOR_THRESHOLD:
                if (fogFlipper):
                    publish_event(f"device/{PROP2}/actuator", "X8")  # Run the fog machine
                    fogFlipper = False
                    print("PROP2 Fog machine on")
                else:
                    fogFlipper = True
                    print("PROP2 Fog machine skipped")

                await asyncio.sleep(3)  # Delay after running the fog machine
                publish_event(f"device/{PROP6}/actuator", "A1") # Run the witch sound
                publish_event(f"device/{PROP6}/actuator", "X1") # Run the air pump
                await asyncio.sleep(2)
                publish_event(f"device/{PROP6}/actuator", "X1") # Run the air pump
                await asyncio.sleep(2)
                publish_event(f"device/{PROP6}/actuator", "X4") # Run the air pump
                await asyncio.sleep(90)  # Delay before running the fog machine again
        queues[PROP2] = []  # Clear the list
       

"""

# PHONE HALL SENSOR
async def process_queue_PROP1():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP1]:
            payloads = [int(message.payload.decode()) for message in queues[PROP1]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP1 Max payload is: {max_payload}")

            if max_payload > SENSOR_THRESHOLD:
                publish_event(f"device/{PROP6}/actuator", "A11")  # Publish event when the maximum threshold is exceeded
                await asyncio.sleep(4)  # Delay after running 
                publish_event(f"device/{PROP6}/actuator", "B8")  # Publish event when the maximum threshold is exceeded

                await asyncio.sleep(30)  # Delay after running 
        queues[PROP1] = []  # Clear the list

# WEREWOLF
async def process_queue_PROP3():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP3]:
            payloads = [int(message.payload.decode()) for message in queues[PROP3]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP3 Max payload is: {max_payload}")

            if max_payload > SENSOR_THRESHOLD:
                publish_event(f"device/{PROP3}/actuator", "X30")  # Publish event when the maximum threshold is exceeded
                await asyncio.sleep(20)  # Delay after running the werewolf
        queues[PROP3] = []  # Clear the list

# COFFIN HALL SENSOR
async def process_queue_PROP4():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP4]:
            payloads = [int(message.payload.decode()) for message in queues[PROP4]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP4 Max payload is: {max_payload}")

            if max_payload > SENSOR_THRESHOLD:
                publish_event(f"device/{PROP2}/actuator", "X20")  # Publish event when the maximum threshold is exceeded
                await asyncio.sleep(60)  # Delay after running 
        queues[PROP4] = []  # Clear the list

# PHONE
async def process_queue_PROP6():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP6]:
            payloads = [int(message.payload.decode()) for message in queues[PROP6]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP6 Max payload is: {max_payload}")

            if max_payload > SENSOR_THRESHOLD:
                publish_event(f"device/{PROP6}/actuator", "B8")  # Publish event when the maximum threshold is exceeded
                await asyncio.sleep(20)  # Delay after running the werewolf
        queues[PROP6] = []  # Clear the list


"""

async def process_queue_PROP4():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP4]:
            for message in queues[PROP4]:
                topic = message.topic
                payload = message.payload.decode()
                print(f"PROP4 Processing {PROP4} - Received from queue message: {payload} from topic {topic}")
                # Custom processing for PROP4
                await asyncio.sleep(5)
            queues[PROP4] = []


# SCARECROW
async def process_queue_PROP5():
    while True:
        await asyncio.sleep(0.3)
        if queues[PROP5]:
            payloads = [int(message.payload.decode()) for message in queues[PROP5]]  # Extract payloads as integers
            max_payload = max(payloads)  # Find the maximum payload value
            print(f"PROP5 Max payload is: {max_payload}")

            if max_payload > SENSOR_THRESHOLD:
                #async sleep for a random interval between 0 and 2 seconds
                sleep_time = random.uniform(0, 2)
                print(f"Sleeping for {sleep_time:.2f} seconds")
                await asyncio.sleep(sleep_time)
                print("Done sleeping")

                publish_event(f"device/{PROP5}/actuator", "X1")  # Publish event when the maximum threshold is exceeded
                await asyncio.sleep(20)  # Delay after running the scarecrow
        queues[PROP5] = []  # Clear the list

"""

# Define the event loop
async def event_loop():
    while True:
        # All this main loop does is print the current time every .5 seconds
        await asyncio.sleep(0.5)
        current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
        # print(current_time)


# Start the event loop
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(event_loop())
    loop.create_task(process_queue_PROP1())
    # loop.create_task(process_queue_PROP2())
    loop.create_task(process_queue_PROP3())
    loop.create_task(process_queue_PROP4())
    # loop.create_task(process_queue_PROP5())
    # loop.create_task(process_queue_PROP6())
    client.loop_start()
    loop.run_forever()

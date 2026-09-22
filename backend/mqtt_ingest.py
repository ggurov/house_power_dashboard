"""MQTT subscriber: house/power/v1 -> validate -> store + live hub.

Runs in its own thread; any exception on a message is logged, never fatal.
"""

import json
import logging
import os
import threading

import ingest

log = logging.getLogger("mqtt")


def start_mqtt(handle_reading, host=None, port=1883, topic="house/power/v1"):
    import paho.mqtt.client as mqtt

    host = host or os.environ.get("MQTT_HOST", "mosquitto")

    def on_message(_client, _ud, msg):
        try:
            handle_reading(json.loads(msg.payload.decode()))
        except Exception:  # noqa: BLE001 - one bad message must not kill ingest
            log.exception("bad MQTT message on %s", msg.topic)

    client = mqtt.Client()
    client.on_message = on_message

    def _run():
        while True:
            try:
                client.connect(host, port, keepalive=30)
                client.subscribe(topic)
                log.info("MQTT subscribed to %s @ %s:%d", topic, host, port)
                client.loop_forever(retry_first_connection=True)
            except Exception:  # noqa: BLE001 - reconnect forever
                log.exception("MQTT connection lost; retrying in 5s")
                import time

                time.sleep(5)

    t = threading.Thread(target=_run, daemon=True, name="mqtt")
    t.start()
    return t

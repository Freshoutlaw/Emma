"""MQTT home automation — publish/subscribe to a local broker (Mosquitto).

Used for smart-home control (lights, plugs, sensors). The broker address and
credentials come from settings; publishing is gated through the Guardian.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from security.guardian import Guardian


class MQTTUnavailable(RuntimeError):
    pass


class MQTTClient:
    def __init__(
        self,
        guardian: Guardian,
        host: str = "localhost",
        port: int = 1883,
        user: Optional[str] = None,
        password: Optional[str] = None,
        topic_prefix: str = "emma",
        client_id: str = "emma-assistant",
    ) -> None:
        self.guardian = guardian
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.topic_prefix = topic_prefix.strip("/")
        self.client_id = client_id
        self._client: Any = None

    # ------------------------------------------------------------------ connect
    def _ensure(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover
            raise MQTTUnavailable("paho-mqtt is not installed") from exc
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        if self.user:
            client.username_pw_set(self.user, self.password)
        try:
            client.connect(self.host, self.port, keepalive=10)
        except Exception as exc:
            raise MQTTUnavailable(f"cannot reach MQTT broker at {self.host}:{self.port}") from exc
        client.loop_start()
        self._client = client
        return client

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        connected = bool(self._client is not None and getattr(self._client, "is_connected", lambda: False)())
        return {
            "configured": bool(self.host),
            "broker": f"{self.host}:{self.port}",
            "connected": connected,
            "topic_prefix": self.topic_prefix,
        }

    # ------------------------------------------------------------------ api
    def publish(self, topic: str, payload: Any, qos: int = 0, retain: bool = False) -> bool:
        self.guardian.guard("mqtt_publish", {"topic": topic, "payload": payload})
        client = self._ensure()
        full_topic = topic
        if self.topic_prefix and not topic.startswith(f"{self.topic_prefix}/"):
            full_topic = f"{self.topic_prefix}/{topic}"
        if not isinstance(payload, str):
            payload = str(payload)
        info = client.publish(full_topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5)
        return True

    def subscribe(self, topic: str, callback: Callable[[str, str], None]) -> None:
        client = self._ensure()
        full_topic = f"{self.topic_prefix}/{topic}" if self.topic_prefix else topic

        def _on_message(_client: Any, _userdata: Any, message: Any) -> None:
            callback(message.topic, message.payload.decode("utf-8", errors="replace"))

        client.on_message = _on_message
        client.subscribe(full_topic, qos=0)

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

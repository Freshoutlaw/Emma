"""MediaPipe perception — hands, face landmarks and pose detection.

Optional dependencies (`pip install 'emma-ai[vision]'`): mediapipe + opencv.
Image bytes are decoded with OpenCV and processed in a worker thread so the
event loop stays responsive.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional


class MediaPipeHandler:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._hands: Any = None
        self._face_mesh: Any = None
        self._pose: Any = None

    def available(self) -> bool:
        try:
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401

            return True
        except ImportError:
            return False

    # ---------------------------------------------------------------- decode
    @staticmethod
    def _decode(image_bytes: bytes) -> "np.ndarray":
        import cv2
        import numpy as np

        array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("could not decode image bytes")
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # ---------------------------------------------------------------- hands
    async def detect_hands(self, image_bytes: bytes) -> list[dict]:
        if not self.available():
            raise RuntimeError("mediapipe is not installed — run `pip install 'emma-ai[vision]'`")
        return await asyncio.to_thread(self._hands_sync, image_bytes)

    def _hands_sync(self, image_bytes: bytes) -> list[dict]:
        import mediapipe as mp

        if self._hands is None:
            self._hands = mp.solutions.hands.Hands(static_image_mode=True, max_num_hands=4, min_detection_confidence=0.5)
        image = self._decode(image_bytes)
        results = self._hands.process(image)
        detections = []
        if results.multi_hand_landmarks:
            for hand_landmarks, handedness in zip(results.multi_hand_landmarks, results.multi_handedness or []):
                label = handedness.classification[0].label if handedness and handedness.classification else "unknown"
                detections.append(
                    {
                        "handedness": label,
                        "score": round(handedness.classification[0].score, 3) if handedness and handedness.classification else None,
                        "landmarks": [[lm.x, lm.y, lm.z] for lm in hand_landmarks.landmark],
                    }
                )
        return detections

    # ---------------------------------------------------------------- face
    async def detect_face_landmarks(self, image_bytes: bytes) -> list[dict]:
        if not self.available():
            raise RuntimeError("mediapipe is not installed — run `pip install 'emma-ai[vision]'`")
        return await asyncio.to_thread(self._face_sync, image_bytes)

    def _face_sync(self, image_bytes: bytes) -> list[dict]:
        import mediapipe as mp

        if self._face_mesh is None:
            self._face_mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=4, min_detection_confidence=0.5)
        image = self._decode(image_bytes)
        results = self._face_mesh.process(image)
        faces = []
        if results.multi_face_landmarks:
            for landmarks in results.multi_face_landmarks:
                faces.append({"landmarks": [[lm.x, lm.y, lm.z] for lm in landmarks.landmark]})
        return faces

    # ---------------------------------------------------------------- pose
    async def detect_pose(self, image_bytes: bytes) -> list[dict]:
        if not self.available():
            raise RuntimeError("mediapipe is not installed — run `pip install 'emma-ai[vision]'`")
        return await asyncio.to_thread(self._pose_sync, image_bytes)

    def _pose_sync(self, image_bytes: bytes) -> list[dict]:
        import mediapipe as mp

        if self._pose is None:
            self._pose = mp.solutions.pose.Pose(static_image_mode=True, min_detection_confidence=0.5)
        image = self._decode(image_bytes)
        results = self._pose.process(image)
        if results.pose_landmarks:
            return [{"landmarks": [[lm.x, lm.y, lm.z] for lm in results.pose_landmarks.landmark]}]
        return []

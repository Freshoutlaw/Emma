"""Encryption helpers (Fernet) for secrets Emma must persist.

The master key comes from the `EMMA_MASTER_KEY` env var, or a generated key
persisted with 0600 permissions under `data/master.key`. Used for encrypting
credentials at rest (e.g. stored MQTT passwords, API tokens in memory dumps).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: Optional[str] = None, key_path: Optional[str | Path] = None) -> None:
        self._fernet = self._resolve(key, Path(key_path) if key_path else None)

    # ------------------------------------------------------------------ keys
    def _resolve(self, key: Optional[str], key_path: Optional[Path]) -> Fernet:
        if key:
            return Fernet(key.encode())
        if key_path and key_path.exists():
            return Fernet(key_path.read_text(encoding="utf-8").strip().encode())
        # Generate and persist a fresh key.
        generated = Fernet.generate_key()
        if key_path:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text(generated.decode(), encoding="utf-8")
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        return Fernet(generated)

    # ------------------------------------------------------------------ api
    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("decryption failed — wrong master key or corrupted token") from exc

    def encrypt_dict(self, data: dict) -> str:
        return self.encrypt(json.dumps(data, default=str))

    def decrypt_dict(self, token: str) -> dict[str, Any]:
        return json.loads(self.decrypt(token))

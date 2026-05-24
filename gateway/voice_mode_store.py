"""Voice-mode map persistence for the gateway.

Extracted from ``GatewayRunner`` so the per-chat ``/voice`` state (``off`` /
``voice_only`` / ``all``), its JSON persistence, and the validation/migration
of stored keys live in one tested place instead of being smeared as a
mutate→persist triad across the gateway's command handlers. Pure stdlib — no
gateway, no adapters.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

VALID_MODES: Set[str] = {"off", "voice_only", "all"}


class VoiceModeStore:
    """Owns the voice-mode map and its on-disk JSON representation."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._modes: Dict[str, str] = self._load()

    @staticmethod
    def voice_key(platform_value: str, chat_id: str) -> str:
        """Platform-namespaced key for a chat's voice mode."""
        return f"{platform_value}:{chat_id}"

    def _load(self) -> Dict[str, str]:
        try:
            data = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

        if not isinstance(data, dict):
            return {}

        result: Dict[str, str] = {}
        for chat_id, mode in data.items():
            if mode not in VALID_MODES:
                continue
            key = str(chat_id)
            # Skip legacy unprefixed keys — they predate platform namespacing.
            if ":" not in key:
                logger.warning(
                    "Skipping legacy unprefixed voice mode key %r during migration. "
                    "Re-enable voice mode on that chat to rebuild the prefixed key.",
                    key,
                )
                continue
            result[key] = mode
        return result

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._modes, indent=2))
        except OSError as e:
            logger.warning("Failed to save voice modes: %s", e)

    def get(self, key: str, default: str = "off") -> str:
        return self._modes.get(key, default)

    def set(self, key: str, mode: str) -> None:
        """Set a chat's voice mode and persist immediately."""
        self._modes[key] = mode
        self.save()

    @property
    def modes(self) -> Dict[str, str]:
        return dict(self._modes)

    def live_modes(self) -> Dict[str, str]:
        """The mutable backing dict. For callers that mutate in place and then
        call :meth:`save` (the gateway's ``/voice`` handlers). Prefer
        :meth:`set` for new code."""
        return self._modes

    def keys_for_modes(self, modes: Set[str], prefix: str) -> List[str]:
        """Chat ids (prefix stripped) whose mode is in ``modes`` and whose key
        starts with ``prefix``. Used to rebuild a platform adapter's auto-TTS
        opt-in / opt-out sets."""
        return [
            key[len(prefix):]
            for key, mode in self._modes.items()
            if mode in modes and key.startswith(prefix)
        ]

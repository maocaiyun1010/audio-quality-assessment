# -*- coding: utf-8 -*-
"""Factory for audio scoring model providers."""
from __future__ import annotations

import os
from typing import Callable, Optional


def current_audio_model_provider() -> str:
    """Return the selected audio scoring provider; Dify is the safe default."""
    p = (os.environ.get("SPEAKER_LLM_PROVIDER") or os.environ.get("AUDIO_MODEL_PROVIDER") or "").strip().lower()
    if p in {"seedpace", "study-ai-gateway", "study_ai_gateway"}:
        return "seedpace"
    return "dify"


def create_audio_model_client(log: Optional[Callable[[str], None]] = None):
    """Create a client exposing the DifyClient-compatible audio scoring methods."""
    if current_audio_model_provider() == "seedpace":
        from seedpace_audio_client import SeedpaceAudioClient

        return SeedpaceAudioClient(log=log)

    from difyclient import DifyClient

    return DifyClient(log=log)

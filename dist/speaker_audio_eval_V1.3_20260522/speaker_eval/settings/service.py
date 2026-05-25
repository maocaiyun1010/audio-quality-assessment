# -*- coding: utf-8 -*-
"""本地 HTTP 服务监听配置。"""
from __future__ import annotations

import os

SERVICE_HOST: str = os.environ.get("SPEAKER_AI_HOST", "127.0.0.1")
SERVICE_PORT: int = int(os.environ.get("SPEAKER_AI_PORT", "8765"))

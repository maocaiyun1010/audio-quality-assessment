# -*- coding: utf-8 -*-
"""领域与基础设施异常（单一职责：错误类型定义）。"""


class SpeakerEvalError(Exception):
    """评测流水线可捕获的根异常。"""


class ConfigurationError(SpeakerEvalError):
    """配置缺失或非法。"""


class CaptureError(SpeakerEvalError):
    """采集阶段失败（ADB、播放、录音等）。"""


class ScoringError(SpeakerEvalError):
    """Dify 评分或解析失败。"""


class ReportError(SpeakerEvalError):
    """报告生成失败。"""

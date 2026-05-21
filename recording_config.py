# -*- coding: utf-8 -*-
"""
工程录音配置唯一源。

OmniMic / PortAudio 采集并在本机落盘的 WAV 约定为：

- 采样率 **48000 Hz**（本模块 ``SAMPLE_RATE``）
- **单声道**（见 ``speaker_eval.settings.recording.RECORD_CHANNELS``，当前为 1）
- **16-bit PCM**（线性 PCM，无有损压缩；见 ``wav_capture_write.write_standard_capture_wav``）

其他模块通过 ``speaker_eval.settings.recording`` 或 ``from recording_config import SAMPLE_RATE`` 引用。
"""
SAMPLE_RATE: int = 48000

# -*- coding: utf-8 -*-
"""
双设备分步对比测评模式专用录制模块。

核心特性：
- 仅使用单个 OmniMic 麦克风，全程固定位置不动
- 分步录制：先录【被测设备A】，再录【对比设备B】
- 两次独立录制，不能同时播放、不能混音
- 强制统一标准：喇叭正对麦克风、固定15cm、同高度同角度同音量同环境
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from speaker_eval.adapters.audio import acquire_recording_buffer
from speaker_eval.adapters.audio.tooling import get_record_tool
from speaker_eval.adapters.audio.wav_capture_write import write_standard_capture_wav
from speaker_eval.settings import SAMPLE_RATE


class DualDeviceRecorder:
    """双设备分步录制器（与原有单设备/多设备录制完全隔离）。"""

    def __init__(self, log: Optional[Callable[[str], None]] = None, mic_spec: str = ""):
        self.log = log or (lambda _m: None)
        self._audio_a_path: Optional[str] = None
        self._audio_b_path: Optional[str] = None
        self._session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._mic_spec = mic_spec  # 保存麦克风规格

    @property
    def audio_a_path(self) -> Optional[str]:
        """被测设备A的音频路径。"""
        return self._audio_a_path

    @property
    def audio_b_path(self) -> Optional[str]:
        """对比设备B的音频路径。"""
        return self._audio_b_path

    @property
    def is_complete(self) -> bool:
        """两段音频是否都已录制完成。"""
        return self._audio_a_path is not None and self._audio_b_path is not None

    def record_device_a(
        self,
        duration: float = 30.0,
        gain_db: float = 0.0,
        output_dir: Optional[Path] = None,
    ) -> str:
        """
        第一步：录制【被测设备A】。

        Args:
            duration: 录制时长（秒）
            gain_db: 录音增益（dB）
            output_dir: 输出目录（默认 output/recorded）

        Returns:
            生成的 WAV 文件路径
        """
        if self._audio_a_path:
            self.log("⚠️ 被测设备A已录制，将覆盖旧文件")

        if output_dir is None:
            from config import RECORDED_DIR
            output_dir = RECORDED_DIR

        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"dual_device_A_{self._session_tag}_{timestamp}.wav"
        output_path = output_dir / filename

        self.log(f"🎙️ 开始录制【被测设备A】（{duration}秒）...")
        self.log("📏 请确保：喇叭正对麦克风、距离15cm、同高度同角度")

        # 应用麦克风配置
        if self._mic_spec:
            os.environ["SPEAKER_INPUT_DEVICE"] = self._mic_spec
            self.log(f"🎤 使用麦克风设备配置: {self._mic_spec}")

        frames = max(1, int(duration * SAMPLE_RATE))
        recording = acquire_recording_buffer(
            duration, frames, log=self.log, tool=get_record_tool(os.environ.get("SPEAKER_RECORD_TOOL"))
        )
        write_standard_capture_wav(str(output_path), recording)

        self._audio_a_path = str(output_path.resolve())
        self.log(f"✅ 【被测设备A】录制完成：{self._audio_a_path}")

        return self._audio_a_path

    def record_device_b(
        self,
        duration: float = 30.0,
        gain_db: float = 0.0,
        output_dir: Optional[Path] = None,
    ) -> str:
        """
        第二步：录制【对比设备B】。

        Args:
            duration: 录制时长（秒）
            gain_db: 录音增益（dB）
            output_dir: 输出目录（默认 output/recorded）

        Returns:
            生成的 WAV 文件路径
        """
        if not self._audio_a_path:
            raise RuntimeError("必须先录制【被测设备A】，才能录制【对比设备B】")

        if self._audio_b_path:
            self.log("⚠️ 对比设备B已录制，将覆盖旧文件")

        if output_dir is None:
            from config import RECORDED_DIR
            output_dir = RECORDED_DIR

        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"dual_device_B_{self._session_tag}_{timestamp}.wav"
        output_path = output_dir / filename

        self.log(f"🎙️ 开始录制【对比设备B】（{duration}秒）...")
        self.log("📏 请确保：麦克风位置不变、喇叭正对、距离15cm、同高度同角度同音量")

        # 应用麦克风配置
        if self._mic_spec:
            os.environ["SPEAKER_INPUT_DEVICE"] = self._mic_spec
            self.log(f"🎤 使用麦克风设备配置: {self._mic_spec}")

        frames = max(1, int(duration * SAMPLE_RATE))
        recording = acquire_recording_buffer(
            duration, frames, log=self.log, tool=get_record_tool(os.environ.get("SPEAKER_RECORD_TOOL"))
        )
        write_standard_capture_wav(str(output_path), recording)

        self._audio_b_path = str(output_path.resolve())
        self.log(f"✅ 【对比设备B】录制完成：{self._audio_b_path}")

        return self._audio_b_path

    def get_audio_paths_ordered(self) -> list[str]:
        """
        获取按顺序排列的音频路径列表（用于刺激比较评分）。

        Returns:
            [被测设备A路径, 对比设备B路径]
        """
        if not self.is_complete:
            raise RuntimeError("两段音频未全部录制完成")
        return [self._audio_a_path, self._audio_b_path]  # type: ignore

    def detach_in_memory_results_keep_wav_files(self) -> None:
        """仅清空内存中的路径引用，不删除磁盘 WAV（与导入已有清单评测配合，避免误删）。"""
        self._audio_a_path = None
        self._audio_b_path = None

    def clear_recordings(self) -> None:
        """清除已录制的音频文件（用于重新开始）。"""
        for path in [self._audio_a_path, self._audio_b_path]:
            if path and Path(path).is_file():
                try:
                    Path(path).unlink()
                    self.log(f"🗑️ 已删除：{path}")
                except Exception as e:
                    self.log(f"⚠️ 删除失败 {path}: {e}")
        self._audio_a_path = None
        self._audio_b_path = None
        self._session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")

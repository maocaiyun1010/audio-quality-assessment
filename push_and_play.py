# -*- coding: utf-8 -*-
"""
已废弃：旧版「先整批推送播放再单独录制」无法与麦克风时间对齐。

请改用 ``sync_capture.py`` 与 ``local_service.py`` 编排的多设备同步采集。
"""
from __future__ import annotations

import os
import sys
import time

import adbutils


def push_and_play(device_serial: str, audio_dir: str = "test_audio", sleep_duration: int = 30) -> None:
    """
    将本地音频推送到设备 ``/sdcard/test_audio/`` 并依次触发系统播放器。

    Args:
        device_serial: ADB 设备序列号。
        audio_dir: 本地音频目录（相对或绝对路径）。
        sleep_duration: 每条音频播放后等待的秒数（粗略控制时长）。
    """
    print(f"连接设备: {device_serial}")
    device = adbutils.adb.device(device_serial)

    device.shell("mkdir -p /sdcard/test_audio/")

    audio_files = [f for f in os.listdir(audio_dir) if f.endswith((".mp3", ".wav"))]

    print(f"找到 {len(audio_files)} 个音频文件")

    for i, file in enumerate(audio_files, 1):
        src_path = os.path.join(audio_dir, file)
        dst_path = f"/sdcard/test_audio/{file}"

        print(f"[{i}/{len(audio_files)}] 推送: {file}")
        device.push(src_path, dst_path)

    print("\n开始播放音频...\n")

    for i, file in enumerate(sorted(audio_files), 1):
        print(f"[{i}/{len(audio_files)}] 正在播放: {file}")

        if file.endswith(".mp3"):
            mime_type = "audio/mpeg"
        else:
            mime_type = "audio/wav"

        device.shell(
            "am start -a android.intent.action.VIEW "
            f"-d file:///sdcard/test_audio/{file} "
            f"-t {mime_type}"
        )

        time.sleep(sleep_duration)

        device.shell("am force-stop com.android.music")
        device.shell("am force-stop com.google.android.music")
        time.sleep(1)

    print("\n所有音频播放完成！")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python push_and_play.py <设备序列号> [播放时长]")
        print("示例: python push_and_play.py ABC123XYZ 30")
        sys.exit(1)

    serial = sys.argv[1]
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    push_and_play(serial, sleep_duration=duration)

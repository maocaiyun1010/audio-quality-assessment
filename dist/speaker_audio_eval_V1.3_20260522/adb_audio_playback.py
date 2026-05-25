# -*- coding: utf-8 -*-
"""
ADB 侧音频播放触发（常规采集 sync_capture 与双设备录制共用）。

- Android 10（API 29）及以上常见机型：MediaStore、Music 目录镜像、多意图回退、必要时 MEDIA_PLAY。
- MTK Android 10 另有额外补键逻辑（与历史行为兼容）。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from adbutils import AdbDevice

from config import DEVICE_REMOTE_DIR


def clean_shell_text(text: object) -> str:
    if text is None:
        return ""
    return str(text).replace("\r", "").strip()


def mime_for_filename(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".m4a") or lower.endswith(".aac"):
        return "audio/mp4"
    return "audio/*"


def extract_media_id(content_query_output: str) -> str:
    txt = clean_shell_text(content_query_output)
    if not txt:
        return ""
    import re

    m = re.search(r"_id=(\d+)", txt)
    return m.group(1) if m else ""


def _is_android10_family(release: str, sdk: str) -> bool:
    if clean_shell_text(sdk) == "29":
        return True
    rel = clean_shell_text(release)
    return bool(rel.startswith("10"))


def _is_mtk_platform(platform: str) -> bool:
    return "mt" in clean_shell_text(platform).lower()


def trigger_android_audio_playback(
    device: AdbDevice,
    remote_basename: str,
    log: Callable[[str], None],
    *,
    device_remote_dir: Optional[str] = None,
    skip_device_banner_log: bool = False,
) -> tuple[bool, str]:
    """
    多策略触发本地文件外放（file:// 相对 DEVICE_REMOTE_DIR）。

    ``skip_device_banner_log``：与常规采集同线程已打印设备信息时可设为 True，避免日志重复。
    """
    remote_dir = device_remote_dir if device_remote_dir is not None else DEVICE_REMOTE_DIR
    remote = f"{remote_dir}/{remote_basename}"
    uri_path = f"file://{remote}"
    mime = mime_for_filename(remote_basename)
    release = clean_shell_text(device.shell("getprop ro.build.version.release", timeout=5))
    sdk = clean_shell_text(device.shell("getprop ro.build.version.sdk", timeout=5))
    platform = clean_shell_text(device.shell("getprop ro.board.platform", timeout=5))
    brand = clean_shell_text(device.shell("getprop ro.product.brand", timeout=5))
    model = clean_shell_text(device.shell("getprop ro.product.model", timeout=5))

    if not skip_device_banner_log:
        log(
            f"📱 设备信息: brand={brand or '?'} model={model or '?'} "
            f"android={release or '?'} sdk={sdk or '?'} platform={platform or '?'}"
        )

    android10 = _is_android10_family(release, sdk)
    mtk_a10 = _is_mtk_platform(platform) and android10

    pre_check = clean_shell_text(
        device.shell(
            f"if [ -f '{remote}' ]; then echo '__FILE_OK__'; ls -l '{remote}'; else echo '__FILE_MISSING__'; fi",
            timeout=10,
        )
    )
    log(f"🔎 播放前文件检查: {pre_check or '<empty>'}")
    if "__FILE_MISSING__" in pre_check:
        return False, f"播放前设备端文件缺失: {remote}"

    try:
        scan_out = clean_shell_text(
            device.shell(
                f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d '{uri_path}'",
                timeout=10,
            )
        )
        log(f"📡 媒体库扫描回执: {scan_out or '<empty>'}")
    except Exception as exc:
        log(f"⚠️ 媒体库扫描异常（忽略继续）: {exc}")

    media_uri = ""
    music_remote = f"/sdcard/Music/{remote_basename}"
    music_uri = f"file://{music_remote}"
    try:
        # Android 10 常见兼容：复制到 Music 再查 MediaStore（常规模式与双设备模式共用）
        if android10:
            try:
                device.shell("mkdir -p /sdcard/Music", timeout=10)
                device.shell(f"cp '{remote}' '{music_remote}'", timeout=10)
                log(f"🎯 Android10 兼容：已镜像到 {music_remote}")
            except Exception as exc:
                log(f"⚠️ 复制到 /sdcard/Music 失败（继续回退）: {exc}")
        query_cmd = (
            "content query "
            "--uri content://media/external/audio/media "
            "--projection _id "
            f"--where \"_data='{remote}'\""
        )
        query_out = clean_shell_text(device.shell(query_cmd, timeout=12))
        media_id = extract_media_id(query_out)
        if media_id:
            media_uri = f"content://media/external/audio/media/{media_id}"
            log(f"🧭 MediaStore 命中: _id={media_id} uri={media_uri}")
        else:
            if android10:
                query_cmd_music = (
                    "content query "
                    "--uri content://media/external/audio/media "
                    "--projection _id "
                    f"--where \"_data='{music_remote}'\""
                )
                query_out_music = clean_shell_text(device.shell(query_cmd_music, timeout=12))
                media_id_music = extract_media_id(query_out_music)
                if media_id_music:
                    media_uri = f"content://media/external/audio/media/{media_id_music}"
                    log(f"🧭 MediaStore 命中(Music): _id={media_id_music} uri={media_uri}")
                else:
                    log(
                        f"🧭 MediaStore 未命中（原路径/ Music 路径均失败，将回退 file://）: "
                        f"{query_out_music or query_out or '<empty>'}"
                    )
            else:
                log(f"🧭 MediaStore 未命中（将回退 file://）: {query_out or '<empty>'}")
    except Exception as exc:
        log(f"⚠️ MediaStore 查询异常（将回退 file://）: {exc}")

    play_cmds: list[tuple[str, str] | None] = [
        (
            "COMPONENT+MediaPlaybackActivity+Music目录",
            "am start -W "
            "-n com.android.music/com.android.music.MediaPlaybackActivity "
            "-a android.intent.action.VIEW "
            f"-d '{music_uri}' -t {mime}",
        )
        if android10
        else None,
        (
            "COMPONENT+MediaPlaybackActivity+原目录",
            "am start -W "
            "-n com.android.music/com.android.music.MediaPlaybackActivity "
            "-a android.intent.action.VIEW "
            f"-d '{uri_path}' -t {mime}",
        )
        if android10
        else None,
        ("VIEW+MediaStore", f"am start -W -a android.intent.action.VIEW -d '{media_uri}' -t {mime}")
        if media_uri
        else None,
        ("VIEW+Music目录", f"am start -W -a android.intent.action.VIEW -d '{music_uri}' -t {mime}")
        if android10
        else None,
        ("VIEW+精准MIME", f"am start -W -a android.intent.action.VIEW -d '{uri_path}' -t {mime}"),
        ("VIEW+通配MIME", f"am start -W -a android.intent.action.VIEW -d '{uri_path}' -t audio/*"),
        ("VIEW+SENDTO", f"am start -W -a android.intent.action.SENDTO -d '{uri_path}'"),
    ]

    for item in play_cmds:
        if item is None:
            continue
        name, cmd = item
        try:
            log(f"🎵 播放尝试[{name}]: {cmd}")
            out = clean_shell_text(device.shell(cmd, timeout=20))
            log(f"📨 播放回执[{name}]: {out or '<empty>'}")
            low = out.lower()
            if "error:" in low or "exception" in low:
                continue
            if "status: ok" in low or "starting: intent" in low or "activity:" in low:
                if android10:
                    try:
                        time.sleep(0.2)
                        device.shell("input keyevent 126", timeout=5)
                        tag = "MTK Android10" if mtk_a10 else "Android10"
                        log(f"▶ {tag}：触发成功后补发 MEDIA_PLAY")
                    except Exception as exc:
                        log(f"⚠️ Android10 补发 MEDIA_PLAY 异常: {exc}")
                if "audiopreview" in low:
                    try:
                        time.sleep(0.25)
                        device.shell("input keyevent 126", timeout=5)
                        log("▶ 已发送 MEDIA_PLAY，尝试从预览页进入实际播放")
                        if android10:
                            device.shell("input keyevent 126", timeout=5)
                            log("▶ Android10 兼容补发 MEDIA_PLAY")
                    except Exception as exc:
                        log(f"⚠️ 发送 MEDIA_PLAY 异常: {exc}")
                return True, f"播放触发成功({name})"
        except Exception as exc:
            log(f"⚠️ 播放尝试异常[{name}]: {exc}")
    return False, "设备播放器未接管音频（多策略均失败）"


def wake_and_trigger_playback(
    device: AdbDevice,
    remote_basename: str,
    log: Callable[[str], None],
    *,
    device_remote_dir: Optional[str] = None,
    wakeup: bool = True,
) -> tuple[bool, str]:
    """唤醒屏幕并调用 ``trigger_android_audio_playback``（与双设备录制侧一致）。"""
    if wakeup:
        try:
            device.shell("input keyevent KEYCODE_WAKEUP", timeout=5)
            device.shell("input keyevent KEYCODE_MENU", timeout=5)
        except Exception as exc:
            log(f"⚠️ 唤醒/菜单键异常（忽略继续）: {exc}")
    return trigger_android_audio_playback(
        device, remote_basename, log, device_remote_dir=device_remote_dir
    )

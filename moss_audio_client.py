# -*- coding: utf-8 -*-
"""MOSS-Audio local scoring client.

This client mirrors the small subset of ``DifyClient`` used by ``scoring.py``.
It targets a local OpenAI-compatible MOSS-Audio/SGLang server and sends audio as
``messages[].content[].audio_url`` local file paths. It does not base64-encode
audio and does not require a file upload endpoint.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlparse

import requests

from audio_llm_normalize import write_normalized_wav_for_upload
from difyclient import (
    _build_stimulus_compare_query,
    _chat_request_timeout,
    _DEFAULT_CHAT_READ,
    _pretty_json_text_for_display,
    dify_upload_max_audio_seconds,
    first_balanced_json_object_slice,
)


DEFAULT_MOSS_API_URL = "http://localhost:30000/v1/chat/completions"
DEFAULT_MOSS_MODEL = "default"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 0.95
DEFAULT_MAX_TOKENS = 4096
REQUEST_ATTEMPTS = 2
REQUEST_RETRY_INTERVAL_SEC = 2.0


def _float_env(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def normalize_moss_api_url(raw: str) -> str:
    u = (raw or "").strip() or DEFAULT_MOSS_API_URL
    if u.endswith("/"):
        u = u.rstrip("/")
    if u.endswith("/v1"):
        return f"{u}/chat/completions"
    return u


def moss_models_url(chat_url: str) -> str:
    u = normalize_moss_api_url(chat_url)
    suffix = "/chat/completions"
    if u.endswith(suffix):
        return f"{u[: -len(suffix)]}/models"
    return u


def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wsl_available() -> bool:
    try:
        r = subprocess.run(
            ["wsl", "-e", "true"],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _wsl_primary_ip() -> Optional[str]:
    try:
        r = subprocess.run(
            ["wsl", "hostname", "-I"],
            capture_output=True,
            timeout=8,
            check=False,
        )
        if r.returncode != 0:
            return None
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        for token in out.strip().split():
            if token and token != "127.0.0.1":
                return token
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def moss_probe_hosts(port: int = 30000) -> list[str]:
    """Candidate hosts for MOSS HTTP probe (localhost + optional WSL IP)."""
    hosts = ["127.0.0.1", "localhost"]
    wsl_ip = _wsl_primary_ip()
    if wsl_ip and wsl_ip not in hosts:
        hosts.append(wsl_ip)
    extra = (os.environ.get("MOSS_AUDIO_PROBE_HOSTS") or "").strip()
    for part in extra.replace(";", ",").split(","):
        h = part.strip()
        if h and h not in hosts:
            hosts.append(h)
    _ = port
    return hosts


def discover_moss_api_url(
    preferred: str = "",
    *,
    port: int = 30000,
    api_key: str = "",
) -> Optional[str]:
    """Return first reachable ``.../v1/chat/completions`` URL, or None."""
    if preferred:
        chk = check_moss_server(preferred, api_key=api_key, try_discover_hosts=False)
        if chk.get("ok"):
            return normalize_moss_api_url(preferred)
    for host in moss_probe_hosts(port):
        url = f"http://{host}:{port}/v1/chat/completions"
        chk = check_moss_server(url, api_key=api_key, try_discover_hosts=False)
        if chk.get("ok"):
            return url
    return None


def check_moss_server(
    api_url: str = "",
    *,
    api_key: str = "",
    try_discover_hosts: bool = True,
) -> dict[str, Any]:
    """
    Probe MOSS/SGLang OpenAI-compatible endpoint.

    Returns dict with keys: ``ok``, ``message``, ``api_url``, ``models_url``,
    ``hints``, ``port_listening``, ``wsl_available``, ``suggested_url``.
    """
    chat_url = normalize_moss_api_url(api_url or os.environ.get("MOSS_AUDIO_API_URL") or "")
    models_url = moss_models_url(chat_url)
    parsed = urlparse(chat_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 30000
    headers = {"Content-Type": "application/json"}
    if (api_key or os.environ.get("MOSS_AUDIO_API_KEY") or "").strip():
        headers["Authorization"] = f"Bearer {(api_key or os.environ.get('MOSS_AUDIO_API_KEY') or '').strip()}"

    hints: list[str] = []
    port_listening = _port_open(host, port)
    wsl_ok = _wsl_available()

    if not port_listening and host in ("localhost", "127.0.0.1"):
        hints.append(f"本机 {host}:{port} 无进程监听（WinError 10061 即连接被拒绝）。")
        if not wsl_ok:
            hints.append(
                "未检测到可用 WSL：MOSS-Audio-8B 需在 Linux+GPU 环境用 SGLang 启动；"
                "请安装 WSL2 + NVIDIA 驱动，或在另一台已部署 MOSS 的机器上填写其 IP。"
            )
        else:
            wsl_ip = _wsl_primary_ip()
            if wsl_ip:
                hints.append(
                    f"已检测到 WSL（IP {wsl_ip}）。若服务在 WSL 内启动，请把 MOSS_AUDIO_API_URL 改为 "
                    f"http://{wsl_ip}:{port}/v1/chat/completions 并在 WSL 中执行 sglang serve。"
                )
            hints.append(
                "在 WSL 终端执行：sglang serve --model-path ~/weights/MOSS-Audio-8B-Thinking "
                "--trust-remote-code --host 0.0.0.0 --port 30000"
            )
        hints.append("部署说明见项目根目录 MOSS_本地部署说明.md。")
        hints.append("若暂不部署 MOSS，侧栏请改选 Dify 或 Seedpace Gateway。")

    timeout = _float_env("MOSS_AUDIO_HEALTH_TIMEOUT_SEC", 5.0)
    urls_to_try = [models_url]
    if try_discover_hosts:
        for h in moss_probe_hosts(port):
            u = f"http://{h}:{port}/v1/models"
            if u not in urls_to_try:
                urls_to_try.append(u)

    last_exc = ""
    for probe_url in urls_to_try:
        try:
            resp = requests.get(probe_url, headers=headers, timeout=timeout)
            if resp.status_code < 400:
                suggested = chat_url
                if probe_url != models_url:
                    base = probe_url[: -len("/models")]
                    suggested = f"{base}/chat/completions"
                return {
                    "ok": True,
                    "message": f"MOSS 服务可用（HTTP {resp.status_code}）",
                    "api_url": chat_url,
                    "models_url": probe_url,
                    "hints": hints,
                    "port_listening": port_listening or True,
                    "wsl_available": wsl_ok,
                    "suggested_url": suggested if suggested != chat_url else None,
                }
            last_exc = f"HTTP {resp.status_code}: {(resp.text or '')[:300]}"
        except requests.RequestException as exc:
            last_exc = f"{type(exc).__name__}: {exc}"

    suggested_url: Optional[str] = None
    if try_discover_hosts:
        suggested_url = discover_moss_api_url(chat_url, port=port, api_key=api_key)

    msg = (
        f"MOSS-Audio 本地服务不可用：{last_exc}\n"
        f"当前 MOSS_AUDIO_API_URL={chat_url!r}"
    )
    if suggested_url:
        msg += f"\n探测到可用地址：{suggested_url!r}（请写入侧栏 MOSS_AUDIO_API_URL）"
    return {
        "ok": False,
        "message": msg,
        "api_url": chat_url,
        "models_url": models_url,
        "hints": hints,
        "port_listening": port_listening,
        "wsl_available": wsl_ok,
        "suggested_url": suggested_url,
    }


def format_moss_deploy_help(probe: Optional[dict[str, Any]] = None) -> str:
    """Human-readable deployment checklist for UI/errors."""
    lines = [
        "【MOSS 本地服务未就绪】本项目只负责调用 HTTP 接口，不会自动下载模型或启动 SGLang。",
        "1) 在 Linux/WSL2 + NVIDIA GPU 环境安装 MOSS 专用 SGLang 并下载权重；",
        "2) 启动：sglang serve --model-path <权重目录>/MOSS-Audio-8B-Thinking --trust-remote-code --host 0.0.0.0 --port 30000；",
        "3) 浏览器或 PowerShell 访问 http://<服务IP>:30000/v1/models 有响应后再评测；",
        "4) 服务在 WSL 时，MOSS_AUDIO_API_URL 用 WSL IP，不要用 localhost。",
        "详见：MOSS_本地部署说明.md",
    ]
    if probe:
        for h in probe.get("hints") or []:
            if h not in lines:
                lines.append(h)
        if probe.get("suggested_url"):
            lines.append(f"建议 URL：{probe['suggested_url']}")
    return "\n".join(lines)


def moss_model_name(explicit: Optional[str] = None) -> str:
    """Use local server model by default; avoid leaking Dify/Seedpace UI names."""
    if _env_truthy("MOSS_AUDIO_USE_SELECTED_MODEL") and (explicit or "").strip():
        return (explicit or "").strip()
    return (
        (os.environ.get("MOSS_AUDIO_MODEL") or "").strip()
        or (os.environ.get("MOSS_MODEL") or "").strip()
        or DEFAULT_MOSS_MODEL
    )


class MossAudioClient:
    """OpenAI-compatible MOSS-Audio client exposing DifyClient-style methods."""

    def __init__(self, log: Optional[Callable[[str], None]] = None) -> None:
        self.api_url = normalize_moss_api_url(os.environ.get("MOSS_AUDIO_API_URL") or "")
        self.api_key = (os.environ.get("MOSS_AUDIO_API_KEY") or "").strip()
        self._user_log = log
        self._temp_dirs: list[Path] = []
        atexit.register(self.cleanup)

    def cleanup(self) -> None:
        for tdir in self._temp_dirs:
            shutil.rmtree(tdir, ignore_errors=True)
        self._temp_dirs.clear()

    def _emit(self, msg: str) -> None:
        if self._user_log:
            self._user_log(msg)
        else:
            print(msg, flush=True)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _ensure_server_ready(self) -> None:
        if _env_truthy("MOSS_AUDIO_SKIP_HEALTHCHECK"):
            return
        if _env_truthy("MOSS_AUDIO_AUTO_DISCOVER"):
            found = discover_moss_api_url(self.api_url, api_key=self.api_key)
            if found and found != self.api_url:
                self._emit(f"[MOSS] 自动发现服务地址：{found!r}")
                self.api_url = found
                os.environ["MOSS_AUDIO_API_URL"] = found
        probe = check_moss_server(self.api_url, api_key=self.api_key)
        if probe.get("ok"):
            return
        raise RuntimeError(format_moss_deploy_help(probe) + f"\n\n{probe.get('message', '')}")

    @staticmethod
    def _audio_url_from_path(path: Path) -> str:
        # SGLang's MOSS-Audio guide accepts local paths in audio_url.url.
        # Keep Windows paths readable for a same-machine Windows server.
        style = (os.environ.get("MOSS_AUDIO_URL_STYLE") or "path").strip().lower()
        resolved = path.resolve()
        if style in ("file", "file_uri", "uri"):
            return resolved.as_uri()
        return resolved.as_posix()

    def _audio_entry_from_path(self, path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"音频不存在：{path}")
        tdir = Path(tempfile.mkdtemp(prefix="moss_audio_"))
        self._temp_dirs.append(tdir)
        staged = tdir / f"moss_{uuid.uuid4().hex[:16]}.wav"
        cap = dify_upload_max_audio_seconds()
        cap_label = f"上传最长 {cap:.0f}s" if cap is not None else "上传不截断"
        self._emit(f"[MOSS] {label}：正在规范化为 48kHz WAV（{cap_label}）…")
        meta = write_normalized_wav_for_upload(path, staged, max_duration_sec=cap)
        if meta.get("trimmed"):
            self._emit(
                f"[MOSS] {label}：实录约 {float(meta.get('duration_in_sec') or 0):.1f}s，"
                f"送评截断为前 {float(meta.get('duration_out_sec') or 0):.1f}s"
            )
        return {
            "type": "audio",
            "filename": path.name,
            "mime_type": "audio/wav",
            "url": self._audio_url_from_path(staged),
            "_local_path": str(staged),
        }

    @staticmethod
    def _scoring_json_from_blob(blob: str) -> Optional[str]:
        t = (blob or "").strip()
        if not t:
            return None
        if "声音响度" not in t and "人声清晰度" not in t:
            return None
        chunk = first_balanced_json_object_slice(t)
        if chunk and ("声音响度" in chunk or "人声清晰度" in chunk):
            return chunk.strip()
        return None

    @staticmethod
    def _extract_response_text(obj: Any) -> Optional[str]:
        if isinstance(obj, dict):
            choices = obj.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message") or first.get("delta")
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        if isinstance(content, str) and content.strip():
                            return content.strip()
                        if isinstance(content, list):
                            parts: list[str] = []
                            for block in content:
                                if isinstance(block, dict):
                                    tx = block.get("text") or block.get("content")
                                    if isinstance(tx, str):
                                        parts.append(tx)
                                elif isinstance(block, str):
                                    parts.append(block)
                            merged = "".join(parts).strip()
                            if merged:
                                return merged
                        reasoning = msg.get("reasoning_content")
                        if isinstance(reasoning, str) and reasoning.strip():
                            from_json = MossAudioClient._scoring_json_from_blob(reasoning)
                            if from_json:
                                return from_json
                    text = first.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
            for key in ("answer", "content", "text", "output_text"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        return None

    def _post_chat(self, query: str, files: Sequence[dict[str, Any]], *, model: Optional[str]) -> Optional[str]:
        self._ensure_server_ready()
        content: list[dict[str, Any]] = []
        for idx, entry in enumerate(files, start=1):
            url = str(entry.get("url") or "").strip()
            if not url:
                raise ValueError(f"MOSS 音频附件缺少 url：第 {idx} 个")
            content.append({"type": "audio_url", "audio_url": {"url": url}})
        content.append({"type": "text", "text": query})

        payload: dict[str, Any] = {
            "model": moss_model_name(model),
            "stream": False,
            "temperature": _float_env("MOSS_AUDIO_TEMPERATURE", DEFAULT_TEMPERATURE),
            "top_p": _float_env("MOSS_AUDIO_TOP_P", DEFAULT_TOP_P),
            "max_tokens": max(256, _int_env("MOSS_AUDIO_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
            "messages": [{"role": "user", "content": content}],
        }
        if _env_truthy("MOSS_AUDIO_SEPARATE_REASONING", True):
            payload["separate_reasoning"] = True

        timeout = _chat_request_timeout(_float_env("MOSS_AUDIO_CHAT_TIMEOUT_SEC", _DEFAULT_CHAT_READ))
        last_err = ""
        for attempt in range(REQUEST_ATTEMPTS):
            if attempt:
                self._emit(f"[MOSS] 评分请求重试第 {attempt} 次（上次：{last_err[:500]}）…")
                time.sleep(REQUEST_RETRY_INTERVAL_SEC)
            else:
                self._emit(
                    f"[MOSS] 评分请求中：url={self.api_url!r}，model={payload['model']!r}，"
                    f"音频数={len(files)}，max_tokens={payload['max_tokens']}…"
                )
            try:
                resp = requests.post(
                    self.api_url,
                    headers=self._headers(),
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=timeout,
                )
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}: {(resp.text or '')[:1200]}"
                    continue
                obj = resp.json()
                text = self._extract_response_text(obj)
                if text:
                    self._emit(f"[MOSS] 模型正文 {len(text)} 字符")
                    return text
                last_err = f"HTTP 200 但未解析出正文：{json.dumps(obj, ensure_ascii=False)[:1200]}"
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            except json.JSONDecodeError as exc:
                last_err = f"JSONDecodeError: {exc}"
        raise RuntimeError(f"MOSS-Audio 本地评分请求失败：{last_err}")

    def analyze_audio(
        self,
        audio_path: str,
        query: str = "请分析这段音频的质量和内容",
        *,
        audio_eval_prompt: Optional[str] = None,
        selected_model: Optional[str] = None,
    ) -> Optional[str]:
        del audio_eval_prompt
        path = Path(audio_path)
        entry = self._audio_entry_from_path(path, f"音频 {path.name}")
        text = self._post_chat(query, [entry], model=selected_model)
        if text:
            print(
                f"\n=== MOSS-Audio 音频分析结果 ===\n{_pretty_json_text_for_display(text)}\n======================\n"
            )
        return text

    def upload_audios_for_stimulus_compare(
        self,
        audio_paths: Sequence[str],
        device_slot_labels: Sequence[str],
    ) -> list[dict[str, Any]]:
        paths = [Path(p) for p in audio_paths]
        if len(paths) != len(device_slot_labels):
            raise ValueError("audio_paths 与 device_slot_labels 数量不一致")
        out: list[dict[str, Any]] = []
        for i, (path, label) in enumerate(zip(paths, device_slot_labels), start=1):
            self._emit(f"[MOSS] 刺激比较：准备第 {i}/{len(paths)} 路本地音频（{label}）")
            out.append(self._audio_entry_from_path(path, f"第 {i} 路音频 {path.name}"))
        return out

    def chat_stimulus_compare_with_uploaded_files(
        self,
        file_entries: Sequence[dict[str, Any]],
        *,
        stimulus_label: str,
        device_slot_labels: Sequence[str],
        extra_instruction: str = "",
        dut_attachment_index: int = 0,
        ref_attachment_index: int = 1,
        comparison_variant: str = "same_session",
        audio_eval_prompt: Optional[str] = None,
        selected_model: Optional[str] = None,
        post_upload_chat_delay_sec: Optional[float] = None,
        log_reuse_hint: bool = False,
        prompt_mode: str = "builtin",
    ) -> Optional[str]:
        del audio_eval_prompt, post_upload_chat_delay_sec, log_reuse_hint
        query = _build_stimulus_compare_query(
            extra_instruction=extra_instruction,
            stimulus_label=stimulus_label,
            device_slot_labels=device_slot_labels,
            comparison_variant=comparison_variant,
            dut_attachment_index=dut_attachment_index,
            ref_attachment_index=ref_attachment_index,
            prompt_mode=prompt_mode,
        )
        return self._post_chat(query, list(file_entries), model=selected_model)

    def analyze_audios_stimulus_compare(
        self,
        audio_paths: Sequence[str],
        device_slot_labels: Sequence[str],
        stimulus_label: str,
        extra_instruction: str = "",
        dut_attachment_index: int = 0,
        ref_attachment_index: int = 1,
        comparison_variant: str = "same_session",
        *,
        audio_eval_prompt: Optional[str] = None,
        selected_model: Optional[str] = None,
        post_upload_chat_delay_sec: Optional[float] = None,
    ) -> Optional[str]:
        entries = self.upload_audios_for_stimulus_compare(audio_paths, device_slot_labels)
        return self.chat_stimulus_compare_with_uploaded_files(
            entries,
            stimulus_label=stimulus_label,
            device_slot_labels=device_slot_labels,
            extra_instruction=extra_instruction,
            dut_attachment_index=dut_attachment_index,
            ref_attachment_index=ref_attachment_index,
            comparison_variant=comparison_variant,
            audio_eval_prompt=audio_eval_prompt,
            selected_model=selected_model,
            post_upload_chat_delay_sec=post_upload_chat_delay_sec,
        )

# -*- coding: utf-8 -*-
"""Seedpace Gateway audio scoring client.

This client intentionally mirrors the small subset of ``DifyClient`` used by
``scoring.py`` so the caller can switch providers without changing scoring
logic. Dify remains the default provider.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

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


DEFAULT_SEEDPACE_API_URL = (
    "https://study-ai-gateway.seedpace.com/pre-gen-text/v1/chat/completions"
)
DEFAULT_SEEDPACE_MODEL = "gemini-3.1-pro-preview"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_P = 0.3
# gemini-3.1-pro-preview 常先消耗 reasoning_tokens；1024 易 finish_reason=length 且 content=null
DEFAULT_MAX_TOKENS = 8192
REQUEST_ATTEMPTS = 3
REQUEST_RETRY_INTERVAL_SEC = 3.0
_MODEL_ALIASES = {
    "gemini 3.1 pro preview": DEFAULT_SEEDPACE_MODEL,
    "gemini-3.1-pro-preview": DEFAULT_SEEDPACE_MODEL,
    "gemini 2.5 pro": "gemini-2.5-pro",
    "gemini-2.5-pro": "gemini-2.5-pro",
}


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


def normalize_seedpace_api_key(raw: str) -> str:
    """Strip accidental ``Authorization: Bearer `` prefix from UI paste."""
    k = (raw or "").strip()
    if not k:
        return ""
    low = k.lower()
    if low.startswith("bearer "):
        k = k[7:].strip()
    if low.startswith("authorization:"):
        rest = k.split(":", 1)[-1].strip()
        if rest.lower().startswith("bearer "):
            rest = rest[7:].strip()
        k = rest
    return k


def normalize_seedpace_api_url(raw: str) -> str:
    u = (raw or "").strip() or DEFAULT_SEEDPACE_API_URL
    if "/pre-gen-text/" not in u and u.endswith("/v1/chat/completions"):
        u = u.replace("/v1/chat/completions", "/pre-gen-text/v1/chat/completions")
    return u.rstrip("/")


def seedpace_model_name(explicit: Optional[str] = None) -> str:
    name = (
        (explicit or "").strip()
        or (os.environ.get("SEEDPACE_MODEL") or "").strip()
        or (os.environ.get("SPEAKER_EVAL_MODEL_NAME") or "").strip()
        or DEFAULT_SEEDPACE_MODEL
    )
    return _MODEL_ALIASES.get(name.lower(), name)


class SeedpaceAudioClient:
    """OpenAI-style chat/completions client with Dify-style audio ``files``."""

    def __init__(self, log: Optional[Callable[[str], None]] = None) -> None:
        self.api_url = normalize_seedpace_api_url(os.environ.get("SEEDPACE_API_URL") or "")
        self.api_key = normalize_seedpace_api_key(os.environ.get("SEEDPACE_API_KEY") or "")
        self.user = (os.environ.get("DIFY_USER") or os.environ.get("SEEDPACE_USER") or "").strip()
        self._user_log = log

    def _emit(self, msg: str) -> None:
        if self._user_log:
            self._user_log(msg)
        else:
            print(msg, flush=True)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError(
                "未配置 SEEDPACE_API_KEY。请在 Web UI 填写 Seedpace API Key，"
                "或设置环境变量 SEEDPACE_API_KEY。"
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _audio_entry_from_path(self, path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"音频不存在：{path}")
        tdir = Path(tempfile.mkdtemp(prefix="seedpace_audio_"))
        staged = tdir / f"seedpace_{uuid.uuid4().hex[:16]}.wav"
        try:
            cap = dify_upload_max_audio_seconds()
            cap_label = f"上传最长 {cap:.0f}s" if cap is not None else "上传不截断"
            self._emit(f"[Seedpace] {label}：正在规范化为 48kHz WAV（{cap_label}）…")
            meta = write_normalized_wav_for_upload(path, staged, max_duration_sec=cap)
            if meta.get("trimmed"):
                self._emit(
                    f"[Seedpace] {label}：实录约 {float(meta.get('duration_in_sec') or 0):.1f}s，"
                    f"上传截断为前 {float(meta.get('duration_out_sec') or 0):.1f}s"
                )
            data_b64 = base64.b64encode(staged.read_bytes()).decode("ascii")
        finally:
            shutil.rmtree(tdir, ignore_errors=True)

        # Dify-compatible field names are kept, with base64 payload added for
        # gateways that accept Dify-style files without a separate upload step.
        return {
            "type": "audio",
            "transfer_method": "local_file",
            "filename": path.name,
            "mime_type": "audio/wav",
            "data": data_b64,
        }

    @staticmethod
    def _usage_audio_tokens(obj: dict) -> int:
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            return -1
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            try:
                return int(details.get("audio_tokens", -1))
            except (TypeError, ValueError):
                pass
        return -1

    @staticmethod
    def _format_empty_response_hint(obj: dict) -> str:
        choices = obj.get("choices") or []
        ch0 = choices[0] if choices and isinstance(choices[0], dict) else {}
        finish = str(ch0.get("finish_reason") or "")
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
        comp = usage.get("completion_tokens_details") or {}
        reasoning_n = comp.get("reasoning_tokens") if isinstance(comp, dict) else None
        parts = [f"finish_reason={finish!r}"]
        if reasoning_n is not None:
            parts.append(f"reasoning_tokens={reasoning_n}")
        if finish == "length":
            parts.append(
                "输出被 max_tokens 截断（常见：思考占满配额后 content 为 null）；"
                "请增大 SEEDPACE_MAX_TOKENS（默认已改为 8192）或缩短提示词"
            )
        audio_t = SeedpaceAudioClient._usage_audio_tokens(obj)
        if audio_t == 0 and finish:
            parts.append(
                "usage 显示 audio_tokens=0，网关可能未识别 files 附件，评分可能未真正听到录音"
            )
        return "；".join(parts)

    def _post_chat(self, query: str, files: Sequence[dict[str, Any]], *, model: Optional[str]) -> Optional[str]:
        base_max = max(512, _int_env("SEEDPACE_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        payload: dict[str, Any] = {
            "model": seedpace_model_name(model),
            "stream": False,
            "temperature": _float_env("SEEDPACE_TEMPERATURE", DEFAULT_TEMPERATURE),
            "top_p": _float_env("SEEDPACE_TOP_P", DEFAULT_TOP_P),
            "max_tokens": base_max,
            "messages": [{"role": "user", "content": query}],
            "files": list(files),
        }
        if self.user:
            payload["user"] = self.user

        last_err = ""
        timeout = _chat_request_timeout(_float_env("SEEDPACE_CHAT_TIMEOUT_SEC", _DEFAULT_CHAT_READ))
        for attempt in range(REQUEST_ATTEMPTS):
            if attempt:
                if "length" in last_err or "max_tokens" in last_err:
                    payload["max_tokens"] = min(16384, int(payload["max_tokens"]) * 2)
                    self._emit(
                        f"[Seedpace] 上次输出被截断，重试并将 max_tokens 提至 {payload['max_tokens']}…"
                    )
                self._emit(f"[Seedpace] 评分请求重试第 {attempt} 次（上次：{last_err[:500]}）…")
                time.sleep(REQUEST_RETRY_INTERVAL_SEC)
            else:
                self._emit(
                    f"[Seedpace] 评分请求中：model={payload['model']!r}，"
                    f"附件数={len(files)}，max_tokens={payload['max_tokens']}…"
                )
            try:
                resp = requests.post(
                    self.api_url,
                    headers=self._headers(),
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=timeout,
                )
                if resp.status_code != 200:
                    body = (resp.text or "")[:1200]
                    last_err = f"HTTP {resp.status_code}: {body}"
                    if resp.status_code == 404:
                        last_err += (
                            f"（请核对 SEEDPACE_API_URL={self.api_url!r} 须含 /pre-gen-text/；"
                            f"model={payload['model']!r} 须为网关支持的 ID，如 gemini-3.1-pro-preview）"
                        )
                    continue
                obj = resp.json()
                if isinstance(obj, dict) and files:
                    _at = self._usage_audio_tokens(obj)
                    if _at == 0:
                        self._emit(
                            "[Seedpace] 警告：响应 usage.audio_tokens=0，网关可能未消费音频附件；"
                            "若评分不准请向 Seedpace 确认 files 多模态格式。"
                        )
                text = self._extract_response_text(obj)
                if text:
                    self._emit(f"[Seedpace] 模型正文 {len(text)} 字符")
                    return text
                hint = self._format_empty_response_hint(obj) if isinstance(obj, dict) else ""
                last_err = f"HTTP 200 但未解析出正文（{hint}）"
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            except json.JSONDecodeError as exc:
                last_err = f"JSONDecodeError: {exc}"
        raise RuntimeError(f"Seedpace 评分请求失败：{last_err}")

    @staticmethod
    def _scoring_json_from_blob(blob: str) -> Optional[str]:
        """从长文本（含 reasoning）中抽出含五维键的 JSON 对象字符串。"""
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
                            from_json = SeedpaceAudioClient._scoring_json_from_blob(reasoning)
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
                f"\n=== Seedpace 音频分析结果 ===\n{_pretty_json_text_for_display(text)}\n======================\n"
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
            self._emit(f"[Seedpace] 刺激比较：准备第 {i}/{len(paths)} 路附件（{label}）")
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

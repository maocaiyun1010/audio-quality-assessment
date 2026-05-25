# -*- coding: utf-8 -*-
"""
Dify API 端点与凭证（环境变量）。

与 ``difyclient`` 相关的其它变量（本文件未逐项列出时仍可读 OS 环境）：

- ``DIFY_AUDIO_EVAL_PROMPT``：写入 ``inputs.audio_eval_prompt``（须短于平台上限，常见 **<256** 字符；超长由客户端截断）。
- ``DIFY_AUDIO_EVAL_PROMPT_MAX_LEN``：截断上限（缺省 255，对应「小于 256 字符」类校验）。
- ``DIFY_OMIT_AUDIO_EVAL_PROMPT_INPUT``：设为 1/true 时不传该字段（兼容未声明该变量的旧应用）。
- ``DIFY_SELECTED_MODEL`` / ``SPEAKER_EVAL_MODEL_NAME``：写入 ``inputs.selected_model``（Web UI 侧栏会同步后者；发往 Dify 前会经 ``difyclient.resolve_selected_model_for_dify_inputs`` 做豆包等可选别名映射，见根目录 ``web_ui_provider_model_map.json.example``）。
- ``DIFY_OMIT_SELECTED_MODEL_INPUT``：设为 1/true 时不传 ``selected_model``。
- ``SPEAKER_DISABLE_PROVIDER_MODEL_ALIAS``：设为 1/true 时不对 ``selected_model`` 做别名改写（兼容 Dify 下拉值已与上游完全一致的场景）。
- ``SPEAKER_PROVIDER_MODEL_MAP_PATH``：可选，指向自定义 JSON 映射文件路径（缺省为项目根 ``web_ui_provider_model_map.json``）。
- ``DIFY_UPLOAD_MAX_AUDIO_SECONDS``：上传 Dify 前最长保留秒数（仅取开头；本地录音不改动）。未设置默认 **60**；``0`` = 不截断。
- ``SPEAKER_LLM_PROVIDER``：音频评分接口，缺省 ``dify``；可设 ``seedpace`` 使用 Seedpace Gateway。
- ``SEEDPACE_API_KEY`` / ``SEEDPACE_API_URL`` / ``SEEDPACE_MODEL``：Seedpace Gateway 鉴权、chat/completions 地址与模型名。
"""
from __future__ import annotations

import os

DIFY_API_URL: str = os.environ.get("DIFY_API_URL", "https://dify.cvte.com/v1/chat-messages")
DIFY_FILE_UPLOAD_URL: str = os.environ.get(
    "DIFY_FILE_UPLOAD_URL", "https://dify.cvte.com/v1/files/upload"
)
DIFY_API_KEY: str = os.environ.get("DIFY_API_KEY", "")
DIFY_USER: str = os.environ.get("DIFY_USER", "")

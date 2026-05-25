# -*- coding: utf-8 -*-
"""
喇叭音效自动化评测 — 企业级包结构入口。

- ``speaker_eval.settings``：配置（环境变量 / 路径），与业务逻辑分离
- ``speaker_eval.adapters``：麦克风、ADB 等外部系统适配
- ``speaker_eval.pipelines``：采集 → 评分 → 报告编排
- ``speaker_eval.cli``：命令行统一入口

根目录 ``config.py`` 为配置薄封装；录音 API 见 ``speaker_eval.adapters.audio``。
"""

__version__ = "2.0.0"

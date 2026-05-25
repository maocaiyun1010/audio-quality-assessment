# -*- coding: utf-8 -*-
"""
供 Streamlit web_ui 以子进程方式调用 ``main_run_eval``，便于主界面「停止测试」时终止子进程。

用法: python web_ui_eval_worker.py <config.json> <result.json>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        print("用法: python web_ui_eval_worker.py <config.json> <result.json>", file=sys.stderr)
        raise SystemExit(2)

    cfg_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    root = Path(__file__).resolve().parent
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    _ll = cfg.get("live_log_path")
    if _ll:
        os.environ["SPEAKER_WEB_UI_LIVE_LOG"] = str(_ll)

    for k, v in (cfg.get("env") or {}).items():
        if v is None or v == "":
            os.environ.pop(str(k), None)
        else:
            os.environ[str(k)] = str(v)

    from web_ui_dify_model_keys import (
        apply_dify_api_key_string,
        configure_api_key_for_model,
        set_dify_api_key_baseline,
    )

    set_dify_api_key_baseline(str(cfg.get("dify_api_key_baseline") or ""))
    _m = (os.environ.get("SPEAKER_EVAL_MODEL_NAME") or "").strip()
    _resolved = (cfg.get("dify_api_key_resolved") or "").strip()
    if _resolved:
        # 父进程已按模型映射解析好 Key（与侧栏全局 Key + web_ui_dify_api_keys_by_model.json 一致）
        apply_dify_api_key_string(_resolved, model_name=_m)
    else:
        configure_api_key_for_model(_m)
    _k = (os.environ.get("DIFY_API_KEY") or "").strip()
    print(
        f"[web_ui_eval_worker] 子进程 Dify：模型={_m!r} | app_key 长度={len(_k)} | "
        f"来源={'dify_api_key_resolved' if _resolved else 'configure_api_key_for_model'}",
        flush=True,
    )

    out: dict = {"ok": False}
    try:
        from run_all import WebUiEvalPipelineError, main_run_eval

        rp, sj = main_run_eval(
            dut_serial=cfg["dut_serial"],
            ref_serial=cfg["ref_serial"],
            gain_db=float(cfg["gain_db"]),
            duration=int(cfg["duration"]),
        )
        out = {"ok": True, "report_path": rp, "score_json": sj}
    except WebUiEvalPipelineError as exc:
        out = {
            "ok": False,
            "error": str(exc),
            "session_safe": exc.session_safe,
            "pipeline_code": exc.pipeline_code,
        }
    except BaseException as exc:  # noqa: BLE001
        out = {"ok": False, "error": str(exc), "session_safe": None, "pipeline_code": None}
    finally:
        try:
            out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except Exception as write_exc:
            print(
                f"[web_ui_eval_worker] 写入结果文件失败：{write_exc}",
                file=sys.stderr,
                flush=True,
            )


if __name__ == "__main__":
    main()

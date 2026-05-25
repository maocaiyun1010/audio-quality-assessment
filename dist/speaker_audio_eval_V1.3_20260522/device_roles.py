# -*- coding: utf-8 -*-
"""
多设备运行时「被测机 / 对比机」角色：重排序列号顺序与槽位标签。

约定：重排后列表第 1 台 → 槽位 d01 = 被测(DUT)；第 2 台 → d02 = 对比(REF)；其余为参测。
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence


def labels_for_slot_count(n_slots: int) -> list[str]:
    """
    根据槽位数量生成交互展示用中文角色标签（被测 / 对比 / 参测）。

    Args:
        n_slots: 设备槽位数（通常等于连接设备数）。
    """
    if n_slots <= 0:
        return []
    out: list[str] = []
    for i in range(n_slots):
        if i == 0:
            out.append("被测机（DUT）")
        elif i == 1:
            out.append("对比机（REF）")
        else:
            out.append(f"参测机-{i + 1}")
    return out


def reorder_serials_dut_ref_indices(serials: Sequence[str], dut_1based: int, ref_1based: int) -> list[str]:
    """按 1-based 序号将被测、对比两台排到列表最前，其余保持原相对顺序。"""
    s = [str(x).strip() for x in serials if x and str(x).strip()]
    n = len(s)
    if n < 2:
        return list(s)
    di, ri = dut_1based - 1, ref_1based - 1
    if di < 0 or di >= n or ri < 0 or ri >= n or di == ri:
        return list(s)
    first, second = s[di], s[ri]
    rest = [sx for i, sx in enumerate(s) if i not in (di, ri)]
    return [first, second] + rest


def reorder_serials_by_serial(
    serials: Sequence[str],
    dut_serial: str,
    ref_serial: str,
) -> list[str]:
    """按序列号字符串将被测、对比排到最前（用于 HTTP 等无交互场景）。"""
    s = [str(x).strip() for x in serials if x and str(x).strip()]
    n = len(s)
    if n < 2:
        return list(s)
    ds, rs = dut_serial.strip(), ref_serial.strip()
    try:
        di = s.index(ds) + 1
        ri = s.index(rs) + 1
    except ValueError:
        return list(s)
    return reorder_serials_dut_ref_indices(s, di, ri)


def _default_ref_index(n: int, dut_1based: int) -> int:
    """在 1..n 中返回与 ``dut_1based`` 不同的第一个序号，作为默认对比机序号。"""
    for c in range(1, n + 1):
        if c != dut_1based:
            return c
    return 1


def _parse_index(raw: str, default: int, n: int) -> int:
    """解析用户输入的 1-based 设备序号；空串用 ``default``，非法返回 -1。"""
    raw = (raw or "").strip()
    if not raw:
        return default if 1 <= default <= n else -1
    try:
        v = int(raw)
        return v if 1 <= v <= n else -1
    except ValueError:
        return -1


def confirm_dut_ref_interactive(
    serials: list[str],
    *,
    skip: bool,
    log: Callable[[str], None],
) -> Optional[list[str]]:
    """
    多设备时交互确认被测/对比；返回重排后的序列号列表；None 表示用户取消。
    ``skip=True`` 时不读 stdin，按当前 ``serials`` 顺序作为 DUT→REF→…
    """
    if len(serials) < 2:
        return list(serials)

    if skip:
        log("")
        log("【设备角色】已跳过交互确认；顺序为：d01=被测(DUT)，d02=对比(REF)，其后为参测。")
        for i, ser in enumerate(serials, start=1):
            role = "被测(DUT)" if i == 1 else ("对比(REF)" if i == 2 else f"参测(d{i:02d})")
            log(f"  [{i}] {role:12}  {ser}")
        return list(serials)

    n = len(serials)
    log("")
    log("————————————————————————————————————————————————————————————")
    log("【被测机 / 对比机确认】")
    log("  刺激比较：槽位 d01 录音 = 被测机（DUT）；d02 = 对比参考机（REF）。")
    log("  五维分差含义：被测相对对比机（整数 -3…+3）。")
    if n > 2:
        log(f"  当前共 {n} 台：除前两台外，其余为同场参测（详见评分提示）。")
    log("")
    log("已选设备（下面请输入序号，与 ADB 枚举顺序一致）：")
    for i, ser in enumerate(serials, start=1):
        log(f"  [{i}]  {ser}")
    log("")

    while True:
        dut_raw = input(f"被测机（DUT）序号 [1-{n}]，直接回车=1: ")
        dut_i = _parse_index(dut_raw, 1, n)
        if dut_i < 1:
            log("被测机序号无效，请重新输入。")
            continue
        ref_def = _default_ref_index(n, dut_i)
        ref_raw = input(f"对比机（REF）序号 [1-{n}]，直接回车={ref_def}: ")
        ref_i = _parse_index(ref_raw, ref_def, n)
        if ref_i < 1:
            log("对比机序号无效，请重新输入。")
            continue
        if dut_i == ref_i:
            log("被测机与对比机不能为同一序号，请重新输入。")
            continue
        break

    ordered = reorder_serials_dut_ref_indices(serials, dut_i, ref_i)
    log("")
    log("即将采用的采集与评分顺序（写入清单后按此绑定槽位）：")
    for si, ser in enumerate(ordered, start=1):
        role = "被测(DUT)" if si == 1 else ("对比(REF)" if si == 2 else "参测")
        log(f"  d{si:02d}  {role:10}  {ser}")
    log("")
    while True:
        yn = input("确认并开始采集？[Y/n] ").strip().lower()
        if yn in ("n", "no", "q", "quit"):
            return None
        if yn in ("", "y", "yes"):
            return ordered
        log("请输入 Y 确认，或 n 取消。")

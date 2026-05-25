# -*- coding: utf-8 -*-
"""
将喇叭音质测评系统打包为可外发 ZIP（不含 .venv、运行产物与本地密钥）。

用法（在项目根目录）:
  python scripts/build_release_package.py
  python scripts/build_release_package.py --with-placeholders
  python scripts/build_release_package.py --out-dir dist
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 相对项目根的路径前缀，整目录跳过
EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    ".idea",
    "__pycache__",
    "dist",
    "build",
    "node_modules",
}

# 任意层级目录名匹配则跳过该目录下全部内容
EXCLUDE_DIR_GLOBS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

# 不复制到发行包的单文件（相对根或 basename 匹配）
EXCLUDE_FILES = {
    "web_ui_dify_api_keys_by_model.json",
    "web_ui_provider_model_map.json",
    ".env",
    ".env.local",
}

EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}

EXCLUDE_PREFIXES = ("debug-",)

# 用户本地状态：外发包给干净默认，由程序首次运行再生成
EXCLUDE_USER_STATE = {
    "web_ui_selected_tracks.json",
    "web_ui_selected_models.json",
}

OUTPUT_SUBDIRS = (
    "output/recorded",
    "output/analysis",
    "output/reports",
    "output/logs",
    "models/nisqa",
)

PACKAGE_DIRNAME = "喇叭音质测评系统_V1.3"


def _should_skip_file(rel: Path) -> bool:
    if rel.name in EXCLUDE_FILES:
        return True
    if rel.name in EXCLUDE_USER_STATE:
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    if any(rel.name.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    parts = rel.parts
    if "output" in parts and rel.suffix:  # 跳过 output 下已有产物文件
        return True
    if parts[:2] == ("models", "nisqa") and rel.suffix.lower() == ".tar":
        return True
    return False


def _should_skip_dir(rel: Path) -> bool:
    name = rel.name
    if name in EXCLUDE_DIR_NAMES:
        return True
    if name in EXCLUDE_DIR_GLOBS:
        return True
    if rel.parts == ("output",) or (len(rel.parts) >= 1 and rel.parts[0] == "output"):
        # output 目录本身会重建空结构，不复制旧内容
        return True
    return False


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            continue
        if any(_should_skip_dir(rel.parents[i]) for i in range(len(rel.parents))):
            continue
        if _should_skip_file(rel):
            continue
        files.append(rel)
    return files


def copy_tree(staging: Path) -> int:
    count = 0
    for rel in iter_source_files():
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
        count += 1
    return count


def ensure_output_layout(staging: Path) -> None:
    for sub in OUTPUT_SUBDIRS:
        d = staging / sub
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.txt"
        if not readme.exists():
            readme.write_text(
                "本目录用于程序运行输出，可安全清空。\n",
                encoding="utf-8",
            )


def maybe_gen_placeholders(staging: Path) -> None:
    script = staging / "tools" / "gen_placeholder_wavs.py"
    if not script.is_file():
        return
    py = sys.executable
    env = {**__import__("os").environ, "PYTHONPATH": str(staging)}
    subprocess.run(
        [py, str(script)],
        cwd=staging,
        env=env,
        check=True,
    )


def remove_pycache(staging: Path) -> None:
    for d in list(staging.rglob("__pycache__")):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def make_zip(staging: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                arc = path.relative_to(staging.parent)
                zf.write(path, arc.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description="打包喇叭音质测评系统外发 ZIP")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "dist",
        help="输出目录（默认项目根 dist/）",
    )
    parser.add_argument(
        "--with-placeholders",
        action="store_true",
        help="打包前在暂存目录生成占位试跑 WAV（曲艺/语声各若干条）",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="仅生成解压即用文件夹，不压缩 ZIP",
    )
    args = parser.parse_args()

    out_dir: Path = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    date_tag = datetime.now().strftime("%Y%m%d")
    folder_name = f"{PACKAGE_DIRNAME}_{date_tag}"
    staging = out_dir / folder_name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    n_files = copy_tree(staging)
    ensure_output_layout(staging)

    if args.with_placeholders:
        print("生成占位试跑音源 …")
        maybe_gen_placeholders(staging)

    remove_pycache(staging)

    zip_name = f"{folder_name}.zip"
    zip_path = out_dir / zip_name

    if args.no_zip:
        print(f"已生成文件夹（{n_files} 个文件）: {staging}")
    else:
        print(f"正在压缩 …")
        make_zip(staging, zip_path)
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"已生成 ZIP（{n_files} 个源文件）: {zip_path}")
        print(f"大小: {size_mb:.2f} MB")
        print(f"同步保留解压目录: {staging}")

    print()
    print("外发说明: 将 ZIP 发给对方，解压后阅读「外发使用说明.md」，双击「一键安装依赖.bat」或「启动WebUI.bat」即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Source-only archive; no private config, runs, caches, or virtual environments."""

import argparse
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TOP_FILES = {"README.md", "Design.md", "TESTING.md", "DEMO.md", "pyproject.toml",
             "requirements.txt", ".env.example", ".gitignore"}
SOURCE_DIRS = ("workflow_agent", "examples", "tests", "scripts")


def package(destination: Path) -> int:
    files = [PROJECT / name for name in sorted(TOP_FILES) if (PROJECT / name).is_file()]
    for directory in SOURCE_DIRS:
        for path in sorted((PROJECT / directory).rglob("*")):
            relative = path.relative_to(PROJECT)
            if path.is_symlink() or not path.is_file():
                continue
            if any(p.startswith(".") or p == "__pycache__" for p in relative.parts):
                continue
            if path.suffix in {".py", ".md", ".txt", ".toml"}:
                files.append(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, PROJECT.name + "/" + path.relative_to(PROJECT).as_posix())
    return len(files)


def main():
    parser = argparse.ArgumentParser(description="打包源码与文档，排除密钥、运行产物和缓存")
    parser.add_argument("--name", default="code-review-workflow", help="不含扩展名的压缩包名称")
    parser.add_argument("--output-dir", default=str(PROJECT.parent))
    args = parser.parse_args()
    if not args.name.strip() or Path(args.name).name != args.name or args.name in {".", ".."}:
        parser.error("--name 必须是普通文件名，不能包含目录")
    destination = Path(args.output_dir).expanduser() / (args.name + ".zip")
    try:
        count = package(destination)
    except FileExistsError:
        parser.error("压缩包已存在，请更换名称。")
    print("已打包 %d 个文件：%s（%.1f KiB）" % (count, destination, destination.stat().st_size / 1024))


if __name__ == "__main__":
    main()

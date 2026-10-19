"""Build the offline console from explicitly fictional fixtures."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="demo-output")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / "fictional"
    target = Path(args.output_dir).resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        parser.error("Choose an empty output directory; existing work is never overwritten.")
    shutil.copytree(source, target, dirs_exist_ok=True)
    scripts = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "job-hunt-console" / "scripts"
    for name in ("score.py", "build_console.py"):
        subprocess.run([sys.executable, "-X", "utf8", str(scripts / name),
                        "--config", str(target / "config.json")], check=True)
    print(target / "out" / "投递台.html")


if __name__ == "__main__":
    main()

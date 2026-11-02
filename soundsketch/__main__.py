"""Run with python -m soundsketch. Listen on loopback only."""
import argparse

from .web import create_app


def main():
    parser = argparse.ArgumentParser(description="SoundSketch 本地听感表达原型")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port 必须在 1024–65535")
    app = create_app(args.data_dir)
    print(f"SoundSketch 本地工作台: http://127.0.0.1:{args.port}", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

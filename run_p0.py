"""P0 入口: python run_p0.py [--profile ...] [--user ... --password ...] [--no-headless] [--offline]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discovery.playwright_crawl import main  # noqa: E402

if __name__ == "__main__":
    main()

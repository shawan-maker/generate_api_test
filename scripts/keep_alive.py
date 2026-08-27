"""
keep_alive.py — Cookie 保活脚本（对齐 EcsCloud keep_alive.js）

定时访问保活页面，防止 accessToken 过期。

功能：
  * 定时访问（默认 15 分钟）
  * 单实例锁（避免重复运行）
  * 崩溃自愈（浏览器关闭后自动重连）
  * 看门狗（长时间未执行保活则强制退出）
  * 状态文件（便于外部监控）

用法：
  python scripts/keep_alive.py --project ecm-compute
  python scripts/keep_alive.py --project ecm-compute --interval 10
"""
import os
import sys
import json
import time
import signal
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("keep_alive")


def parse_args():
    import argparse
    ap = argparse.ArgumentParser(description="Cookie 保活脚本")
    ap.add_argument("--project", required=True, help="项目 ID (projects/<id>/)")
    ap.add_argument("--interval", type=int, default=15, help="保活间隔（分钟，默认 15）")
    return ap.parse_args()


def load_profile(project_dir: Path) -> dict:
    try:
        import yaml
    except Exception:
        return {}
    p = project_dir / "profile.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


class KeepAliveLock:
    """单实例锁：防止重复运行。"""

    def __init__(self, lock_file: Path):
        self.lock_file = lock_file
        self.acquired = False

    def acquire(self) -> bool:
        """获取锁，返回是否成功。"""
        if self.lock_file.exists():
            try:
                pid = int(self.lock_file.read_text(encoding="utf-8").strip())
                # 检查进程是否存活
                os.kill(pid, 0)
                log.warning(f"已有保活进程在运行 (PID {pid})，本实例自动退出")
                return False
            except (OSError, ValueError):
                # 进程已死，删除残留锁文件
                try:
                    self.lock_file.unlink()
                except Exception:
                    pass

        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            self.lock_file.write_text(str(os.getpid()), encoding="utf-8")
            self.acquired = True
            # 注册退出清理
            signal.signal(signal.SIGTERM, self._cleanup)
            signal.signal(signal.SIGINT, self._cleanup)
            return True
        except Exception as e:
            log.error(f"获取锁失败: {e}")
            return False

    def release(self):
        """释放锁。"""
        if self.acquired:
            try:
                self.lock_file.unlink()
                self.acquired = False
            except Exception:
                pass

    def _cleanup(self, signum, frame):
        """信号处理：清理锁文件后退出。"""
        self.release()
        sys.exit(0)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()


class KeepAlive:
    """Cookie 保活管理器。"""

    def __init__(self, project_dir: Path, interval_minutes: int = 15):
        self.project_dir = project_dir
        self.interval_minutes = interval_minutes
        self.interval_ms = interval_minutes * 60 * 1000

        # 加载配置
        self.profile = load_profile(project_dir)
        self.base_url = self.profile.get("base_url", "https://10.151.37.249")
        self.probe_url = self.profile.get("probe_url") or self.profile.get("login_url")

        # 状态文件
        self.log_dir = Path(__file__).resolve().parents[1] / "output" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.status_file = self.log_dir / "keep_alive.status"
        self.lock_file = self.log_dir / "keep_alive.lock"

        # 浏览器实例
        self.browser = None
        self.context = None
        self.page = None

        # 统计
        self.count = 0
        self.last_run_ts = time.time()

    def write_status(self, status: str):
        """写入状态文件。"""
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.status_file.write_text(f"{status} @ {ts}\n", encoding="utf-8")
        except Exception:
            pass

    async def init_browser(self):
        """初始化浏览器。"""
        if self.browser is None:
            from playwright.async_api import async_playwright
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(
                headless=True,
                args=["--ignore-certificate-errors", "--no-sandbox"]
            )
            log.info("浏览器已启动")

    async def close_browser(self):
        """关闭浏览器。"""
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
            self.context = None
            self.page = None
            log.info("浏览器已关闭")

    async def keep_alive_once(self) -> str:
        """执行一次保活，返回状态。"""
        from lib.auth import AuthSession

        try:
            # 确保浏览器已初始化
            await self.init_browser()

            # 创建 context
            if self.context is None:
                self.context = await self.browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 1920, "height": 1080}
                )
                # 加载 cookies
                cookie_file = self.project_dir / "output" / "config" / "cookies.json"
                if cookie_file.exists():
                    cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                    await self.context.add_cookies(cookies)
                    log.info(f"已加载 {len(cookies)} 个 cookies")

            # 创建 page
            if self.page is None:
                self.page = await self.context.new_page()

            self.count += 1
            log.info(f"[#{self.count}] 保活访问中...")

            # 访问保活页面
            response = await self.page.goto(self.probe_url, wait_until="domcontentloaded", timeout=30000)
            await self.page.wait_for_timeout(3000)

            # 检查状态
            url = self.page.url
            status_code = response.status if response else 0

            # 判断是否过期
            is_login = "login" in url.lower() or "auth" in url.lower()
            is_forbidden = status_code in (401, 403)

            if is_login or is_forbidden:
                log.warning(f"cookie 可能已过期 (status={status_code}, url={url[:60]})")
                status = "EXPIRED"
            else:
                log.info(f"✅ 保活成功 - {url[:60]}")
                status = "OK"

            self.last_run_ts = time.time()
            self.write_status(status)
            return status

        except Exception as e:
            log.error(f"❌ 保活异常: {e}")
            # 浏览器可能崩溃，关闭后下次重连
            await self.close_browser()
            self.write_status("ERROR")
            return "ERROR"

    async def run(self):
        """主循环：定时保活。"""
        log.info(f"🔄 Cookie 保活服务已启动")
        log.info(f"📡 目标: {self.probe_url}")
        log.info(f"⏰ 间隔: 每 {self.interval_minutes} 分钟一次")
        log.info(f"🛡️  锁文件: {self.lock_file}")

        try:
            while True:
                await self.keep_alive_once()
                log.info(f"⏳ 下次保活将在 {self.interval_minutes} 分钟后执行")

                # 看门狗：检查是否卡死
                if time.time() - self.last_run_ts > self.interval_ms / 1000 + 600:
                    log.error("⚠️ 看门狗触发：长时间未执行保活，强制退出")
                    sys.exit(1)

                # 等待下一次
                await asyncio.sleep(self.interval_minutes * 60)

        except KeyboardInterrupt:
            log.info("收到中断信号，正在退出...")
        finally:
            await self.close_browser()
            if hasattr(self, "pw"):
                await self.pw.stop()


async def main():
    args = parse_args()
    project_dir = ROOT / "projects" / args.project

    if not project_dir.exists():
        log.error(f"❌ 项目目录不存在: {project_dir}")
        sys.exit(1)

    ka = KeepAlive(project_dir, args.interval)

    with KeepAliveLock(ka.lock_file) as acquired:
        if not acquired:
            sys.exit(0)
        await ka.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

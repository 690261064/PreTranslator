# -*- coding: utf-8 -*-
"""DLX(DeepLX) 服务托管: 静默后台启动、健康检查、端口自适应、退出清理。

注意: 2026-10 起 DeepL 风控拉黑了 utls iOS TLS 指纹, 官方版 DLX 会持续 429,
必须使用已删除 SetTLSFingerprintIOS 的修复版 (见仓库根目录 DLX修复说明.md)。
"""
import os
import socket
import subprocess
import time

from . import netcheck

CREATE_NO_WINDOW = 0x08000000


def port_free(port):
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class DlxInstance:
    def __init__(self, exe, port, proxy=None, log_dir=None):
        self.exe = exe
        self.port = port
        self.proxy = proxy
        self.log_dir = log_dir or os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'logs')
        self.proc = None
        self.reused = False
        self.rate_limited = False

    @property
    def url(self):
        return 'http://127.0.0.1:%d/translate' % self.port

    @property
    def describe(self):
        tag = '代理通道(%s)' % self.proxy if self.proxy else '直连通道'
        return '%s 端口%d' % (tag, self.port)

    def healthy(self, timeout=15):
        """服务可用性: 上游限速(429)时服务本身仍算可用, 只是不能立刻翻译。"""
        ok, detail = netcheck.dlx_translate(self.url, timeout=timeout)
        if ok:
            self.rate_limited = False
            return True
        self.rate_limited = bool(detail) and ('429' in detail or '限速' in detail)
        return self.rate_limited

    def spawn(self):
        os.makedirs(self.log_dir, exist_ok=True)
        logf = open(os.path.join(self.log_dir, 'dlx-%d.log' % self.port), 'ab')
        args = [self.exe, '-p', str(self.port)]
        if self.proxy:
            args += ['-proxy', self.proxy]
        try:
            self.proc = subprocess.Popen(
                args, stdout=logf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
                cwd=os.path.dirname(self.exe) or None)
        except OSError:
            return False
        return True

    def stop(self):
        if self.reused or self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None


def ensure_dlx(exe, preferred_port, proxy=None, log_fn=print, attempts=10,
               strict=False):
    """复用或启动一个健康 DLX 实例。失败返回 None。

    strict=True 时只尝试 preferred_port 这一个端口 — 供多通道端口规划使用,
    防止某通道健康检查偶发失败后"爬端口"抢走相邻通道的实例, 使多个通道
    合并到同一个 DLX (同一出口IP, 限速额度共享, 429 风暴的元凶之一)。
    """
    ports = [preferred_port] if strict else [preferred_port + i
                                             for i in range(attempts)]
    for port in ports:
        inst = DlxInstance(exe, port, proxy=proxy)
        if inst.healthy(timeout=8):
            inst.reused = True
            log_fn('%s: 端口 %d 已有可用 DLX, 直接复用' % (inst.describe, port))
            return inst
        if not port_free(port):
            log_fn('%s: 端口 %d 被其他程序占用%s'
                   % (inst.describe, port, ', 本通道跳过' if strict else ', 换下一个端口'))
            continue
        if not inst.spawn():
            log_fn('%s: 启动失败 (程序不可执行?)' % inst.describe)
            return None
        ok = False
        for _ in range(30):
            if inst.healthy(timeout=6):
                ok = True
                break
            if inst.proc.poll() is not None:
                break
            time.sleep(0.5)
        if ok:
            log_fn('%s: 启动成功' % inst.describe)
            return inst
        log_fn('%s: 启动后未通过健康检查, 换下一个端口' % inst.describe)
        inst.stop()
    return None


DEFAULT_EXE_CANDIDATES = [
    'deeplx-fixed.exe',  # 相对路径按工具目录解析 (与 exe 同目录即用)
]


def find_exe(cfg_path=''):
    import os as _os
    cands = []
    if cfg_path:
        cands.append(cfg_path)
    tool_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for c in DEFAULT_EXE_CANDIDATES:
        if not os.path.isabs(c):
            cands.append(os.path.join(tool_dir, c))
        else:
            cands.append(c)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None

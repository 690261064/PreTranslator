# -*- coding: utf-8 -*-
"""批量处理目录下全部 Unity 游戏: 装 XUAT -> 预翻译 -> 校验。运行: python batch_run.py [根目录]"""
import os
import shutil
import subprocess
import sys
import time
import traceback

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL_DIR)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pt_core import config, games, runlog
import pretranslator as pt

# 用法: python batch_run.py <游戏根目录>  (缺省用当前目录)
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
# 内置的 XUAT 安装器与工具放同一目录; 也可在 config.json 的 xuat_setup_exe 指定
SETUP = os.path.join(TOOL_DIR, 'SetupReiPatcherAndAutoTranslator.exe')
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'batch_run.log')
LOG = open(LOG_PATH, 'a', encoding='utf-8')
LOG.write('\n==== 批次启动 %s ====\n' % time.strftime('%m-%d %H:%M:%S'))


def log(msg, lv='info'):
    line = '[%s] %s' % (lv, msg)
    print(line, flush=True)
    LOG.write(line + '\n')
    LOG.flush()


def install_standalone(g):
    dst = os.path.join(g.root, os.path.basename(SETUP))
    if not os.path.isfile(dst):
        shutil.copy2(SETUP, dst)
    proc = subprocess.Popen([dst], cwd=g.root, creationflags=0x08000000,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(1)
    log('%s: 独立版安装器已运行 (退出码忽略, 以产物为准)' % g.name)


def main():
    cfg = config.load()
    gs = games.scan([ROOT], log_fn=lambda m: None, time_budget=120)
    ignored = {os.path.normcase(os.path.normpath(x))
               for x in (cfg.get('ignored_games') or [])}
    gs = [g for g in gs
          if os.path.normcase(os.path.normpath(g.root)) not in ignored]
    log('根目录 %s 下发现 %d 个游戏 (忽略 %d 个)'
        % (ROOT, len(gs), len(ignored)))
    results = []
    for g in gs:
        try:
            # 完成标记: 上次成功校验过则跳过 (接力续跑时避免重复工作)
            if runlog.is_done(g, cfg['target_lang']):
                log('%s: 已有完成标记, 跳过' % g.name)
                results.append((g.name, 'OK(已完成)'))
                continue
            if g.kind == 'unity':
                if g.il2cpp:
                    log('%s: IL2CPP 组件安装...' % g.name)
                    ok = games.install_il2cpp(g, cfg, log_fn=log)
                    if not ok:
                        results.append((g.name, 'FAIL: IL2CPP 组件安装失败'))
                        continue
                else:
                    log('%s: 安装独立版XUAT...' % g.name)
                    install_standalone(g)
            g2 = games.detect_at(g.root)
            if g2:
                g = g2
            if g.kind == 'unity':
                log('%s: 安装后仍无法识别, 跳过' % g.name, 'warn')
                results.append((g.name, 'SKIP'))
                continue
            log('%s [%s]: 开始预翻译' % (g.name, g.kind_label))
            s = pt.run_pipeline(cfg, g, log_fn=log)
            log('%s: 完成 翻译%d句 写出%d对 校验一致%d/不一致%d'
                % (g.name, s['engine'].get('done_now', 0),
                   s.get('write', {}).get('pre_pairs', 0),
                   s.get('verify', {}).get('ok', 0),
                   s.get('verify', {}).get('bad', 0)))
            # 完成标记仅在"本轮无遗留句子且校验全一致"时写入 (runlog 公共判定,
            # 与 GUI 批量一致), 只校验一致性会把翻了一部分的游戏误标为已完成
            ok_m = runlog.write_marker(g, cfg['target_lang'], s, log_fn=log)
            results.append((g.name, 'OK' if ok_m else 'OK(有遗留句, 未标记完成)'))
        except Exception as e:
            log('%s: 失败 %s' % (g.name, e), 'err')
            log(traceback.format_exc()[-400:], 'err')
            results.append((g.name, 'FAIL: %s' % str(e)[:90]))
    log('==== 汇总 (%d) ====' % len(results))
    for n, r in results:
        log('  %-36s %s' % (n[:36], r))
    LOG.close()


if __name__ == '__main__':
    main()

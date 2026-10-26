# -*- coding: utf-8 -*-
"""工具配置持久化 (config.json, 与主程序同目录)。"""
import json
import os

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(TOOL_DIR, 'config.json')

DEFAULTS = {
    'proxy_url': 'http://127.0.0.1:7897',  # 逗号/分号分隔可填多个, 每个代理一条DLX通道
    'deepl_keys': '',  # DeepL官方免费API密钥 (逗号分隔多个), 每号50万字符/月
    'dlx_path': '',
    'dlx_port': 1188,
    'dlx_proxy_port': 1189,
    'target_lang': 'zh-CN',
    'dlx_source_lang': 'JA',
    'dlx_target_lang': 'ZH',
    'batch_cap': 1400,
    'binary_extract_cap': 20000,
    'override_font': 'Microsoft YaHei',
    # 字体缺字修复: True=自动给 TMP 图集补中文字形
    'font_tmp_patch': True,
    # 'bake' 静态 SDF 烘焙 (默认, 最稳) / 'dynamic' 运行时动态加字 (需实测)
    'font_patch_mode': 'bake',
    # 指定烘焙用的字体文件 (.ttf/.ttc); 空=按候选表自动挑
    'font_bake_file': '',
    # 字体补丁不可用时, 把译文缺字替换为游戏字体覆盖的日式写法
    'font_safe_substitute': True,
    'enable_glossary': True,
    'glossary_max_terms': 300,
    'glossary_file': '',
    'xuat_setup_exe': '',
    'scan_roots': [],
    'ignored_games': [],  # 用户手动移除的游戏根目录 (自带中文等), 不再扫描
    'games': {},
}


def load():
    cfg = dict(DEFAULTS)
    cfg['games'] = {}
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if k == 'games' and isinstance(v, dict):
                    cfg['games'] = v
                elif k in DEFAULTS:
                    cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print('配置保存失败: %s' % e)

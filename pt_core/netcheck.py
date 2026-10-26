# -*- coding: utf-8 -*-
"""网络检测: 直连/代理连通性、出口IP、DLX 健康检查。仅用标准库 urllib。"""
import json
import urllib.error
import urllib.request

DEEPL_HOME = 'https://www.deepl.com/'
DEEPL_API = 'https://api-free.deepl.com/v2'
IP_ECHO = 'https://api.ipify.org/?format=json'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')


def _opener(proxy=None):
    # proxy=None 表示强制直连(绕过系统/环境代理); 传地址则走该代理
    if proxy is None:
        handler = urllib.request.ProxyHandler({})
    else:
        handler = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
    return urllib.request.build_opener(handler)


def http_get(url, proxy=None, timeout=6):
    op = _opener(proxy)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with op.open(req, timeout=timeout) as r:
        return r.status, r.read(4096).decode('utf-8', 'replace')


def check_deepl(proxy=None, timeout=6):
    """检测 DeepL 站点是否可达。收到任何 HTTP 状态码(含403/429防护)都算网络可达;
    只有连接层失败(超时/DNS/拒绝)才算不可达。返回 (ok, 说明)。"""
    try:
        status, _ = http_get(DEEPL_HOME, proxy=proxy, timeout=timeout)
        return True, 'HTTP %d' % status
    except urllib.error.HTTPError as e:
        return True, 'HTTP %d (站点防护, 网络通)' % e.code
    except Exception as e:
        return False, str(e)[:140]


def get_usage(key, proxy=None, timeout=10):
    """查官方API当月用量。返回 (used, limit); 失败返回 None。"""
    req = urllib.request.Request(DEEPL_API + '/usage',
                                 headers={'Authorization': 'DeepL-Auth-Key ' + key})
    try:
        with _opener(proxy).open(req, timeout=timeout) as r:
            o = json.loads(r.read().decode('utf-8'))
        return (int(o.get('character_count', 0)), int(o.get('character_limit', 0)))
    except Exception:
        return None


def check_deepl_key(key, proxy=None, timeout=10):
    """校验 DeepL 官方免费API密钥 (格式 xxxxx...:fx), 返回 (ok, 说明)。
    ok 时说明里带当月额度用量。走 /v2/usage 接口。"""
    q = get_usage(key, proxy=proxy, timeout=timeout)
    if q is None:
        # 区分"密钥无效"与"网络失败": 再试一次拿原始错误
        try:
            req = urllib.request.Request(
                DEEPL_API + '/usage',
                headers={'Authorization': 'DeepL-Auth-Key ' + key})
            with _opener(proxy).open(req, timeout=timeout) as r:
                pass
            return False, '响应异常'
        except urllib.error.HTTPError as e:
            if e.code == 403:
                return False, '密钥无效 (403)'
            return False, 'HTTP%d' % e.code
        except Exception as e:
            return False, str(e)[:140]
    return True, '有效, 本月额度已用 %d/%d 字符' % q


def exit_ip(proxy=None, timeout=8):
    try:
        _, body = http_get(IP_ECHO, proxy=proxy, timeout=timeout)
        return json.loads(body).get('ip')
    except Exception:
        return None


def dlx_translate(url, text='テスト', source='JA', target='ZH', timeout=20):
    """向 DLX 发一条真实短句, 验证服务与上游均可用。返回 (ok, 译文或错误)。"""
    body = json.dumps({'text': [text], 'source_lang': source,
                       'target_lang': target}).encode('utf-8')
    req = urllib.request.Request(url, data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            o = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(200).decode('utf-8', 'replace')
        except Exception:
            detail = ''
        return False, 'HTTP%d %s' % (e.code, detail[:80])
    except Exception as e:
        return False, str(e)[:140]
    if o.get('code') == 200:
        data = o.get('data')
        if isinstance(data, list) and data:
            return True, str(data[0])[:60]
        return True, str(data)[:60]
    if o.get('code') == 429:
        return False, '上游限速(429)'
    return False, str(o)[:120]

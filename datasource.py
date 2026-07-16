#!/usr/bin/env python3
"""数据源基类、注册表、共享网络工具"""

import json
from urllib.request import urlopen, Request, ProxyHandler, build_opener, install_opener
from urllib.parse import urlencode, quote


# ===== 网络工具 =====

_proxy_handler = None


def set_proxy(proxy_url):
    global _proxy_handler
    if proxy_url:
        _proxy_handler = build_opener(ProxyHandler({
            'http': proxy_url, 'https': proxy_url}))
        install_opener(_proxy_handler)


def _http_get_json(url):
    try:
        req = Request(url, headers={"User-Agent": "Game-Cover-Extractor/1.0"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _http_get_bytes(url):
    try:
        req = Request(url, headers={"User-Agent": "Game-Cover-Extractor/1.0"})
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


# ===== Google 翻译 =====

def google_translate(text, google_lang_code):
    if not text or google_lang_code.startswith('en'):
        return text
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl={google_lang_code}&dt=t"
        f"&q={quote(text[:4000])}"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data and isinstance(data, list) and data[0]:
            return ''.join(seg[0] for seg in data[0] if seg[0])
    except Exception:
        pass
    return text


# ===== 数据源基类 =====

class DataSource:
    name = ""
    display_name = ""
    needs_api_key = False

    def initialize(self, api_key=None, log=print):
        return True

    def fetch_metadata(self, title, platform_id=None, platform_name="", **kwargs):
        raise NotImplementedError


# ===== 注册表 =====

DATASOURCES = {}


def register_datasource(source):
    DATASOURCES[source.name] = source


def get_datasource(name):
    return DATASOURCES.get(name)


def list_datasources():
    return list(DATASOURCES.values())


# ===== 加载数据源 =====

import datasource_thegamesdb
import datasource_wikipedia
try:
    import datasource_igdb
except ImportError:
    pass

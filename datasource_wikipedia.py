#!/usr/bin/env python3
"""Wikipedia 数据源"""

from urllib.parse import urlencode, quote
from datasource import DataSource, register_datasource, _http_get_json


def _wiki_request(lang, endpoint, params):
    base = f"https://{lang}.wikipedia.org"
    url = f"{base}/{endpoint}"
    if params:
        url += "?" + urlencode(params)
    return _http_get_json(url)


def fetch_wikipedia_metadata(title, platform_name=""):
    for lang in ('zh', 'en'):
        search_term = f"{title} {platform_name}" if lang == 'en' and platform_name else title
        params = {
            "action": "query", "list": "search", "format": "json",
            "srsearch": search_term, "srlimit": "3",
        }
        data = _wiki_request(lang, "w/api.php", params)
        if not data or "query" not in data:
            continue
        results = data["query"].get("search", [])
        if not results:
            continue
        page_title = results[0]["title"]
        summary = _wiki_request(
            lang, f"api/rest_v1/page/summary/{quote(page_title, safe='')}", None)
        if summary and summary.get("extract"):
            return {"description": summary["extract"]}
    return None


class WikipediaSource(DataSource):
    name = "wikipedia"
    display_name = "Wikipedia"
    needs_api_key = False

    def fetch_metadata(self, title, platform_id=None, platform_name="", **kwargs):
        return fetch_wikipedia_metadata(title, platform_name)


register_datasource(WikipediaSource())

#!/usr/bin/env python3
"""IGDB (Twitch) 数据源"""

import json
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from datasource import DataSource, register_datasource

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_BASE = "https://api.igdb.com/v4"

PLATFORM_MAP = {
    "Nintendo Switch": 130,
    "Nintendo DS": 20,
}


def _igdb_get_token(client_id, client_secret):
    params = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode()
    try:
        req = Request(TWITCH_TOKEN_URL, data=params, method="POST")
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("access_token")
    except Exception:
        return None


def _igdb_post(endpoint, body, client_id, token):
    url = f"{IGDB_BASE}/{endpoint}"
    req = Request(url, data=body.encode(), method="POST", headers={
        "Client-ID": client_id,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _fetch_genre_names(genre_ids, client_id, token):
    if not genre_ids:
        return []
    ids_str = ",".join(str(gid) for gid in genre_ids)
    data = _igdb_post("genres", f"fields name; where id = ({ids_str}); limit 50;",
                       client_id, token)
    if not data:
        return []
    return [g["name"] for g in data if "name" in g]


def _fetch_publisher_names(ic_ids, client_id, token):
    if not ic_ids:
        return []
    ids_str = ",".join(str(cid) for cid in ic_ids)
    data = _igdb_post("involved_companies",
                       f"fields company.name,publisher; where id = ({ids_str}); limit 50;",
                       client_id, token)
    if not data:
        return []
    return [item["company"]["name"] for item in data
            if item.get("publisher") and isinstance(item.get("company"), dict)
            and item["company"].get("name")]


def _fetch_video_url(video_ids, client_id, token):
    if not video_ids:
        return None
    data = _igdb_post("game_videos",
                       f"fields video_id; where id = ({video_ids[0]}); limit 1;",
                       client_id, token)
    if data and data[0].get("video_id"):
        return f"https://www.youtube.com/watch?v={data[0]['video_id']}"
    return None


def fetch_igdb_metadata(title, client_id, token, platform_name=""):
    igdb_platform = PLATFORM_MAP.get(platform_name)
    where = f" where platforms = ({igdb_platform});" if igdb_platform else ""
    body = (f'search "{title}";'
            f" fields name,summary,first_release_date,total_rating,"
            f"genres,involved_companies,videos;"
            f"{where} limit 5;")
    data = _igdb_post("games", body, client_id, token)
    if not data:
        return None

    game = data[0]
    result = {}

    if game.get("summary"):
        result["description"] = game["summary"]
    if game.get("first_release_date"):
        from datetime import datetime, timezone
        ts = game["first_release_date"]
        result["release"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    if game.get("total_rating"):
        result["rating"] = f"{round(game['total_rating'])}%"

    genres = _fetch_genre_names(game.get("genres", []), client_id, token)
    if genres:
        result["genres"] = ", ".join(genres)

    pubs = _fetch_publisher_names(game.get("involved_companies", []), client_id, token)
    if pubs:
        result["publisher_online"] = ", ".join(pubs)

    yt = _fetch_video_url(game.get("videos", []), client_id, token)
    if yt:
        result["youtube"] = yt

    return result if result else None


class IGDBSource(DataSource):
    name = "igdb"
    display_name = "IGDB (Twitch)"
    needs_api_key = True

    def __init__(self):
        self._client_id = None
        self._token = None

    def initialize(self, api_key=None, log=print):
        if not api_key or ':' not in api_key:
            log("[错误] IGDB 需要 Twitch API 凭据，格式: client_id:client_secret")
            return False
        client_id, client_secret = api_key.split(':', 1)
        log("正在获取 IGDB/Twitch OAuth 令牌...")
        token = _igdb_get_token(client_id, client_secret)
        if not token:
            log("[错误] 无法获取 Twitch OAuth 令牌，请检查凭据")
            return False
        self._client_id = client_id
        self._token = token
        log("  IGDB 认证成功")
        return True

    def fetch_metadata(self, title, platform_id=None, platform_name="", **kwargs):
        if not self._token:
            return None
        return fetch_igdb_metadata(title, self._client_id, self._token, platform_name)


register_datasource(IGDBSource())

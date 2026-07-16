#!/usr/bin/env python3
"""TheGamesDB 数据源"""

from urllib.parse import urlencode
from datasource import DataSource, register_datasource, _http_get_json, _http_get_bytes


TGDB_BASE = "https://api.thegamesdb.net/v1"


def _tgdb_request(endpoint, params):
    url = f"{TGDB_BASE}/{endpoint}?{urlencode(params)}"
    return _http_get_json(url)


def fetch_genres_map(api_key):
    data = _tgdb_request("Genres", {"apikey": api_key})
    if not data or "data" not in data:
        return {}
    genres = data["data"].get("genres", {})
    return {int(k): v.get("name", "") for k, v in genres.items()}


def fetch_publishers_map(api_key):
    data = _tgdb_request("Publishers", {"apikey": api_key})
    if not data or "data" not in data:
        return {}
    pubs = data["data"].get("publishers", {})
    return {int(k): v.get("name", "") for k, v in pubs.items()}


def fetch_game_boxart(api_key, game_id):
    params = {
        "apikey": api_key,
        "games_id": game_id,
        "filter[type]": "boxart",
    }
    data = _tgdb_request("Games/Images", params)
    if not data or "data" not in data:
        return None
    base_url = data["data"].get("base_url", {}).get("original", "")
    images = data["data"].get("images", {}).get(str(game_id), [])
    for img in images:
        if img.get("type") == "boxart" and img.get("side") == "front":
            return base_url + img["filename"] if base_url else None
    return None


def fetch_game_metadata(title, api_key, genres_map, publishers_map, platform_id,
                        include_boxart=False):
    params = {
        "apikey": api_key,
        "name": title,
        "filter[platform]": platform_id,
        "fields": "players,genres,overview,rating,publishers,youtube",
    }
    data = _tgdb_request("Games/ByGameName", params)
    if not data or "data" not in data:
        return None
    games = data["data"].get("games", [])
    if not games:
        return None
    game = games[0]
    game_id = game.get("id")
    result = {}
    if game.get("overview"):
        result["description"] = game["overview"]
    if game.get("release_date"):
        result["release"] = game["release_date"]
    if game.get("players"):
        result["players"] = str(game["players"])
    if game.get("rating"):
        result["rating"] = game["rating"]
    genre_ids = game.get("genres") or []
    if genre_ids and genres_map:
        names = [genres_map[gid] for gid in genre_ids if gid in genres_map]
        if names:
            result["genres"] = ", ".join(names)
    pub_ids = game.get("publishers") or []
    if pub_ids and publishers_map:
        names = [publishers_map[pid] for pid in pub_ids if pid in publishers_map]
        if names:
            result["publisher_online"] = ", ".join(names)
    if game.get("youtube"):
        result["youtube"] = game["youtube"]
    if include_boxart and game_id:
        boxart_url = fetch_game_boxart(api_key, game_id)
        if boxart_url:
            result["boxart_url"] = boxart_url
    return result if result else None


class TheGamesDBSource(DataSource):
    name = "thegamesdb"
    display_name = "TheGamesDB"
    needs_api_key = True

    def __init__(self):
        self._api_key = None
        self._genres_map = {}
        self._publishers_map = {}

    def initialize(self, api_key=None, log=print):
        self._api_key = api_key
        if not api_key:
            return False
        log("正在获取 TheGamesDB 元数据映射表...")
        self._genres_map = fetch_genres_map(api_key)
        self._publishers_map = fetch_publishers_map(api_key)
        if self._genres_map:
            log(f"  已加载 {len(self._genres_map)} 个类型标签")
        else:
            log("  [警告] 无法获取类型标签映射")
        return True

    def fetch_metadata(self, title, platform_id=None, platform_name="", **kwargs):
        if not self._api_key:
            return None
        return fetch_game_metadata(
            title, self._api_key, self._genres_map,
            self._publishers_map, platform_id,
            include_boxart=kwargs.get('include_boxart', False))


register_datasource(TheGamesDBSource())

#!/usr/bin/env python3
"""TheGamesDB 数据源"""

from urllib.parse import urlencode
from datasource_base import DataSource, register_datasource, _http_get_json, _http_get_bytes


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


def fetch_game_boxart(api_key, game_id, log=print):
    params = {
        "apikey": api_key,
        "games_id": game_id,
        "filter[type]": "boxart",
    }
    data = _tgdb_request("Games/Images", params)
    if not data or "data" not in data:
        log(f"[图片下载] TheGamesDB 图片接口无有效数据: game_id={game_id}")
        return None
    base_url_map = data["data"].get("base_url", {})
    base_url = (base_url_map.get("original") or base_url_map.get("large")
                or base_url_map.get("medium") or base_url_map.get("thumb") or "")
    images_dict = data["data"].get("images", {})
    images = images_dict.get(str(game_id), [])
    if not images:
        log(f"[图片下载] TheGamesDB 未返回游戏图片: game_id={game_id}, "
            f"可用keys={list(images_dict.keys())[:5]}")
    for img in images:
        if img.get("type") == "boxart" and img.get("side") == "front":
            return base_url + img["filename"] if base_url else None
    if images:
        log(f"[图片下载] TheGamesDB 返回 {len(images)} 张图片但无正面封面, "
            f"types={[(i.get('type'),i.get('side')) for i in images[:5]]}")
    return None


def fetch_game_metadata(title, api_key, genres_map, publishers_map, platform_id,
                        include_boxart=False, log=print):
    params = {
        "apikey": api_key,
        "name": title,
        "filter[platform]": platform_id,
        "fields": "players,genres,overview,rating,publishers,youtube",
    }
    data = _tgdb_request("Games/ByGameName", params)
    if not data or "data" not in data:
        log(f"[游戏搜索] TheGamesDB 搜索无结果: {title} | 平台ID: {platform_id}")
        return None
    games = data["data"].get("games", [])
    if not games:
        log(f"[游戏搜索] TheGamesDB 返回空游戏列表: {title}")
        return None
    game = games[0]
    game_id = game.get("id")
    log(f"[游戏搜索] TheGamesDB 匹配成功: "
        f"{game.get('game_title', '?')} | 游戏ID: {game_id}")
    result = {}
    if game_id:
        result["game_id"] = str(game_id)
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
        boxart_url = fetch_game_boxart(api_key, game_id, log=log)
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
        log("[元数据补全] 正在获取 TheGamesDB 元数据映射表")
        self._genres_map = fetch_genres_map(api_key)
        self._publishers_map = fetch_publishers_map(api_key)
        if self._genres_map:
            log(f"[元数据补全] 已加载 {len(self._genres_map)} 个类型标签")
        else:
            log("[元数据补全] 无法获取类型标签映射")
        return True

    def fetch_metadata(self, title, platform_id=None, platform_name="", **kwargs):
        if not self._api_key:
            return None
        log = kwargs.get('log', print)
        return fetch_game_metadata(
            title, self._api_key, self._genres_map,
            self._publishers_map, platform_id,
            include_boxart=kwargs.get('include_boxart', False),
            log=log)


register_datasource(TheGamesDBSource())

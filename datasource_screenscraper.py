#!/usr/bin/env python3
"""ScreenScraper 数据源 (API v2)"""

from urllib.parse import urlencode
from datasource_base import DataSource, register_datasource, _http_get_json

SS_BASE = "https://www.screenscraper.fr/api2"
SS_DEV_ID = "game_scanf"
SS_DEV_PASSWORD = "dK7x3mR9pQ2w"
SS_SOFT_NAME = "game-scanf"

PLATFORM_MAP = {
    "Nintendo DS": 15,
    "Nintendo 3DS": 17,
    "Nintendo Wii": 16,
    "PlayStation Portable": 61,
    "Nintendo Switch": 225,
    "PlayStation 1": 57,
    "Nintendo GameCube": 13,
    "Sega Dreamcast": 23,
    "Game Boy Advance": 12,
}

REGION_PRIORITY = ['wor', 'us', 'eu', 'jp', 'ss']
LANG_PRIORITY = ['en', 'zh', 'fr']


def _pick_text(arr, key='text', region_key='region', use_region=True):
    if not arr or not isinstance(arr, list):
        return ''
    if use_region:
        for pref in REGION_PRIORITY:
            for item in arr:
                if isinstance(item, dict) and item.get(region_key) == pref and item.get(key):
                    return item[key]
    for item in arr:
        if isinstance(item, dict) and item.get(key):
            return item[key]
    return ''


def _pick_lang_text(arr, key='text', lang_key='langue'):
    if not arr or not isinstance(arr, list):
        return ''
    for pref in LANG_PRIORITY:
        for item in arr:
            if isinstance(item, dict) and item.get(lang_key) == pref and item.get(key):
                return item[key]
    for item in arr:
        if isinstance(item, dict) and item.get(key):
            return item[key]
    return ''


def _pick_media_url(medias, media_type, region_priority=None):
    if not medias or not isinstance(medias, list):
        return None
    if isinstance(media_type, str):
        media_type = [media_type]
    if region_priority is None:
        region_priority = REGION_PRIORITY
    for mt in media_type:
        for pref in region_priority:
            for m in medias:
                if m.get('type') == mt and m.get('region') == pref and m.get('url'):
                    return m['url']
        for m in medias:
            if m.get('type') == mt and m.get('url'):
                return m['url']
    return None


def _build_auth_params(ss_user, ss_password):
    params = {
        'devid': SS_DEV_ID,
        'devpassword': SS_DEV_PASSWORD,
        'softname': SS_SOFT_NAME,
        'output': 'json',
    }
    if ss_user:
        params['ssid'] = ss_user
    if ss_password:
        params['sspassword'] = ss_password
    return params


def _search_game(title, system_id, ss_user, ss_password):
    params = _build_auth_params(ss_user, ss_password)
    params['recherche'] = title
    if system_id:
        params['systemeid'] = system_id
    url = f"{SS_BASE}/jeuRecherche.php?{urlencode(params)}"
    return _http_get_json(url)


def _get_game_info(game_id, system_id, ss_user, ss_password):
    params = _build_auth_params(ss_user, ss_password)
    params['jeuid'] = game_id
    if system_id:
        params['systemeid'] = system_id
    url = f"{SS_BASE}/jeuInfos.php?{urlencode(params)}"
    return _http_get_json(url)


def _get_game_by_name(rom_name, system_id, ss_user, ss_password):
    params = _build_auth_params(ss_user, ss_password)
    params['romnom'] = rom_name
    if system_id:
        params['systemeid'] = system_id
    url = f"{SS_BASE}/jeuInfos.php?{urlencode(params)}"
    return _http_get_json(url)


def _parse_game_data(jeu, include_boxart=False):
    result = {}

    jeu_id = jeu.get('id')
    if jeu_id:
        result['game_id'] = str(jeu_id)

    noms = jeu.get('noms', [])
    title = _pick_text(noms, key='text', region_key='region')
    if title:
        result['title_ss'] = title

    synopsis = jeu.get('synopsis', [])
    desc = _pick_lang_text(synopsis)
    if desc:
        result['description'] = desc

    dates = jeu.get('dates', [])
    release = _pick_text(dates, key='text', region_key='region')
    if release:
        result['release'] = release

    editeur = jeu.get('editeur', {})
    if isinstance(editeur, dict) and editeur.get('text'):
        result['publisher_online'] = editeur['text']

    developpeur = jeu.get('developpeur', {})
    if isinstance(developpeur, dict) and developpeur.get('text'):
        result['developer_ss'] = developpeur['text']

    genres = jeu.get('genres', [])
    if isinstance(genres, list):
        genre_names = []
        for g in genres:
            if isinstance(g, dict):
                noms = g.get('noms', [])
                name = _pick_lang_text(noms)
                if name:
                    genre_names.append(name)
        if genre_names:
            result['genres'] = ', '.join(genre_names)

    joueurs = jeu.get('joueurs', {})
    if isinstance(joueurs, dict) and joueurs.get('text'):
        result['players'] = joueurs['text']

    note = jeu.get('note', {})
    if isinstance(note, dict) and note.get('text'):
        try:
            rating_val = float(note['text'])
            result['rating'] = f"{rating_val}/20"
        except (ValueError, TypeError):
            pass

    medias = jeu.get('medias', [])
    if include_boxart:
        box_url = _pick_media_url(medias, ['box-2D', 'box-3D'])
        if box_url:
            result['boxart_url'] = box_url

    for mt in ['video-normalized', 'video']:
        for m in medias:
            if m.get('type') == mt and m.get('url'):
                result['youtube'] = m['url']
                break
        if 'youtube' in result:
            break

    return result if result else None


def fetch_screenscraper_metadata(title, ss_user, ss_password,
                                 platform_name="", include_boxart=False,
                                 log=print):
    system_id = PLATFORM_MAP.get(platform_name)

    data = _search_game(title, system_id, ss_user, ss_password)
    jeu = None
    if data and isinstance(data, dict):
        header = data.get('header', {})
        if header.get('success') == 'true':
            resp = data.get('response', {})
            jeux = resp.get('jeux', [])
            if jeux and isinstance(jeux, list):
                jeu = jeux[0]
                game_id = jeu.get('id')
                if game_id:
                    detail = _get_game_info(game_id, system_id,
                                            ss_user, ss_password)
                    if detail and isinstance(detail, dict):
                        detail_header = detail.get('header', {})
                        if detail_header.get('success') == 'true':
                            detail_jeu = detail.get('response', {}).get('jeu')
                            if detail_jeu:
                                jeu = detail_jeu

    if not jeu:
        return None

    return _parse_game_data(jeu, include_boxart=include_boxart)


class ScreenScraperSource(DataSource):
    name = "screenscraper"
    display_name = "ScreenScraper"
    needs_api_key = True

    def __init__(self):
        self._ss_user = None
        self._ss_password = None

    def initialize(self, api_key=None, log=print):
        if not api_key or ':' not in api_key:
            log("[元数据补全] ScreenScraper 凭据无效，需要 用户名:密码")
            log("[元数据补全] 注册地址: https://www.screenscraper.fr")
            return False
        self._ss_user, self._ss_password = api_key.split(':', 1)
        log(f"[元数据补全] ScreenScraper 已配置用户: {self._ss_user}")
        return True

    def fetch_metadata(self, title, platform_id=None, platform_name="",
                       **kwargs):
        return fetch_screenscraper_metadata(
            title, self._ss_user, self._ss_password,
            platform_name=platform_name,
            include_boxart=kwargs.get('include_boxart', False))


register_datasource(ScreenScraperSource())

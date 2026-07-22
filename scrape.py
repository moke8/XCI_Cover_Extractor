#!/usr/bin/env python3
"""刮削主调度：batch_scrape、元数据写入、ExtractWorker"""

import re
import threading
from pathlib import Path

from datasource_base import set_proxy, google_translate, get_datasource, _http_get_bytes


# ===== 工具函数 =====

def _has_cjk(text):
    return any('一' <= c <= '鿿' for c in text)


_TC_CHARS = set(
    '優記點開關學東與號說麗買義書實總這從進時間問題長們線還過處對電動員請種業經連結個機數產無裡發準現環樂選項認設據應體歡視頁練網幣節讓際計畫區觀覺單歷歸調變標響廣達歲當積類會島語車輕齊廳觸創職護編譯'
    '騎夢魘級瑪歐驚寶劍傳險戰鬥義務構圖館絡話輛運遊錄訊號該還結頭圍廠廢廚龍鳳鑰鍵鐵銀釘針腦臟藝術蘭藥獲獎獵猶瑣爾燈燒營獻獨狀滅漲測決減準溫視離種積穩競節篇範築縣織繼續縮繩約紀純紅納級終組細織續華萬著薩處裝製複觀詢試詳認誤課談調請論證識議護變讓議豐貝負財販貧貨質購貿費貼資賊賓賞賢賣賦質賬賴賺購贈贊趙輕載較輝輩輪轉辦辭辯達遙適選遷邊邏還進遠違連過運過適選遲遺鄰醫醬釋鑒鋼錢錯鍋鍵鏡鐘關隊階隨險隱雙雜離難電靈靜響頂預領頻題顏額願類顯飛養餘駐駕驅驗體'
)


def _is_traditional(text):
    return any(c in _TC_CHARS for c in text)


def sanitize_filename(name):
    illegal = '<>:"/\\|?*'
    for c in illegal:
        name = name.replace(c, '_')
    return name.strip().strip('.')


def backup_file(path):
    if not path.exists():
        return
    import shutil
    shutil.copy2(path, Path(str(path) + '.bak'))


# ===== Pegasus Metadata =====

def parse_pegasus_meta(meta_path):
    if not meta_path.exists():
        return [], []
    text = meta_path.read_text(encoding='utf-8')
    blocks = re.split(r'\n(?=game:)', text)
    collection_lines = []
    games = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith('game:'):
            file_match = re.search(r'^file:\s*(.+)$', block, re.MULTILINE)
            filename = file_match.group(1).strip() if file_match else None
            games.append({'lines': block, 'file': filename})
        else:
            collection_lines.append(block)
    return collection_lines, games


def build_game_entry(info, image_rel_path):
    lines = [f"game: {info['title']}"]
    lines.append(f"file: {info['filename']}")
    if info.get('game_id'):
        lines.append(f"x-id: {info['game_id']}")
    if info.get('publisher'):
        lines.append(f"developer: {info['publisher']}")
    if info.get('genres'):
        lines.append(f"genre: {info['genres']}")
    if info.get('players'):
        lines.append(f"players: {info['players']}")
    if info.get('release'):
        lines.append(f"release: {info['release']}")
    if info.get('rating'):
        lines.append(f"rating: {info['rating']}")
    if info.get('description'):
        desc = info['description'].replace('\n', ' ').replace('\r', '')
        lines.append(f"description: {desc}")
    if image_rel_path:
        lines.append(f"assets.boxFront: {image_rel_path}")
    if info.get('youtube'):
        lines.append(f"assets.video: {info['youtube']}")
    return '\n'.join(lines)


def write_pegasus_meta(meta_path, results, collection_defaults=None):
    backup_file(meta_path)
    collection_lines, existing_games = parse_pegasus_meta(meta_path)

    if not collection_lines and collection_defaults:
        d = collection_defaults
        collection_lines = [
            f"collection: {d['collection']}\n"
            f"shortname: {d['shortname']}\n"
            f"extensions: {d['extensions']}\n"
            f"launch: {{file.path}}"
        ]

    file_to_index = {}
    for i, g in enumerate(existing_games):
        if g['file']:
            file_to_index[g['file']] = i

    for info, image_rel_path in results:
        entry_text = build_game_entry(info, image_rel_path)
        fname = info['filename']
        if fname in file_to_index:
            existing_games[file_to_index[fname]]['lines'] = entry_text
        else:
            existing_games.append({'lines': entry_text, 'file': fname})

    with open(meta_path, 'w', encoding='utf-8') as f:
        for coll in collection_lines:
            f.write(coll.strip() + '\n')
        f.write('\n')
        for g in existing_games:
            f.write('\n' + g['lines'].strip() + '\n')


# ===== Anbernic gamelist.xml =====

def _xml_escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def write_gamelist_xml(gamelist_path, results):
    backup_file(gamelist_path)
    existing = {}
    if gamelist_path.exists():
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(gamelist_path)
            for game_el in tree.findall('game'):
                path_el = game_el.find('path')
                if path_el is not None and path_el.text:
                    existing[path_el.text] = game_el
        except Exception:
            pass

    import xml.etree.ElementTree as ET
    root = ET.Element('gameList')

    for game_el in existing.values():
        root.append(game_el)

    for info, image_rel_path in results:
        game_path = f"./{info['filename']}"
        if game_path in existing:
            game_el = existing[game_path]
        else:
            game_el = ET.SubElement(root, 'game')

        def _set(tag, val):
            if not val:
                return
            el = game_el.find(tag)
            if el is None:
                el = ET.SubElement(game_el, tag)
            el.text = str(val)

        _set('path', game_path)
        _set('name', info.get('title', ''))
        if info.get('game_id'):
            _set('id', info['game_id'])
        if image_rel_path:
            _set('image', f"./{image_rel_path}")
        if info.get('description'):
            _set('desc', info['description'])
        if info.get('publisher'):
            _set('developer', info['publisher'])
            _set('publisher', info['publisher'])
        if info.get('genres'):
            _set('genre', info['genres'])
        if info.get('players'):
            _set('players', info['players'])
        if info.get('release'):
            date_str = info['release'].replace('-', '') + 'T000000'
            _set('releasedate', date_str)
        if info.get('rating'):
            try:
                r = float(info['rating'].rstrip('%')) / 100.0
                _set('rating', f"{r:.2f}")
            except (ValueError, AttributeError):
                pass
        if info.get('youtube'):
            _set('video', info['youtube'])

    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(str(gamelist_path), encoding='utf-8', xml_declaration=True)


# ===== 完整度检查 =====

def get_complete_games(directory):
    from platform_base import load_showcase_games
    games, _, _ = load_showcase_games(directory)
    complete = set()
    for g in games:
        if not g.get('description'):
            continue
        cover = g.get('cover', '')
        if cover and Path(cover).exists():
            complete.add(g.get('file', ''))
    return complete


# ===== 统一刮削逻辑 =====

def batch_scrape(
    game_dir, extract_fn, file_extensions, platform_id, platform_name,
    collection_defaults, extract_kwargs=None,
    generate_meta=False, generate_gamelist=False,
    online_mode=False, api_key=None, datasource_name='thegamesdb',
    lang_code='en', google_lang='', translate=False,
    video=False, filename_as_title=False,
    thread_count=4, scrape_mode='refresh', target_files=None,
    proxy='', override_search_name='',
    log=print, cancel_event=None
):
    import shutil
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from platform_base import collect_game_files

    if proxy:
        set_proxy(proxy)

    game_files, temp_dir = collect_game_files(game_dir, file_extensions)

    if not game_files:
        log(f"[错误] 所选目录中没有找到游戏文件")
        return

    if target_files is not None:
        game_files = [(p, n) for p, n in game_files if n in target_files]
        if not game_files:
            log("[信息] 未找到指定的游戏文件")
            return

    if scrape_mode == 'complement' and target_files is None:
        complete = get_complete_games(game_dir)
        before = len(game_files)
        game_files = [(p, n) for p, n in game_files if n not in complete]
        skipped = before - len(game_files)
        if skipped:
            log(f"[补全模式] 跳过 {skipped} 个已有完整元数据的游戏")
        if not game_files:
            log("[补全模式] 所有游戏已有完整元数据，无需处理")
            return

    media_base = Path(game_dir) / 'media'
    media_base.mkdir(exist_ok=True)

    source = get_datasource(datasource_name)
    if source and online_mode and api_key:
        source.initialize(api_key, log)

    total = len(game_files)
    counter = [0]
    lock = threading.Lock()
    success = [0]
    failed = [0]
    meta_results = []

    def process_game(game_path, display_name):
        if cancel_event and cancel_event.is_set():
            return None
        with lock:
            counter[0] += 1
            idx = counter[0]
        log(f"[{idx}/{total}] {display_name}")
        try:
            info = extract_fn(str(game_path), lang_code=lang_code,
                              log=log, **(extract_kwargs or {}))
            if not info:
                return None
            info['filename'] = display_name
            if not info.get('game_id'):
                info['game_id'] = (info.pop('game_code', None)
                                   or info.pop('product_id', None) or '')
            safe_title = sanitize_filename(info.get('title_en') or info['title'])
            log(f"  [DEBUG] title='{info.get('title')}', "
                f"title_en='{info.get('title_en')}', "
                f"game_id='{info.get('game_id')}'")

            icon_data = info.pop('icon_data', None)
            img_data = None
            img_ext = '.jpg'
            image_rel_path = None

            if online_mode and source:
                search_name = override_search_name or info.get('title_en') or info['title']
                log(f"  [DEBUG] 搜索名: '{search_name}', platform_id={platform_id}")
                online = source.fetch_metadata(
                    search_name, platform_id,
                    platform_name=platform_name,
                    include_boxart=True,
                    log=log)
                if not online:
                    wiki = get_datasource('wikipedia')
                    if wiki:
                        online = wiki.fetch_metadata(
                            search_name, platform_name=platform_name)
                if online:
                    boxart_url = online.pop('boxart_url', None)
                    if boxart_url:
                        boxart_data = _http_get_bytes(boxart_url)
                        if boxart_data:
                            img_ext = Path(boxart_url).suffix or '.jpg'
                            img_data = boxart_data
                            log(f"  [封面] 已下载在线封面")
                        else:
                            log(f"  [封面] 下载失败: {boxart_url}")
                    else:
                        log(f"  [封面] 在线数据无封面URL")
                    if translate and google_lang:
                        for k in ('description', 'genres'):
                            if online.get(k):
                                online[k] = google_translate(
                                    online[k], google_lang)
                    if video:
                        if not online.get('youtube'):
                            log(f"  [视频] 未找到视频: {display_name}")
                    else:
                        online.pop('youtube', None)
                    info.update(online)
                    log(f"  [在线] 已补全元数据")
                else:
                    log(f"  [在线] 未找到匹配")

            if translate and google_lang and google_lang.startswith('zh'):
                if not _has_cjk(info['title']):
                    translated = google_translate(info['title'], google_lang)
                    if translated and translated != info['title']:
                        info['title'] = translated

            if filename_as_title:
                info['title'] = Path(display_name).stem

            if not img_data and icon_data:
                img_data = icon_data
                img_ext = '.png'
                log(f"  [封面] 使用内置图标")

            if img_data:
                game_media = media_base / safe_title
                game_media.mkdir(exist_ok=True)
                out_path = game_media / f"boxfront{img_ext}"
                with open(out_path, 'wb') as out_f:
                    out_f.write(img_data)
                image_rel_path = f"media/{safe_title}/boxfront{img_ext}"
                log(f"  [OK] -> {image_rel_path}")
            else:
                log(f"  [OK] {display_name} (无封面)")

            return (info, image_rel_path)
        except Exception as e:
            log(f"  [失败] {e}")
            return None

    try:
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {executor.submit(process_game, p, n): n
                       for p, n in game_files}
            for fut in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    log("\n[取消] 用户已取消操作")
                    break
                try:
                    result = fut.result()
                except Exception:
                    result = None
                if result:
                    meta_results.append(result)
                    success[0] += 1
                else:
                    failed[0] += 1
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if generate_meta and meta_results:
        meta_path = Path(game_dir) / 'metadata.pegasus.txt'
        write_pegasus_meta(meta_path, meta_results, collection_defaults)
        log(f"\nPegasus 元数据已写入: {meta_path}")

    if generate_gamelist and meta_results:
        gl_path = Path(game_dir) / 'gamelist.xml'
        write_gamelist_xml(gl_path, meta_results)
        log(f"Anbernic gamelist 已写入: {gl_path}")

    log(f"\n处理完成! 成功: {success[0]}, 失败: {failed[0]}")


# ===== ExtractWorker =====

try:
    from PySide6.QtCore import Signal, QThread

    class ExtractWorker(QThread):
        log_signal = Signal(str)
        finished_signal = Signal()

        def __init__(self, params):
            super().__init__()
            self.params = params
            self.cancel_event = threading.Event()

        def cancel(self):
            self.cancel_event.set()

        def run(self):
            try:
                batch_scrape(**self.params, log=self.log_signal.emit,
                             cancel_event=self.cancel_event)
            except Exception as e:
                self.log_signal.emit(f"\n[错误] {e}")
            finally:
                self.finished_signal.emit()

except ImportError:
    pass

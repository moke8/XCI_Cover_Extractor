#!/usr/bin/env python3
"""刮削主调度：batch_scrape、元数据写入、ExtractWorker"""

import re
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


def _normalized_image_suffix(image_suffix):
    suffix = str(image_suffix)
    if not suffix.startswith('.'):
        suffix = f'.{suffix}'
    return suffix.lower()


def _rom_media_name(display_name):
    return sanitize_filename(Path(display_name).stem)


def standard_cover_path(display_name, image_suffix):
    name = _rom_media_name(display_name)
    suffix = _normalized_image_suffix(image_suffix)
    return f'media/{name}/boxfront{suffix}'


def anbernic_cover_path(display_name, image_suffix):
    name = _rom_media_name(display_name)
    suffix = _normalized_image_suffix(image_suffix)
    return f'Imgs/{name}{suffix}'


def _enabled_text(value):
    return '开启' if value else '关闭'


def _safe_proxy_display(proxy):
    if not proxy:
        return '未设置'
    try:
        parsed = urlsplit(proxy)
        if not parsed.scheme or not parsed.hostname:
            return '已设置'
        host = parsed.hostname
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        if parsed.port:
            host = f'{host}:{parsed.port}'
        return urlunsplit((parsed.scheme, host, '', '', ''))
    except (TypeError, ValueError):
        return '已设置'


def _log_scrape_configuration(
    log, *, game_dir, platform_name, file_extensions,
    generate_meta, generate_gamelist, online_mode, api_key,
    datasource_name, lang_code, google_lang, translate, video,
    filename_as_title, thread_count, scrape_mode, target_files,
    proxy, override_search_name, normalize_media_paths,
    anbernic_compatible,
):
    source = get_datasource(datasource_name)
    datasource_display = source.display_name if source else datasource_name
    mode_display = '补全' if scrape_mode == 'complement' else '刷新'
    target_display = ('全部游戏' if target_files is None
                      else f'指定 {len(target_files)} 个游戏')
    formats = ', '.join(file_extensions)
    credential = '已配置' if api_key else '未配置'
    translation_target = google_lang or '未设置'
    search_override = override_search_name or '未设置'
    lines = [
        f'平台: {platform_name}',
        f'游戏目录: {Path(game_dir)}',
        f'模式: {mode_display}',
        f'处理范围: {target_display}',
        f'文件格式: {formats}',
        (f'在线补全: {_enabled_text(online_mode)} | '
         f'数据源: {datasource_display} | 凭据: {credential}'),
        (f'解析语言: {lang_code} | 翻译: {_enabled_text(translate)} | '
         f'翻译目标: {translation_target}'),
        (f'视频: {_enabled_text(video)} | '
         f'文件名作为标题: {_enabled_text(filename_as_title)}'),
        f'线程: {thread_count}',
        (f'Pegasus: {_enabled_text(generate_meta)} | '
         f'gamelist.xml: {_enabled_text(generate_gamelist)}'),
        f'强制保持图片目录统一: {_enabled_text(normalize_media_paths)}',
        f'兼容 Anbernic 封面: {_enabled_text(anbernic_compatible)}',
        f'代理: {_safe_proxy_display(proxy)}',
        f'手动搜索词: {search_override}',
    ]
    log('[刮削配置] ========================================')
    for line in lines:
        log(f'[刮削配置] {line}')
    log('[刮削配置] ========================================')


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


def _resolve_index_path(game_dir, value):
    value = str(value).strip().replace('\\', '/')
    if value.startswith('./'):
        value = value[2:]
    path = Path(value)
    return path if path.is_absolute() else Path(game_dir) / path


def _pegasus_cover_records(meta_path):
    collection_lines, games = parse_pegasus_meta(meta_path)
    records = {}
    for game in games:
        match = re.search(
            r'^assets\.box(?:Front|_front):\s*(.+)$',
            game['lines'],
            re.MULTILINE,
        )
        if game['file'] and match:
            records[game['file']] = {
                'path': match.group(1).strip(),
                'game': game,
            }
    return collection_lines, games, records


def _gamelist_cover_records(gamelist_path):
    import xml.etree.ElementTree as ET

    if not gamelist_path.exists():
        return None, {}, None
    try:
        tree = ET.parse(gamelist_path)
    except Exception as error:
        return None, {}, error

    records = {}
    for game_el in tree.findall('game'):
        path_el = game_el.find('path')
        image_el = game_el.find('image')
        if (path_el is None or not path_el.text
                or image_el is None or not image_el.text):
            continue
        filename = path_el.text.strip()
        if filename.startswith('./'):
            filename = filename[2:]
        records[filename] = {
            'path': image_el.text.strip(),
            'image': image_el,
        }
    return tree, records, None


def _same_file_content(first, second):
    import filecmp

    return filecmp.cmp(first, second, shallow=False)


def _prune_empty_parents(start_dir, game_dir):
    root = Path(game_dir).resolve()
    current = Path(start_dir).resolve()
    protected = {root, root / 'media', root / 'Imgs'}
    removed = []
    if current != root and root not in current.parents:
        return removed
    while current not in protected:
        try:
            current.rmdir()
        except OSError:
            break
        removed.append(current)
        current = current.parent
    return removed


def _display_media_path(root, path):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _write_pegasus_document(meta_path, collection_lines, games):
    with open(meta_path, 'w', encoding='utf-8') as output:
        for collection in collection_lines:
            output.write(collection.strip() + '\n')
        output.write('\n')
        for game in games:
            output.write('\n' + game['lines'].strip() + '\n')


def organize_existing_media(game_dir, filenames, normalize_paths=True,
                            anbernic_compatible=False, log=print):
    import shutil
    import xml.etree.ElementTree as ET

    root = Path(game_dir)
    filenames = list(filenames)
    meta_path = root / 'metadata.pegasus.txt'
    gamelist_path = root / 'gamelist.xml'
    collections, games, pegasus = _pegasus_cover_records(meta_path)
    gamelist_tree, gamelist, gamelist_error = _gamelist_cover_records(
        gamelist_path)
    if gamelist_error:
        log(f'[图片整理] gamelist.xml 解析失败: {gamelist_error}')

    stats = {
        'checked': len(filenames),
        'migrated': 0,
        'standard': 0,
        'anbernic': 0,
        'missing': 0,
        'no_index': 0,
        'conflicts': 0,
        'failures': 0,
        'empty_dirs': 0,
    }
    log(
        f'[图片整理] 开始检查 {len(filenames)} 个游戏 | '
        f'目录统一: {_enabled_text(normalize_paths)} | '
        f'Anbernic兼容: {_enabled_text(anbernic_compatible)} | '
        f'Pegasus: {"已发现" if meta_path.exists() else "未发现"} | '
        f'gamelist: {"已发现" if gamelist_path.exists() else "未发现"}'
    )

    pegasus_changed = False
    gamelist_changed = False
    removable_sources = set()
    preserved_sources = set()

    def organize_game(filename):
        nonlocal pegasus_changed, gamelist_changed
        records = [
            record for record in (
                pegasus.get(filename),
                gamelist.get(filename),
            ) if record
        ]
        if not records:
            stats['no_index'] += 1
            log(f'[图片整理] 未发现索引封面: {filename}')
            return

        source = None
        for record in records:
            candidate = _resolve_index_path(root, record['path'])
            if candidate.is_file():
                source = candidate
                break
        suffix = source.suffix if source else Path(records[0]['path']).suffix
        if not suffix:
            stats['missing'] += 1
            log(f'[图片整理] 无法识别封面格式: {filename}')
            return

        available = source
        standard_relative = standard_cover_path(filename, suffix)
        standard = root / standard_relative
        if normalize_paths:
            if source and source.resolve() != standard.resolve():
                standard.parent.mkdir(parents=True, exist_ok=True)
                if standard.exists():
                    if _same_file_content(source, standard):
                        removable_sources.add(source)
                        stats['standard'] += 1
                        log(
                            f'[图片整理] 目标已有相同图片: {filename} | '
                            f'{standard_relative}'
                        )
                    else:
                        preserved_sources.add(source)
                        removable_sources.discard(source)
                        stats['conflicts'] += 1
                        log(
                            f'[图片整理] 目标已存在且内容冲突，保留旧源文件: '
                            f'{filename} | '
                            f'{_display_media_path(root, source)}'
                        )
                else:
                    shutil.copy2(source, standard)
                    removable_sources.add(source)
                    stats['migrated'] += 1
                    log(
                        f'[图片整理] 已迁移: {filename} | '
                        f'{_display_media_path(root, source)} -> '
                        f'{standard_relative}'
                    )
            elif source and source.resolve() == standard.resolve():
                stats['standard'] += 1
                log(f'[图片整理] 路径已符合标准: {filename} | {standard_relative}')

            if standard.is_file():
                available = standard
                pegasus_record = pegasus.get(filename)
                if (pegasus_record
                        and pegasus_record['path'] != standard_relative):
                    pegasus_record['game']['lines'] = re.sub(
                        r'^assets\.box(?:Front|_front):\s*.+$',
                        f'assets.boxFront: {standard_relative}',
                        pegasus_record['game']['lines'],
                        count=1,
                        flags=re.MULTILINE,
                    )
                    pegasus_changed = True
                    log(
                        f'[Pegasus] 已更新封面路径: {filename} -> '
                        f'{standard_relative}'
                    )

                gamelist_record = gamelist.get(filename)
                indexed_path = f'./{standard_relative}'
                if (gamelist_record
                        and gamelist_record['path'] != indexed_path):
                    gamelist_record['image'].text = indexed_path
                    gamelist_changed = True
                    log(
                        f'[gamelist] 已更新封面路径: {filename} -> '
                        f'{indexed_path}'
                    )
            else:
                stats['missing'] += 1
                log(f'[图片整理] 找不到封面文件: {filename}')

        if anbernic_compatible and available and available.is_file():
            destination = root / anbernic_cover_path(
                filename, available.suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if available.resolve() != destination.resolve():
                shutil.copy2(available, destination)
                stats['anbernic'] += 1
                log(
                    f'[Anbernic封面] 已复制: {filename} -> '
                    f'{_display_media_path(root, destination)}'
                )

    for filename in filenames:
        try:
            organize_game(filename)
        except Exception as error:
            stats['failures'] += 1
            log(f'[图片整理] {filename} 处理失败: {error}')

    index_write_failed = False
    if pegasus_changed:
        try:
            backup_file(meta_path)
            _write_pegasus_document(meta_path, collections, games)
            log(f'[Pegasus] 索引写入完成: {meta_path}')
        except Exception as error:
            index_write_failed = True
            stats['failures'] += 1
            log(f'[图片整理] Pegasus 索引写入失败: {error}')
    if gamelist_changed and gamelist_tree is not None:
        try:
            backup_file(gamelist_path)
            ET.indent(gamelist_tree, space='  ')
            gamelist_tree.write(
                str(gamelist_path),
                encoding='utf-8',
                xml_declaration=True,
            )
            log(f'[gamelist] 索引写入完成: {gamelist_path}')
        except Exception as error:
            index_write_failed = True
            stats['failures'] += 1
            log(f'[图片整理] gamelist 索引写入失败: {error}')

    if not index_write_failed:
        for source in removable_sources - preserved_sources:
            try:
                if source.is_file():
                    source.unlink()
                    log(
                        f'[图片整理] 已删除旧封面: '
                        f'{_display_media_path(root, source)}'
                    )
                    removed_dirs = _prune_empty_parents(source.parent, root)
                    for removed_dir in removed_dirs:
                        stats['empty_dirs'] += 1
                        log(
                            f'[图片整理] 已删除空目录: '
                            f'{_display_media_path(root, removed_dir)}'
                        )
            except Exception as error:
                stats['failures'] += 1
                log(f'[图片整理] 旧封面清理失败 {source}: {error}')

    log(
        f'[图片整理] 完成: 检查 {stats["checked"]}，'
        f'迁移 {stats["migrated"]}，已标准 {stats["standard"]}，'
        f'Anbernic复制 {stats["anbernic"]}，无索引 {stats["no_index"]}，'
        f'缺失 {stats["missing"]}，冲突 {stats["conflicts"]}，'
        f'失败 {stats["failures"]}，清理空目录 {stats["empty_dirs"]}'
    )


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
    normalize_media_paths=True, anbernic_compatible=False,
    log=print, cancel_event=None
):
    import shutil
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from platform_base import collect_game_files

    _log_scrape_configuration(
        log,
        game_dir=game_dir,
        platform_name=platform_name,
        file_extensions=file_extensions,
        generate_meta=generate_meta,
        generate_gamelist=generate_gamelist,
        online_mode=online_mode,
        api_key=api_key,
        datasource_name=datasource_name,
        lang_code=lang_code,
        google_lang=google_lang,
        translate=translate,
        video=video,
        filename_as_title=filename_as_title,
        thread_count=thread_count,
        scrape_mode=scrape_mode,
        target_files=target_files,
        proxy=proxy,
        override_search_name=override_search_name,
        normalize_media_paths=normalize_media_paths,
        anbernic_compatible=anbernic_compatible,
    )

    if proxy:
        set_proxy(proxy)

    game_files, temp_dir = collect_game_files(game_dir, file_extensions)

    if not game_files:
        log("[文件扫描] 未找到游戏文件")
        return
    log(f"[文件扫描] 找到 {len(game_files)} 个游戏文件")

    if target_files is not None:
        game_files = [(p, n) for p, n in game_files if n in target_files]
        if not game_files:
            log("[文件扫描] 未找到指定的游戏文件")
            return
        log(f"[文件扫描] 已筛选 {len(game_files)} 个指定游戏")

    if normalize_media_paths or anbernic_compatible:
        organize_existing_media(
            game_dir,
            {name for _, name in game_files},
            normalize_paths=normalize_media_paths,
            anbernic_compatible=anbernic_compatible,
            log=log,
        )

    if scrape_mode == 'complement' and target_files is None:
        complete = get_complete_games(game_dir)
        before = len(game_files)
        game_files = [(p, n) for p, n in game_files if n not in complete]
        skipped = before - len(game_files)
        if skipped:
            log(f"[刮削] 补全模式跳过 {skipped} 个已有完整元数据的游戏")
        if not game_files:
            log("[刮削] 所有游戏已有完整元数据，无需处理")
            return

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
        log(f"[游戏解析] 开始处理 {idx}/{total}: {display_name}")
        try:
            info = extract_fn(str(game_path), lang_code=lang_code,
                              log=log, **(extract_kwargs or {}))
            if not info:
                return None
            info['filename'] = display_name
            if not info.get('game_id'):
                info['game_id'] = (info.pop('game_code', None)
                                   or info.pop('product_id', None) or '')
            log(
                f"[游戏解析] 解析成功: {display_name} | "
                f"标题: {info.get('title', '')} | "
                f"英文标题: {info.get('title_en', '')} | "
                f"游戏ID: {info.get('game_id', '')}"
            )

            icon_data = info.pop('icon_data', None)
            img_data = None
            img_ext = '.jpg'
            image_rel_path = None

            if online_mode and source:
                search_name = override_search_name or info.get('title_en') or info['title']
                log(
                    f"[游戏搜索] 开始搜索: {search_name} | "
                    f"数据源: {source.display_name} | 平台ID: {platform_id}"
                )
                online = source.fetch_metadata(
                    search_name, platform_id,
                    platform_name=platform_name,
                    include_boxart=True,
                    log=log)
                if not online:
                    wiki = get_datasource('wikipedia')
                    if wiki:
                        log(
                            f"[游戏搜索] 主数据源未匹配，回退到: "
                            f"{wiki.display_name}"
                        )
                        online = wiki.fetch_metadata(
                            search_name, platform_name=platform_name)
                if online:
                    matched_title = (online.get('title')
                                     or online.get('title_ss')
                                     or search_name)
                    matched_id = online.get('game_id', '')
                    log(
                        f"[游戏搜索] 匹配成功: {matched_title} | "
                        f"游戏ID: {matched_id or '未提供'}"
                    )
                    boxart_url = online.pop('boxart_url', None)
                    if boxart_url:
                        log(f"[图片下载] 开始下载: {boxart_url}")
                        boxart_data = _http_get_bytes(boxart_url)
                        if boxart_data:
                            img_ext = Path(boxart_url).suffix or '.jpg'
                            img_data = boxart_data
                            log(
                                f"[图片下载] 下载成功: {len(boxart_data)} 字节"
                            )
                        else:
                            log(f"[图片下载] 下载失败: {boxart_url}")
                    else:
                        log("[图片下载] 匹配结果未提供封面地址")
                    if translate and google_lang:
                        for k in ('description', 'genres'):
                            if online.get(k):
                                log(
                                    f"[翻译] 开始翻译 {k}: {display_name} -> "
                                    f"{google_lang}"
                                )
                                translated_value = google_translate(
                                    online[k], google_lang)
                                if translated_value:
                                    online[k] = translated_value
                                    log(
                                        f"[翻译] 翻译完成 {k}: {display_name}"
                                    )
                                else:
                                    log(
                                        f"[翻译] 翻译失败 {k}: {display_name}"
                                    )
                    if video:
                        if not online.get('youtube'):
                            log(f"[视频] 未找到视频: {display_name}")
                        else:
                            log(
                                f"[视频] 已获取视频: {online['youtube']}"
                            )
                    else:
                        online.pop('youtube', None)
                    info.update(online)
                    log(f"[元数据补全] 已合并在线元数据: {display_name}")
                else:
                    log(f"[游戏搜索] 未找到匹配: {search_name}")

            if (translate and google_lang and google_lang.startswith('zh')
                    and not filename_as_title):
                if not _has_cjk(info['title']):
                    log(
                        f"[翻译] 开始翻译游戏标题: {info['title']} -> "
                        f"{google_lang}"
                    )
                    translated = google_translate(info['title'], google_lang)
                    if translated and translated != info['title']:
                        info['title'] = translated
                        log(f"[翻译] 游戏标题翻译完成: {translated}")
                    else:
                        log("[翻译] 游戏标题未发生变化")

            if filename_as_title:
                info['title'] = Path(display_name).stem

            if not img_data and icon_data:
                img_data = icon_data
                img_ext = '.png'
                log(f"[图片下载] 使用 ROM 内置图标: {display_name}")

            if img_data:
                image_rel_path = standard_cover_path(display_name, img_ext)
                out_path = Path(game_dir) / image_rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img_data)
                if anbernic_compatible:
                    anbernic_path = Path(game_dir) / anbernic_cover_path(
                        display_name, img_ext)
                    anbernic_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(out_path, anbernic_path)
                    log(
                        f"[Anbernic封面] 已复制: {display_name} -> "
                        f"{anbernic_cover_path(display_name, img_ext)}"
                    )
                log(f"[图片下载] 已保存: {display_name} -> {image_rel_path}")
            else:
                log(f"[图片下载] 未获得封面: {display_name}")

            return (info, image_rel_path)
        except Exception as e:
            log(f"[游戏解析] 处理失败: {display_name} | {e}")
            return None

    try:
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {executor.submit(process_game, p, n): n
                       for p, n in game_files}
            for fut in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    log("[刮削] 用户已取消操作")
                    break
                try:
                    result = fut.result()
                except Exception as error:
                    log(f"[刮削] 任务执行异常: {futures[fut]} | {error}")
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
        log(f"[Pegasus] 元数据已写入: {meta_path}")

    if generate_gamelist and meta_results:
        gl_path = Path(game_dir) / 'gamelist.xml'
        write_gamelist_xml(gl_path, meta_results)
        log(f"[gamelist] 元数据已写入: {gl_path}")

    log(f"[刮削完成] 成功: {success[0]} | 失败: {failed[0]}")


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
                self.log_signal.emit(f"[刮削] 执行异常: {e}")
            finally:
                self.finished_signal.emit()

except ImportError:
    pass

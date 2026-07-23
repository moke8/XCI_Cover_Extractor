# 图片目录统一与 Anbernic 封面兼容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有新封面按 ROM 文件名写入标准 `media` 目录，可选迁移旧索引封面，并可额外生成不入索引的 Anbernic `Imgs` 副本。

**Architecture:** 在 `scrape.py` 增加纯路径函数和独立的刮削前媒体整理函数；整理函数只从 Pegasus/gamelist 的明确引用解析封面，负责安全迁移、索引改写、空目录清理和可选 `Imgs` 复制。刮削线程继续负责新封面，但统一复用 ROM 文件名路径函数；UI 仅保存两个布尔配置并通过现有参数链传入。

**Tech Stack:** Python 3.8+、`pathlib`、`shutil`、`xml.etree.ElementTree`、标准库 `unittest`、PySide6。

---

### Task 1: 标准封面路径函数

**Files:**
- Create: `tests/test_scrape_media.py`
- Modify: `scrape.py:35`

- [ ] **Step 1: 写路径规则失败测试**

```python
import unittest

from scrape import anbernic_cover_path, standard_cover_path


class CoverPathTests(unittest.TestCase):
    def test_paths_use_sanitized_rom_stem_and_preserve_extension(self):
        self.assertEqual(
            standard_cover_path('Game: Name.gba', '.png'),
            'media/Game_ Name/boxfront.png',
        )
        self.assertEqual(
            anbernic_cover_path('Game: Name.gba', '.png'),
            'Imgs/Game_ Name.png',
        )

    def test_zip_uses_zip_filename_stem(self):
        self.assertEqual(
            standard_cover_path('Archive Game.zip', '.webp'),
            'media/Archive Game/boxfront.webp',
        )
```

- [ ] **Step 2: 运行测试并确认因函数不存在而失败**

Run: `python -m unittest tests.test_scrape_media.CoverPathTests -v`

Expected: `ImportError`，指出 `standard_cover_path` 或 `anbernic_cover_path` 尚不存在。

- [ ] **Step 3: 实现最小路径函数**

在 `sanitize_filename()` 后增加：

```python
def _normalized_image_suffix(image_suffix):
    suffix = image_suffix if str(image_suffix).startswith('.') else f'.{image_suffix}'
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
```

- [ ] **Step 4: 运行路径测试并确认通过**

Run: `python -m unittest tests.test_scrape_media.CoverPathTests -v`

Expected: 2 tests pass。

### Task 2: 已索引封面的安全迁移

**Files:**
- Modify: `tests/test_scrape_media.py`
- Modify: `scrape.py:43`

- [ ] **Step 1: 写 Pegasus 与 gamelist 迁移失败测试**

在 `tests/test_scrape_media.py` 增加使用 `tempfile.TemporaryDirectory` 的测试：创建 `Alpha.gba`、`legacy/covers/alpha.png`、同时引用旧路径的 `metadata.pegasus.txt` 和 `gamelist.xml`，调用：

```python
organize_existing_media(
    root,
    {'Alpha.gba'},
    normalize_paths=True,
    anbernic_compatible=True,
    log=messages.append,
)
```

断言：

```python
self.assertFalse((root / 'legacy' / 'covers' / 'alpha.png').exists())
self.assertFalse((root / 'legacy').exists())
self.assertEqual((root / 'media/Alpha/boxfront.png').read_bytes(), b'png-data')
self.assertEqual((root / 'Imgs/Alpha.png').read_bytes(), b'png-data')
self.assertIn('assets.boxFront: media/Alpha/boxfront.png', pegasus_text)
self.assertEqual(gamelist_image, './media/Alpha/boxfront.png')
```

- [ ] **Step 2: 运行测试并确认因整理函数不存在而失败**

Run: `python -m unittest tests.test_scrape_media.ExistingMediaTests.test_migrates_both_indexes_and_copies_anbernic_cover -v`

Expected: `ImportError` 或 `AttributeError` 指向 `organize_existing_media`。

- [ ] **Step 3: 实现索引读取与封面解析**

在 `scrape.py` 增加内部函数：

```python
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
        match = re.search(r'^assets\.box(?:Front|_front):\s*(.+)$',
                          game['lines'], re.MULTILINE)
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
        if path_el is None or not path_el.text or image_el is None or not image_el.text:
            continue
        filename = path_el.text.strip()
        if filename.startswith('./'):
            filename = filename[2:]
        records[filename] = {'path': image_el.text.strip(), 'image': image_el}
    return tree, records, None
```

Python 3.8 不支持 `str.removeprefix`，实际实现使用显式 `startswith('./')` 分支。

- [ ] **Step 4: 实现安全移动、索引改写和空目录清理**

增加以下职责明确的内部函数：

```python
def _same_file_content(first, second):
    return first.stat().st_size == second.stat().st_size and first.read_bytes() == second.read_bytes()


def _prune_empty_parents(start_dir, game_dir):
    protected = {Path(game_dir), Path(game_dir) / 'media', Path(game_dir) / 'Imgs'}
    current = start_dir
    while current not in protected and current != Path(game_dir):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


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
    meta_path = root / 'metadata.pegasus.txt'
    gamelist_path = root / 'gamelist.xml'
    collections, games, pegasus = _pegasus_cover_records(meta_path)
    gamelist_tree, gamelist, gamelist_error = _gamelist_cover_records(gamelist_path)
    if gamelist_error:
        log(f'[图片整理] gamelist.xml 解析失败: {gamelist_error}')

    pegasus_changed = False
    gamelist_changed = False
    for filename in filenames:
        records = [record for record in (pegasus.get(filename), gamelist.get(filename))
                   if record]
        if not records:
            continue
        source = next(
            (_resolve_index_path(root, record['path']) for record in records
             if _resolve_index_path(root, record['path']).is_file()),
            None,
        )
        suffix = source.suffix if source else Path(records[0]['path']).suffix
        if not suffix:
            log(f'[图片整理] 无法识别封面格式: {filename}')
            continue

        available = source
        standard_relative = standard_cover_path(filename, suffix)
        standard = root / standard_relative
        if normalize_paths:
            if source and source.resolve() != standard.resolve():
                standard.parent.mkdir(parents=True, exist_ok=True)
                if standard.exists():
                    if _same_file_content(source, standard):
                        source.unlink()
                        _prune_empty_parents(source.parent, root)
                    else:
                        log(f'[图片整理] 目标已存在，保留旧源文件: {source}')
                else:
                    shutil.move(str(source), str(standard))
                    _prune_empty_parents(source.parent, root)
            if standard.is_file():
                available = standard
                pegasus_record = pegasus.get(filename)
                if pegasus_record and pegasus_record['path'] != standard_relative:
                    pegasus_record['game']['lines'] = re.sub(
                        r'^assets\.box(?:Front|_front):\s*.+$',
                        f'assets.boxFront: {standard_relative}',
                        pegasus_record['game']['lines'],
                        count=1,
                        flags=re.MULTILINE,
                    )
                    pegasus_changed = True
                gamelist_record = gamelist.get(filename)
                indexed_path = f'./{standard_relative}'
                if gamelist_record and gamelist_record['path'] != indexed_path:
                    gamelist_record['image'].text = indexed_path
                    gamelist_changed = True
            else:
                log(f'[图片整理] 找不到封面文件: {filename}')

        if anbernic_compatible and available and available.is_file():
            destination = root / anbernic_cover_path(filename, available.suffix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if available.resolve() != destination.resolve():
                shutil.copy2(available, destination)

    if pegasus_changed:
        backup_file(meta_path)
        _write_pegasus_document(meta_path, collections, games)
    if gamelist_changed and gamelist_tree is not None:
        backup_file(gamelist_path)
        ET.indent(gamelist_tree, space='  ')
        gamelist_tree.write(str(gamelist_path), encoding='utf-8', xml_declaration=True)
```

Pegasus 写回保留原 collection 和 game block；仅用正则替换对应 block 的 `assets.boxFront` 行。gamelist 使用原 ElementTree，仅修改 `<image>` 文本并沿用 `ET.indent` 与 UTF-8 XML 声明。

- [ ] **Step 5: 运行迁移测试并确认通过**

Run: `python -m unittest tests.test_scrape_media.ExistingMediaTests.test_migrates_both_indexes_and_copies_anbernic_cover -v`

Expected: 1 test passes。

- [ ] **Step 6: 写关闭迁移但仍复制 Anbernic 的失败测试**

创建 `Beta.gba` 与 Pegasus 旧路径，调用 `normalize_paths=False, anbernic_compatible=True`，断言旧封面和旧索引不变，但 `Imgs/Beta.jpg` 已生成。

- [ ] **Step 7: 运行测试确认失败，再补齐独立复制逻辑**

Run: `python -m unittest tests.test_scrape_media.ExistingMediaTests.test_copies_anbernic_without_normalizing_old_path -v`

Expected before implementation: FAIL because `Imgs/Beta.jpg` missing。补齐逻辑后重跑，Expected: PASS。

- [ ] **Step 8: 写冲突与清理边界测试并实现**

覆盖：目标已存在且内容不同不覆盖/不删除源；内容相同删除重复源；非空目录不删除；损坏 XML 仅记录日志且不阻止 Pegasus 迁移。

- [ ] **Step 9: 运行媒体迁移测试集**

Run: `python -m unittest tests.test_scrape_media.ExistingMediaTests -v`

Expected: all tests pass，无未处理异常。

### Task 3: 将媒体整理接入批量刮削

**Files:**
- Modify: `tests/test_scrape_media.py`
- Modify: `scrape.py:212`

- [ ] **Step 1: 写补全模式先迁移后跳过的失败测试**

用临时 `.gba`、完整描述和旧封面构造现有 Pegasus；传入会记录调用次数的 `extract_fn`，调用：

```python
batch_scrape(
    game_dir=root,
    extract_fn=extract_fn,
    file_extensions=('gba',),
    platform_id=5,
    platform_name='GBA',
    collection_defaults={},
    scrape_mode='complement',
    normalize_media_paths=True,
)
```

断言封面已迁移至 `media/Alpha/boxfront.png`，索引已更新，且 `extract_fn` 调用次数为 0。

- [ ] **Step 2: 运行测试并确认参数或行为失败**

Run: `python -m unittest tests.test_scrape_media.BatchScrapeMediaTests.test_complement_migrates_before_skipping_complete_game -v`

Expected: FAIL because `batch_scrape` 不接受 `normalize_media_paths` 或旧封面未迁移。

- [ ] **Step 3: 在跳过逻辑前调用媒体整理**

在现有 `batch_scrape` 签名的 `proxy` 参数前加入：

```python
normalize_media_paths=True, anbernic_compatible=False,
```

在 `target_files` 过滤之后、补全完整度检查之前调用：

```python
selected_names = {name for _, name in game_files}
if normalize_media_paths or anbernic_compatible:
    organize_existing_media(
        game_dir,
        selected_names,
        normalize_paths=normalize_media_paths,
        anbernic_compatible=anbernic_compatible,
        log=log,
    )
```

- [ ] **Step 4: 运行补全模式测试并确认通过**

Run: `python -m unittest tests.test_scrape_media.BatchScrapeMediaTests.test_complement_migrates_before_skipping_complete_game -v`

Expected: PASS。

- [ ] **Step 5: 写新封面双份保存失败测试**

用返回 `icon_data=b'png-data'` 的 fake extractor，关闭在线刮削，开启 `anbernic_compatible=True`，断言：

```python
self.assertEqual((root / 'media/New Game/boxfront.png').read_bytes(), b'png-data')
self.assertEqual((root / 'Imgs/New Game.png').read_bytes(), b'png-data')
self.assertIn('assets.boxFront: media/New Game/boxfront.png', pegasus_text)
self.assertNotIn('Imgs/', pegasus_text)
```

- [ ] **Step 6: 运行测试并确认仍按标题命名而失败**

Run: `python -m unittest tests.test_scrape_media.BatchScrapeMediaTests.test_new_cover_uses_rom_name_and_creates_unindexed_copy -v`

Expected: FAIL，现有实现写入按 `title_en` 命名的目录，且无 `Imgs` 副本。

- [ ] **Step 7: 复用路径函数写入新封面**

移除以 `safe_title` 作为媒体目录的逻辑，改为：

```python
image_rel_path = standard_cover_path(display_name, img_ext)
out_path = Path(game_dir) / image_rel_path
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_bytes(img_data)
if anbernic_compatible:
    anbernic_path = Path(game_dir) / anbernic_cover_path(display_name, img_ext)
    anbernic_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_path, anbernic_path)
```

不要创建无封面游戏的空 `media` 目录；不要把 `Imgs` 路径传给任何索引写入函数。

- [ ] **Step 8: 运行批量刮削媒体测试集**

Run: `python -m unittest tests.test_scrape_media.BatchScrapeMediaTests -v`

Expected: all tests pass。

### Task 4: UI 配置与全调用链传参

**Files:**
- Create: `tests/test_scrape_settings.py`
- Modify: `main.py:379`
- Modify: `main.py:483`
- Modify: `main.py:502`
- Modify: `main.py:801`
- Modify: `main.py:848`
- Modify: `main.py:890`
- Modify: `platform_base.py:528`
- Modify: `platform_base.py:583`

- [ ] **Step 1: 写设置默认值与持久化失败测试**

测试文件在导入 PySide6 前设置 `QT_QPA_PLATFORM=offscreen`，创建一次共享 `QApplication`。实例化 `ScrapeSettingsDialog({})` 后断言：

```python
self.assertTrue(dialog.normalize_media_check.isChecked())
self.assertFalse(dialog.anbernic_compatible_check.isChecked())
self.assertTrue(dialog.get_settings()['normalize_media_paths'])
self.assertFalse(dialog.get_settings()['anbernic_compatible'])
```

再传入相反值，断言 `_load_from_settings()` 正确恢复。

- [ ] **Step 2: 运行测试并确认控件不存在而失败**

Run: `python -m unittest tests.test_scrape_settings -v`

Expected: FAIL with missing `normalize_media_check`。

- [ ] **Step 3: 新增两个独立复选框并接入设置字典**

在刮削选项区域新增：

```python
self.normalize_media_check = QCheckBox('强制保持图片目录统一')
self.anbernic_compatible_check = QCheckBox('兼容 Anbernic 封面')
```

加载默认值：

```python
self.normalize_media_check.setChecked(s.get('normalize_media_paths', True))
self.anbernic_compatible_check.setChecked(s.get('anbernic_compatible', False))
```

`get_settings()`、`MainWindow._scrape_settings`、`get_global_settings()` 与 `_load_config()` 的 `scrape_keys` 全部加入同名键。

- [ ] **Step 4: 将两个参数传入所有刮削入口**

`BasePlatformTab._scrape_games()` 和 `_start_extract()` 构造的 `params` 均加入：

```python
normalize_media_paths=g.get('normalize_media_paths', True),
anbernic_compatible=g.get('anbernic_compatible', False),
```

这同时覆盖右键补全、右键刷新、手动搜索和完整批量刮削。

- [ ] **Step 5: 运行设置测试并确认通过**

Run: `python -m unittest tests.test_scrape_settings -v`

Expected: all tests pass。

### Task 5: 文档与完整验证

**Files:**
- Modify: `README.md`
- Verify: `scrape.py`
- Verify: `main.py`
- Verify: `platform_base.py`
- Verify: `tests/test_scrape_media.py`
- Verify: `tests/test_scrape_settings.py`

- [ ] **Step 1: 更新 README 功能和输出目录**

在“功能一览”与“使用方法”附近说明：新封面固定写入 `media/<ROM名>/boxfront.<原格式>`；目录统一默认开启并迁移已有索引图片；Anbernic 兼容可额外生成 `Imgs/<ROM名>.<原格式>`，该副本不写索引也不会被后续未勾选的刮削删除。

- [ ] **Step 2: 运行语法检查**

Run: `python -m py_compile scrape.py main.py platform_base.py`

Expected: exit code 0，无输出。

- [ ] **Step 3: 运行全部自动化测试**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass，无 error/failure。

- [ ] **Step 4: 检查补丁格式与意外改动**

Run: `git -c safe.directory=C:/Users/65283/Documents/Code/game-scanf diff --check`

Expected: exit code 0，无空白错误。检查 `git diff -- scrape.py main.py platform_base.py README.md tests`，确认未改动用户现有的无关 README 内容。

- [ ] **Step 5: 手动冒烟验证界面**

Run: `python main.py`

Expected: “刮削设置”显示两个新选项，“强制保持图片目录统一”默认勾选，“兼容 Anbernic 封面”默认未勾选；关闭窗口后进程正常退出。若当前环境不允许 GUI，则记录未执行原因，不以此替代自动化测试。

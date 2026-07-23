import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scrape import (
    anbernic_cover_path,
    batch_scrape,
    organize_existing_media,
    standard_cover_path,
)


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


class ExistingMediaTests(unittest.TestCase):
    def test_migrates_both_indexes_and_copies_anbernic_cover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'Alpha.gba').write_bytes(b'rom')
            old_cover = root / 'legacy' / 'covers' / 'alpha.png'
            old_cover.parent.mkdir(parents=True)
            old_cover.write_bytes(b'png-data')
            (root / 'metadata.pegasus.txt').write_text(
                'collection: GBA\n\n'
                'game: Alpha\n'
                'file: Alpha.gba\n'
                'description: Complete\n'
                'assets.boxFront: legacy/covers/alpha.png\n',
                encoding='utf-8',
            )
            (root / 'gamelist.xml').write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<gameList><game><path>./Alpha.gba</path>'
                '<name>Alpha</name><image>./legacy/covers/alpha.png</image>'
                '</game></gameList>',
                encoding='utf-8',
            )
            messages = []

            organize_existing_media(
                root,
                {'Alpha.gba'},
                normalize_paths=True,
                anbernic_compatible=True,
                log=messages.append,
            )

            self.assertFalse(old_cover.exists())
            self.assertFalse((root / 'legacy').exists())
            self.assertEqual(
                (root / 'media' / 'Alpha' / 'boxfront.png').read_bytes(),
                b'png-data',
            )
            self.assertEqual(
                (root / 'Imgs' / 'Alpha.png').read_bytes(),
                b'png-data',
            )
            pegasus_text = (root / 'metadata.pegasus.txt').read_text(
                encoding='utf-8')
            self.assertIn(
                'assets.boxFront: media/Alpha/boxfront.png',
                pegasus_text,
            )
            gamelist = ET.parse(root / 'gamelist.xml')
            self.assertEqual(
                gamelist.findtext('./game/image'),
                './media/Alpha/boxfront.png',
            )

    def test_copies_anbernic_without_normalizing_old_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cover = root / 'old' / 'beta.jpg'
            old_cover.parent.mkdir(parents=True)
            old_cover.write_bytes(b'jpeg-data')
            meta_path = root / 'metadata.pegasus.txt'
            original_meta = (
                'game: Beta\n'
                'file: Beta.gba\n'
                'assets.boxFront: old/beta.jpg\n'
            )
            meta_path.write_text(original_meta, encoding='utf-8')

            organize_existing_media(
                root,
                {'Beta.gba'},
                normalize_paths=False,
                anbernic_compatible=True,
            )

            self.assertEqual(old_cover.read_bytes(), b'jpeg-data')
            self.assertEqual(
                (root / 'Imgs' / 'Beta.jpg').read_bytes(),
                b'jpeg-data',
            )
            self.assertEqual(meta_path.read_text(encoding='utf-8'), original_meta)

    def test_existing_different_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cover = root / 'old' / 'gamma.png'
            old_cover.parent.mkdir(parents=True)
            old_cover.write_bytes(b'old-data')
            standard_cover = root / 'media' / 'Gamma' / 'boxfront.png'
            standard_cover.parent.mkdir(parents=True)
            standard_cover.write_bytes(b'current-data')
            meta_path = root / 'metadata.pegasus.txt'
            meta_path.write_text(
                'game: Gamma\n'
                'file: Gamma.gba\n'
                'assets.boxFront: old/gamma.png\n',
                encoding='utf-8',
            )
            messages = []

            organize_existing_media(
                root,
                {'Gamma.gba'},
                normalize_paths=True,
                log=messages.append,
            )

            self.assertEqual(old_cover.read_bytes(), b'old-data')
            self.assertEqual(standard_cover.read_bytes(), b'current-data')
            self.assertIn(
                'assets.boxFront: media/Gamma/boxfront.png',
                meta_path.read_text(encoding='utf-8'),
            )
            self.assertTrue(any('目标已存在' in message for message in messages))

    def test_shared_source_is_migrated_for_each_game_before_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shared_cover = root / 'old' / 'shared.png'
            shared_cover.parent.mkdir(parents=True)
            shared_cover.write_bytes(b'shared-data')
            meta_path = root / 'metadata.pegasus.txt'
            meta_path.write_text(
                'game: Alpha\n'
                'file: Alpha.gba\n'
                'assets.boxFront: old/shared.png\n\n'
                'game: Beta\n'
                'file: Beta.gba\n'
                'assets.boxFront: old/shared.png\n',
                encoding='utf-8',
            )

            organize_existing_media(
                root,
                {'Alpha.gba', 'Beta.gba'},
                normalize_paths=True,
            )

            self.assertFalse(shared_cover.exists())
            self.assertEqual(
                (root / 'media' / 'Alpha' / 'boxfront.png').read_bytes(),
                b'shared-data',
            )
            self.assertEqual(
                (root / 'media' / 'Beta' / 'boxfront.png').read_bytes(),
                b'shared-data',
            )
            meta_text = meta_path.read_text(encoding='utf-8')
            self.assertIn(
                'assets.boxFront: media/Alpha/boxfront.png', meta_text)
            self.assertIn(
                'assets.boxFront: media/Beta/boxfront.png', meta_text)

    def test_failed_game_does_not_stop_later_migrations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            alpha_cover = root / 'old' / 'alpha.png'
            beta_cover = root / 'old' / 'beta.png'
            alpha_cover.parent.mkdir(parents=True)
            alpha_cover.write_bytes(b'alpha-data')
            beta_cover.write_bytes(b'beta-data')
            (root / 'metadata.pegasus.txt').write_text(
                'game: Alpha\n'
                'file: Alpha.gba\n'
                'assets.boxFront: old/alpha.png\n\n'
                'game: Beta\n'
                'file: Beta.gba\n'
                'assets.boxFront: old/beta.png\n',
                encoding='utf-8',
            )
            messages = []
            real_copy = shutil.copy2

            def copy_with_alpha_failure(source, destination, *args, **kwargs):
                if Path(source) == alpha_cover:
                    raise OSError('simulated copy failure')
                return real_copy(source, destination, *args, **kwargs)

            with patch('shutil.copy2', side_effect=copy_with_alpha_failure):
                organize_existing_media(
                    root,
                    ['Alpha.gba', 'Beta.gba'],
                    normalize_paths=True,
                    log=messages.append,
                )

            self.assertTrue(alpha_cover.exists())
            self.assertEqual(
                (root / 'media' / 'Beta' / 'boxfront.png').read_bytes(),
                b'beta-data',
            )
            self.assertTrue(
                any('Alpha.gba' in message and '失败' in message
                    for message in messages)
            )

    def test_index_write_failure_keeps_old_source_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cover = root / 'old' / 'delta.png'
            old_cover.parent.mkdir(parents=True)
            old_cover.write_bytes(b'delta-data')
            (root / 'metadata.pegasus.txt').write_text(
                'game: Delta\n'
                'file: Delta.gba\n'
                'assets.boxFront: old/delta.png\n',
                encoding='utf-8',
            )
            messages = []

            with patch(
                'scrape._write_pegasus_document',
                side_effect=OSError('simulated index failure'),
            ):
                organize_existing_media(
                    root,
                    ['Delta.gba'],
                    normalize_paths=True,
                    log=messages.append,
                )

            self.assertTrue(old_cover.is_file())
            self.assertEqual(
                (root / 'media' / 'Delta' / 'boxfront.png').read_bytes(),
                b'delta-data',
            )
            self.assertTrue(
                any('索引写入失败' in message for message in messages)
            )


class BatchScrapeMediaTests(unittest.TestCase):
    def test_filename_title_skips_title_translation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rom_path = root / '口袋妖怪 魂银 493版.nds'
            rom_path.write_bytes(b'rom')
            messages = []

            def extract_fn(*_args, **_kwargs):
                return {
                    'title': 'ポケットモンスター ソウルシルバー',
                    'filename': rom_path.name,
                    'game_code': 'IPGJ',
                }

            with patch('scrape.google_translate') as translate_mock:
                batch_scrape(
                    game_dir=root,
                    extract_fn=extract_fn,
                    file_extensions=('nds',),
                    platform_id=8,
                    platform_name='Nintendo DS',
                    collection_defaults={},
                    generate_meta=True,
                    online_mode=False,
                    google_lang='zh-CN',
                    translate=True,
                    filename_as_title=True,
                    normalize_media_paths=False,
                    thread_count=1,
                    log=messages.append,
                )

            translate_mock.assert_not_called()
            self.assertNotIn(
                '[翻译] 开始翻译游戏标题', '\n'.join(messages))
            self.assertIn(
                'game: 口袋妖怪 魂银 493版',
                (root / 'metadata.pegasus.txt').read_text(encoding='utf-8'),
            )

    def test_complement_migrates_before_skipping_complete_game(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'Alpha.gba').write_bytes(b'rom')
            old_cover = root / 'old' / 'alpha.png'
            old_cover.parent.mkdir(parents=True)
            old_cover.write_bytes(b'png-data')
            (root / 'metadata.pegasus.txt').write_text(
                'game: Alpha\n'
                'file: Alpha.gba\n'
                'description: Complete metadata\n'
                'assets.boxFront: old/alpha.png\n',
                encoding='utf-8',
            )
            extract_calls = []

            def extract_fn(*args, **kwargs):
                extract_calls.append((args, kwargs))
                return None

            batch_scrape(
                game_dir=root,
                extract_fn=extract_fn,
                file_extensions=('gba',),
                platform_id=5,
                platform_name='GBA',
                collection_defaults={},
                scrape_mode='complement',
                normalize_media_paths=True,
                log=lambda _message: None,
            )

            self.assertFalse(old_cover.exists())
            self.assertTrue(
                (root / 'media' / 'Alpha' / 'boxfront.png').is_file())
            self.assertIn(
                'assets.boxFront: media/Alpha/boxfront.png',
                (root / 'metadata.pegasus.txt').read_text(encoding='utf-8'),
            )
            self.assertEqual(extract_calls, [])

    def test_new_cover_uses_rom_name_and_creates_unindexed_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rom_path = root / 'New Game.gba'
            rom_path.write_bytes(b'rom')

            def extract_fn(*args, **kwargs):
                return {
                    'title': 'Different Scraped Title',
                    'title_en': 'English Title',
                    'filename': rom_path.name,
                    'game_code': 'TEST',
                    'icon_data': b'png-data',
                }

            batch_scrape(
                game_dir=root,
                extract_fn=extract_fn,
                file_extensions=('gba',),
                platform_id=5,
                platform_name='GBA',
                collection_defaults={},
                generate_meta=True,
                generate_gamelist=True,
                online_mode=False,
                normalize_media_paths=False,
                anbernic_compatible=True,
                thread_count=1,
                log=lambda _message: None,
            )

            self.assertEqual(
                (root / 'media' / 'New Game' / 'boxfront.png').read_bytes(),
                b'png-data',
            )
            self.assertEqual(
                (root / 'Imgs' / 'New Game.png').read_bytes(),
                b'png-data',
            )
            pegasus_text = (root / 'metadata.pegasus.txt').read_text(
                encoding='utf-8')
            self.assertIn(
                'assets.boxFront: media/New Game/boxfront.png',
                pegasus_text,
            )
            self.assertNotIn('Imgs/', pegasus_text)
            gamelist_text = (root / 'gamelist.xml').read_text(encoding='utf-8')
            self.assertIn('./media/New Game/boxfront.png', gamelist_text)
            self.assertNotIn('Imgs/', gamelist_text)


if __name__ == '__main__':
    unittest.main()

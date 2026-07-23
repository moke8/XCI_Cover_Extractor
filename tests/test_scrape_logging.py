import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scrape import batch_scrape, organize_existing_media


class ScrapeConfigurationLogTests(unittest.TestCase):
    def test_configuration_is_logged_before_scan_with_secrets_redacted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            messages = []

            batch_scrape(
                game_dir=Path(temp_dir),
                extract_fn=lambda *_args, **_kwargs: None,
                file_extensions=('gba', 'zip'),
                platform_id=5,
                platform_name='Game Boy Advance',
                collection_defaults={},
                generate_meta=True,
                generate_gamelist=False,
                online_mode=True,
                api_key='super-secret-key',
                datasource_name='thegamesdb',
                lang_code='zh',
                google_lang='zh-CN',
                translate=True,
                video=True,
                filename_as_title=False,
                thread_count=6,
                scrape_mode='complement',
                proxy='http://user:password@127.0.0.1:7890',
                normalize_media_paths=True,
                anbernic_compatible=True,
                log=messages.append,
            )

            text = '\n'.join(messages)
            self.assertIn('[刮削配置] 平台: Game Boy Advance', text)
            self.assertIn('[刮削配置] 游戏目录:', text)
            self.assertIn('[刮削配置] 模式: 补全', text)
            self.assertIn('[刮削配置] 文件格式: gba, zip', text)
            self.assertIn('在线补全: 开启', text)
            self.assertIn('数据源: TheGamesDB', text)
            self.assertIn('凭据: 已配置', text)
            self.assertIn('翻译: 开启', text)
            self.assertIn('视频: 开启', text)
            self.assertIn('线程: 6', text)
            self.assertIn('Pegasus: 开启', text)
            self.assertIn('gamelist.xml: 关闭', text)
            self.assertIn('强制保持图片目录统一: 开启', text)
            self.assertIn('兼容 Anbernic 封面: 开启', text)
            self.assertIn('代理: http://127.0.0.1:7890', text)
            self.assertIn('[文件扫描] 未找到游戏文件', text)
            self.assertNotIn('super-secret-key', text)
            self.assertNotIn('user:password', text)


class MediaOrganizationLogTests(unittest.TestCase):
    def test_migration_logs_each_action_and_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_cover = root / 'legacy' / 'covers' / 'alpha.png'
            old_cover.parent.mkdir(parents=True)
            old_cover.write_bytes(b'png-data')
            (root / 'metadata.pegasus.txt').write_text(
                'game: Alpha\n'
                'file: Alpha.gba\n'
                'assets.boxFront: legacy/covers/alpha.png\n',
                encoding='utf-8',
            )
            game_list = ET.Element('gameList')
            game = ET.SubElement(game_list, 'game')
            ET.SubElement(game, 'path').text = './Alpha.gba'
            ET.SubElement(game, 'image').text = './legacy/covers/alpha.png'
            ET.ElementTree(game_list).write(
                root / 'gamelist.xml', encoding='utf-8', xml_declaration=True)
            messages = []

            organize_existing_media(
                root,
                ['Alpha.gba'],
                normalize_paths=True,
                anbernic_compatible=True,
                log=messages.append,
            )

            text = '\n'.join(messages)
            self.assertIn('[图片整理] 开始检查 1 个游戏', text)
            self.assertIn('[图片整理] 已迁移: Alpha.gba', text)
            self.assertIn(
                'legacy/covers/alpha.png -> media/Alpha/boxfront.png', text)
            self.assertIn('[Pegasus] 已更新封面路径: Alpha.gba', text)
            self.assertIn('[gamelist] 已更新封面路径: Alpha.gba', text)
            self.assertIn('[Anbernic封面] 已复制: Alpha.gba', text)
            self.assertIn('Imgs/Alpha.png', text)
            self.assertIn('[图片整理] 已删除旧封面:', text)
            self.assertIn('[图片整理] 已删除空目录:', text)
            self.assertIn('[图片整理] 完成: 检查 1', text)
            self.assertIn('迁移 1', text)
            self.assertIn('Anbernic复制 1', text)


class ScrapeActionPrefixTests(unittest.TestCase):
    def test_scrape_flow_uses_single_business_prefixes(self):
        class FakeSource:
            display_name = 'FakeSource'

            def initialize(self, _api_key, _log):
                return True

            def fetch_metadata(self, *_args, **_kwargs):
                return {
                    'game_id': '123',
                    'description': 'Description',
                    'boxart_url': 'https://example.test/cover.jpg',
                    'youtube': 'https://example.test/video',
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rom_path = root / 'Prefix Game.gba'
            rom_path.write_bytes(b'rom')
            messages = []

            def extract_fn(*_args, **_kwargs):
                return {
                    'title': 'Prefix Game',
                    'title_en': 'Prefix Game',
                    'filename': rom_path.name,
                    'game_code': 'TEST',
                }

            with (
                patch('scrape.get_datasource', return_value=FakeSource()),
                patch('scrape._http_get_bytes', return_value=b'jpg-data'),
            ):
                batch_scrape(
                    game_dir=root,
                    extract_fn=extract_fn,
                    file_extensions=('gba',),
                    platform_id=5,
                    platform_name='Game Boy Advance',
                    collection_defaults={},
                    generate_meta=True,
                    generate_gamelist=True,
                    online_mode=True,
                    api_key='configured',
                    datasource_name='fake',
                    video=True,
                    thread_count=1,
                    normalize_media_paths=False,
                    log=messages.append,
                )

            text = '\n'.join(messages)
            expected = (
                '[文件扫描]',
                '[游戏解析]',
                '[游戏搜索]',
                '[图片下载]',
                '[元数据补全]',
                '[视频]',
                '[Pegasus]',
                '[gamelist]',
                '[刮削完成]',
            )
            for prefix in expected:
                self.assertIn(prefix, text)
            for old_prefix in ('[DEBUG]', '[DEBUG-search]', '[DEBUG-boxart]',
                               '[在线]', '[封面]', '[OK]', '[失败]'):
                self.assertNotIn(old_prefix, text)


if __name__ == '__main__':
    unittest.main()

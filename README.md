# 老顽头游戏助手

多平台游戏封面与元数据提取工具。通过解析游戏文件的二进制结构，精确提取游戏 ID、标题等信息，自动匹配在线数据库获取封面图片和元数据。

## 核心优势

**精确匹配，而非猜测。** 与依赖文件名匹配的工具不同，本工具直接解析游戏文件的内部结构——读取 XCI 文件中的 NACP 元数据、NDS ROM 的标题头——获取准确的游戏标题和 ID，再据此自动匹配 TheGamesDB / IGDB 等在线数据库，大幅降低匹配错误率。

## 支持平台

| 平台 | 文件格式 | 解析内容 |
|------|---------|---------|
| Nintendo Switch | `.xci` | 解密 HFS0/NCA/RomFS，提取 NACP 多语言标题、发行商、JPEG 封面 |
| Nintendo DS | `.nds` | 解析 ROM 头部，提取多语言标题、Game Code、发行商 |

所有平台均支持 ZIP 压缩包扫描——自动解压包含单个游戏文件的 ZIP。

## 功能一览

- **封面提取** — Switch 从 XCI 内部提取原始 JPEG 封面；NDS 从在线数据库下载高清 boxart（ROM 内置图标仅 32x32，作为离线回退）
- **在线元数据补全** — 支持 TheGamesDB、IGDB (Twitch)、Wikipedia 三种数据源，获取游戏简介、类型、评分、发行日期、发行商、视频等
- **多语言支持** — 16 种语言可选（含简繁中文智能识别），自动翻译非中文标题及在线元数据
- **视频支持** — 勾选"视频"后获取 YouTube 视频链接，写入元数据
- **多线程处理** — 可配置线程数（1~16，默认 4），并行解析加速批量处理
- **元数据输出** — 生成 `metadata.pegasus.txt`（Pegasus Frontend）和 `gamelist.xml`（Anbernic / EmulationStation）
- **代理支持** — HTTP/SOCKS 代理，方便网络受限环境使用
- **图形界面** — PySide6 构建，游戏展柜预览、日志实时输出

## 使用方法

### 直接运行（Python 3.8+）

```bash
pip install -r requirements.txt
python game_cover_extractor.py
```

### 使用编译好的 EXE

从 [Releases](../../releases) 下载 `XCI_Cover_Extractor.exe`，双击运行。

### 自行编译

```bash
pip install pyinstaller
pyinstaller XCI_Cover_Extractor.spec
```

产物在 `dist/XCI_Cover_Extractor.exe`。

## 界面说明

顶部全局设置栏：

| 控件 | 说明 |
|------|------|
| 语言 | 游戏标题语言偏好，默认跟随系统 |
| 线程 | 并发线程数，1~16，默认 4 |
| 在线补全 | 开启后显示以下选项 |
| 视频 | 获取游戏视频链接（YouTube） |
| 翻译 | Google Translate 翻译标题及元数据 |
| 代理 | HTTP 代理地址，如 `http://127.0.0.1:7890` |
| 数据源 | TheGamesDB / IGDB (Twitch) / Wikipedia |
| API Key | TheGamesDB 填 API Key；IGDB 填 `client_id:client_secret` |

每个平台独立 Tab 页，设置游戏目录（Switch 还需 `prod.keys`），勾选输出格式后点击"开始提取"。

## 输出结构

```
<游戏目录>/
├── images/
│   ├── 游戏标题A.jpg
│   ├── 游戏标题B.jpg
│   └── ...
├── metadata.pegasus.txt   (可选)
└── gamelist.xml           (可选)
```

## 项目结构

```
game_cover_extractor.py    # 主程序、共享 GUI、公共工具函数
platform_switch.py         # Nintendo Switch 平台模块
platform_nds.py            # Nintendo DS 平台模块
datasource.py              # 数据源基类、注册表、网络工具
datasource_thegamesdb.py   # TheGamesDB 数据源
datasource_igdb.py         # IGDB (Twitch) 数据源
datasource_wikipedia.py    # Wikipedia 数据源
```

添加新平台：创建 `platform_xxx.py`，实现 `PlatformTab` 和 `batch_extract`，在 `game_cover_extractor.py` 的 `_load_platforms()` 中注册。

添加新数据源：创建 `datasource_xxx.py`，继承 `DataSource`，调用 `register_datasource()`，在 `datasource.py` 底部 import。

## 依赖

- Python 3.8+
- PySide6
- pycryptodome（Switch XCI 解密）

## License

MIT

## 作者

mokevip | QQ 652831080 | [GitHub](https://github.com/moke8/XCI_Cover_Extractor)

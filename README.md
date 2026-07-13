# XCI Cover Extractor

从 Nintendo Switch XCI 文件中批量提取游戏封面图片，自动从TheGamesDB匹配元数据，并可选生成 Pegasus Frontend / Anbernic (EmulationStation) 元数据文件。

## 功能

- 批量解密并提取 XCI 文件中的游戏封面 (JPEG)
- 自动读取游戏标题、发行商（支持 16 种语言，含简繁中文智能识别）
- 可选生成 `metadata.pegasus.txt`（Pegasus Frontend 格式）
- 可选生成 `gamelist.xml`（Anbernic / EmulationStation 格式）
- 在线元数据补全（TheGamesDB / Wikipedia）
- Google Translate 自动翻译在线元数据到所选语言
- 支持 HTTP/SOCKS 代理
- 图形界面，一键操作

## 截图

启动后显示设置界面，填入游戏目录和 prod.keys 即可开始提取。

## 使用方法

### 直接运行（需 Python 3.8+）

```bash
pip install pycryptodome
python xci_cover_extractor.py
```

### 使用编译好的 EXE

从 [Releases](../../releases) 下载 `XCI_Cover_Extractor.exe`，双击运行即可，无需安装 Python。

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| 游戏目录 | 是 | 存放 .xci 文件的文件夹 |
| prod.keys | 是 | Switch 密钥文件 |
| 语言 | 否 | 游戏标题语言偏好，默认跟随系统 |
| 生成 Pegasus 元数据 | 否 | 输出 `metadata.pegasus.txt` |
| 生成 Anbernic gamelist | 否 | 输出 `gamelist.xml` |
| 在线补全元数据 | 否 | 从 TheGamesDB/Wikipedia 获取描述等信息 |
| 翻译在线元数据 | 否 | 通过 Google Translate 翻译到所选语言 |
| 代理 | 否 | HTTP 代理地址，如 `http://127.0.0.1:7890` |
| API Key | 否 | TheGamesDB API Key（无则使用 Wikipedia） |

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

## 自行编译 EXE

```bash
pip install pyinstaller pycryptodome
pyinstaller --onefile --windowed --name XCI_Cover_Extractor xci_cover_extractor.py
```

产物在 `dist/XCI_Cover_Extractor.exe`。

## 依赖

- Python 3.8+
- pycryptodome

## License

MIT

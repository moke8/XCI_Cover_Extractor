# 刮削结构化日志 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Steps use checkbox syntax for tracking.

**Goal:** 为全部刮削入口增加完整、脱敏、单业务前缀的可追踪日志。

**Architecture:** 在 `scrape.py` 集中生成启动配置日志，避免 UI 入口重复和遗漏；媒体整理函数维护统计并直接记录文件动作；刮削调度、数据源和平台解析器统一业务前缀。

**Tech Stack:** Python 3.10、标准库 `urllib.parse`、`unittest`、PyInstaller。

---

### Task 1: 启动配置日志

**Files:** `tests/test_scrape_logging.py`, `scrape.py`, `platform_base.py`

- [ ] 写失败测试：调用空目录上的 `batch_scrape()`，断言扫描失败前已输出所有 `[刮削配置]` 行，API 密钥不出现，认证代理只显示协议/主机/端口。
- [ ] 运行 `python -m unittest tests.test_scrape_logging.ScrapeConfigurationLogTests -v`，确认因配置日志缺失而失败。
- [ ] 实现 `_safe_proxy_display()`、`_enabled_text()` 与 `_log_scrape_configuration()`，并在 `batch_scrape()` 最开始调用。
- [ ] 删除 UI 入口重复的代理日志，右键入口改用 `[刮削]`。
- [ ] 重跑测试确认通过。

### Task 2: 图片整理明细和统计

**Files:** `tests/test_scrape_logging.py`, `scrape.py`

- [ ] 写失败测试：构造 Pegasus/gamelist 旧图片并开启两个选项，断言日志包含整理开始、迁移、两个索引更新、Anbernic 复制、旧文件/空目录清理与汇总。
- [ ] 运行目标测试确认失败。
- [ ] 让 `_prune_empty_parents()` 返回删除目录列表；在 `organize_existing_media()` 增加统计字典和逐动作日志。
- [ ] 保持现有冲突保护、共享源与索引失败时保留旧源行为。
- [ ] 运行媒体和日志测试确认通过。

### Task 3: 全调用链单业务前缀

**Files:** `scrape.py`, `datasource_thegamesdb.py`, `datasource_igdb.py`, `datasource_screenscraper.py`, `platform_*.py`, `tests/test_scrape_logging.py`

- [ ] 写失败测试：模拟搜索结果和内置图片，断言出现 `[游戏解析]`、`[游戏搜索]`、`[图片下载]`、`[元数据补全]`、`[Pegasus]`、`[gamelist]`、`[刮削完成]`，且不含旧前缀。
- [ ] 将搜索、下载、翻译、视频、索引和结束日志替换为单业务前缀；数据源内部日志改用对应业务前缀。
- [ ] 将平台解析器中的状态前缀统一为 `[游戏解析]`，手动搜索 UI 日志统一为 `[游戏搜索]` 或 `[游戏解析]`。
- [ ] 运行全部测试并用 `rg` 确认旧前缀不再出现在运行时代码。

### Task 4: 验证和重新打包

**Files:** `README.md`, `XCI_Cover_Extractor.spec`

- [ ] 在 README 说明启动配置与日志前缀。
- [ ] 运行 `python -m py_compile`、`python -m unittest discover -s tests -v` 和 `git diff --check`。
- [ ] 运行 `pyinstaller --noconfirm --clean XCI_Cover_Extractor.spec`。
- [ ] 验证 EXE 存在、读取 SHA256，并短暂启动冒烟测试。

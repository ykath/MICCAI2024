# MICCAI 2024 Paper Browser

MICCAI 2024 Paper Browser 是一个面向 [MICCAI 2024 Open Access](https://papers.miccai.org/miccai-2024/) 论文集的本地检索与浏览项目。项目已构建本地 SQLite 数据库，包含论文元信息、摘要、评审、Meta-review、中文翻译和 PDF 本地路径，可通过浏览器进行搜索、分页查看和 PDF 阅读。

## 项目内容

- `data/miccai2024.sqlite`：本地论文数据库，包含 856 篇 MICCAI 2024 论文。
- `browse_papers.py`：本地 HTTP 服务与 JSON API。
- `web/index.html`：论文检索与详情浏览前端。
- `build_database.py`：从 MICCAI 页面抓取并构建数据库。
- `translate_papers.py`：使用 DeepSeek OpenAI-compatible API 翻译摘要、评审和 Meta-review。
- `download_pdfs.py`：批量下载 PDF，并回写本地 PDF 路径。
- `start_service.bat`：Windows 一键启动脚本。

## 数据状态

- 论文总数：856
- PDF 下载状态：856 / 856
- 中文摘要：856 / 856
- 中文评审：855 / 855
- 中文 Meta-review：855 / 855

说明：`pdfs/` 目录包含约 2.95GB PDF 文件，未纳入 Git；`cache/` 目录为抓取缓存，也未纳入 Git。

## 快速启动

Windows 下可直接双击：

```bat
start_service.bat
```

或在命令行指定端口：

```bat
start_service.bat 8888
```

默认地址：

```text
http://127.0.0.1:8766/
```

也可以手动启动：

```powershell
python browse_papers.py --port 8766
```

## API

- `GET /api/papers?limit=100&offset=0&q=keyword`：分页检索论文列表。
- `GET /api/paper?paper_id=1861`：获取单篇论文详情。
- `GET /api/facets`：获取分类、卷号等筛选项。
- `GET /api/stats`：获取数据库统计信息。

搜索字段覆盖标题、作者、英文摘要、中文摘要、英文评审、中文评审、Meta-review 和分类。

## 重新构建数据

安装依赖：

```powershell
pip install -r requirements.txt
```

重建数据库：

```powershell
python build_database.py --refresh --delay 0
```

下载 PDF：

```powershell
python download_pdfs.py --workers 10 --delay 0
```

翻译中文字段：

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
python translate_papers.py --workers 6 --timeout 600
```

`translate_papers.py` 默认使用：

- API Base：`https://api.deepseek.com`
- Model：`deepseek-v4-flash`

## Git 忽略策略

以下内容不会提交到 Git：

- `pdfs/`
- `cache/`
- Python 缓存和测试缓存
- SQLite sidecar 文件
- 日志、临时文件、`.env` 和本地虚拟环境


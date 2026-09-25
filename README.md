# 字幕校稿对齐（Subtitle Proofreading Aligner）

把**逐行对白稿**与 **SRT 字幕**自动对位的 Web 小工具：解析并保留 SRT 时间码，
对两侧文本做去空白/去标点的归一化，以**归一化字符编辑距离**为匹配代价、
**跳过任一条计 0.45**，求**全局单调、最低代价**的对齐。两侧条目各最多匹配一次、
允许缺项；重复短句也严格按整段顺序匹配。可填写**人工锚点**把确定对应的
「行:块」强制配对，锚点之间仍自动对齐。页面并排显示对位/缺项/锚点行与时间码，
可一键下载含全部状态的 CSV。

后端 Python + FastAPI，前端原生 HTML / CSS / JavaScript（无框架、无构建步骤）。

---

## 一键启动（推荐：Docker Compose）

需要本机已安装 Docker 与 Docker Compose（`docker compose version` 可用）。

```bash
docker compose up -d --build
```

启动后在浏览器打开：

```
http://localhost:8000
```

停止：

```bash
docker compose down
```

### 修改端口

默认占用宿主机 **8000** 端口。改为其它端口有两种方式：

```bash
# 方式一：环境变量临时覆盖
APP_PORT=9000 docker compose up -d --build

# 方式二：复制 .env.example 为 .env 后修改 APP_PORT
cp .env.example .env
# 编辑 .env：APP_PORT=9000
docker compose up -d --build
```

容器内固定监听 8000，仅宿主机映射端口可改（`docker-compose.yml` 中
`"${APP_PORT:-8000}:8000"` 冒号左侧）。

---

## 不用 Docker，本地直接运行

需要 Python 3.11+。

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 。

---

## 使用方法

1. 左侧文本框粘贴**逐行对白稿**（每行一句，空行忽略）。
2. 右侧文本框粘贴标准 **SRT**（`序号 / 时间码入点 --> 出点 / 字幕文本`，块间空行分隔）。
3. 可调整「跳过单条代价」（默认 **0.45**，范围 0~1）。
4. （可选）在「**人工锚点**」框填写若干 `行:块` 强制配对，详见
   [下一节](#人工锚点强制配对)。
5. 点 **开始对齐**，下方并排展示结果表：
   - **对位**（绿）：对白稿行与字幕行成功匹配，显示逐行匹配代价；
   - **缺对白稿**（红）：SRT 有该字幕、对白稿缺这一条；
   - **缺字幕**（黄）：对白稿有这句、SRT 中缺失；
   - **锚点**（紫色徽标/底色）：人工指定的强制配对行。
   - 每行都带有对白稿行号、SRT 序号与保留下来的**入点/出点时间码**。
6. 点 **下载 CSV** 导出含状态、代价、行号、文本、时间码的结果（UTF-8 带 BOM，
   Excel 直接打开不乱码）；提供锚点时 CSV 末尾增加「锚点」列标出锚点行。

页面右上/输入框标题旁的「填入示例」可快速体验，其中特意包含缺项与重复短句。

---

## 人工锚点（强制配对）

自动对齐在个别位置不符合预期时，可人工指定若干锚点把确定对应的两行强行配对：

- **输入格式**：`对白稿行序位:SRT块序位`，多对之间用逗号、分号或空格分隔，
  冒号中英文均可，例如 `3:3, 10:8`、`1：1 6：5`。
- **序位定义**：均从 **1** 开始，指**解析后**条目在各自序列中的序位——
  对白稿即非空行的行号，SRT 即字幕块的块序位（与结果表两列 `#` 一致）。
- **校验规则**：每对必须在两侧实际条目范围内；两列序位都必须**严格递增**。
  越界、重复、交叉（一列序位倒退）都会返回 **400** 并给出具体原因
  （如 `锚点无效：第 2 个锚点 (3:2) 与前一锚点 (1:3) 交叉：两列序位必须同时严格递增`）。
- **对齐方式**：首锚点之前、相邻锚点之间、末锚点之后的各段，仍分别按现有的
  最低代价单调对齐；锚点本身作为强制配对行按顺序拼接——**即使两侧文本差异
  很大（代价 1.0）也绝不跳过锚点**。锚点之外的其他条目照常可能显示为
  「缺对白稿 / 缺字幕」。
- **结果标识**：结果表中锚点行带紫色「锚点」徽标与底色，汇总区显示锚点数；
  CSV 导出额外增加「锚点」列，锚点行标记为 `锚点`。
- **未提供锚点时，对齐结果、接口字段与 CSV 表头均与原来完全一致。**

页面与接口均在同一地址：启动后浏览器打开 **http://localhost:8000**
（端口可改，见上文「修改端口」，例如 `APP_PORT=9000 docker compose up -d --build`
后访问 http://localhost:9000 ）。

API 调用时 `/api/align` 与 `/api/export.csv` 均接受可选的 `anchors` 数组：

```bash
curl -s -X POST http://localhost:8000/api/align \
  -H 'Content-Type: application/json' \
  -d '{"script":"第一行\n第二行\n第三行",
       "srt":"1\n00:00:01,000 --> 00:00:02,000\n字幕甲\n\n2\n00:00:03,000 --> 00:00:04,000\n字幕乙\n",
       "anchors":[{"script":1,"srt":2}]}'
```

---

## 对齐算法说明

对两侧条目序列 `A`（对白稿）、`B`（SRT）做 Needleman–Wunsch 风格的全局序列对齐：

- 文本归一化：Unicode NFKC（全角→半角等）+ 小写折叠（casefold），
  再剔除所有**空白**与**标点**（Unicode 类别 Z / P / C），中日韩文字原样保留。
  因此 `你好，世界！` 与 `你好世界` 视为完全相同（代价 0）。
- 匹配代价：`编辑距离(归一化A, 归一化B) / max(长度A, 长度B)`，范围 0~1。
- 跳过代价：任一条不参与匹配，计固定 **0.45**（可在页面调整）。
- 动态规划在整张 `(n+1)×(m+1)` 表上取全局最小值，保证：
  - **全局单调**：对位顺序与两侧原始顺序一致，不交叉；
  - **每条最多匹配一次**，两侧都允许缺项；
  - 重复短句依据其在整段中的先后顺序依次对位，不会都吸到同一条。

距离矩阵由 rapidfuzz(C++) 批量计算、DP 使用 numpy；缺少 numpy 时自动回退到
纯 Python 实现（结果完全一致，仅速度较慢）。

### 规模与限制

- 每侧最多 **2000 条**（单次请求文本各 2 MB 内）；超出会返回 400 并提示拆分。
  常规影视剧字幕（数百至约一千多条）秒级返回。
- 该工具为本地/内网校稿辅助，默认无需鉴权；如需暴露到公网请自行放在
  反向代理与鉴权之后。

---

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查，返回 `{"status":"ok"}` |
| POST | `/api/align` | 入参 `{"script","srt","gap_cost"?,"anchors"?}`，返回 `summary` 与逐行 `rows` |
| POST | `/api/export.csv` | 同入参，直接下载对齐结果 CSV |

`anchors` 为可选的人工锚点数组：`[{"script": 3, "srt": 3}, {"script": 10, "srt": 8}]`，
元素为解析后 1 起始的「对白稿行序位 / SRT 块序位」，两列须严格递增，
非法锚点返回 400。详见上文「人工锚点（强制配对）」。

`rows` 中每行字段：`status`（`match` / `missing_script` / `missing_srt`）、
`cost`、`script_index`、`script_text`、`srt_index`、`srt_text`、
`start_tc`、`end_tc`；提供锚点时，对位行额外带 `anchor` 布尔字段
（锚点行为 `true`），`summary` 额外带 `anchors` 计数，CSV 增加「锚点」列。

快速验证：

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/api/align \
  -H 'Content-Type: application/json' \
  -d '{"script":"你好世界\n再见","srt":"1\n00:00:01,000 --> 00:00:02,000\n你好世界\n"}'
```

---

## 项目结构

```
.
├── app/
│   ├── main.py        # FastAPI：/api/align、/api/export.csv、静态页托管
│   ├── alignment.py   # 全局单调对齐 DP（numpy 实现 + 纯 Python 回退）
│   ├── parsers.py     # 逐行对白稿与 SRT 解析（保留时间码）
│   └── textnorm.py    # 归一化与归一化字符编辑距离
├── static/
│   ├── index.html     # 页面结构
│   ├── style.css
│   └── app.js         # 前端交互（fetch 调接口、渲染、下载）
├── tests/             # 单元 + 接口测试
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 运行测试

```bash
pip install -r requirements.txt pytest httpx
python -m pytest tests/ -q
# 无需 pytest 时也可直接运行算法层测试：
python -m tests.test_alignment
```

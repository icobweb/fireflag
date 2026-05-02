# FireFlag - 富途股票分析系统

基于富途(Futu) OpenAPI 的股票分析系统，全量扫描 A 股 + 港股，自动筛选多种技术形态。

## 目录结构

```
fireflag/
├── run_scan.py                     # 一键全量扫描启动器 (CLI)
├── cline_mcp/
│   ├── futu_core.py                # 核心引擎：指标函数 + FutuClient
│   ├── futu_mcp_server.py          # MCP 服务器 (导入 futu_core)
│   └── stock_data_cache/           # K线缓存目录
├── README.md
└── .gitignore
```

## 快速使用

```bash
# 全量扫描 A 股(沪+深) + 港股
python3 run_scan.py

# 指定市场
python3 run_scan.py SH SZ HK

# 单市场
python3 run_scan.py HK
```

## 全量扫描功能

每次扫描自动执行 **4种策略**，结果自动保存到文件：

| # | 策略 | 来源 | 说明 |
|:-:|------|------|------|
| 1️⃣ | **5日线回踩10日线确认** | Check_Tu50.py | MA5 金叉 MA10 后回踩确认 |
| 2️⃣ | **MACD 底背离** | checkFt.py | 价格新低但 MACD 未新低 |
| 3️⃣ | **底部筛选** | Check_Tu50_q.py | 日周月三底共振 / 纯日线回踩 |
| 4️⃣ | **追高筛选** | Check_Qiang.py | 主升浪突破 / 波段加速 |

### 参数说明

- **BATCH_SIZE = 250**（每批 250 只，250×1 ≤ 300 额度上限）
- **无数量限制**：扫描所有有交易量的股票
- **结果文件**：
  - `selected_{市场}_{日期}_{时间}.txt` — 简略版（终端可读）
  - `selected_{市场}_{日期}_{时间}.json` — 完整版（全部结果 JSON）

### 每日扫描示例输出

```text
$ python3 run_scan.py

======================================================================
 🔥 SH 全量扫描 (批次250/批)
======================================================================
  总股票: 2373  |  快照后: 2324
  全部扫描: 2324只 | K线获取: 2317只

  [1] 🔵 5日线回踩10日线确认: 183只
       SH.600020  评分:35.0  收盘:4.04  MA5:3.824  MA10:3.818
       ...
  [2] 🟣 MACD底背离: 126只
    SH.600007, SH.600012, ...
  [3] 🟢 底部筛选: 0只
  [4] 🔴 追高筛选: 0只
  📄 完整结果已保存: selected_SH_20260502_180000.json
  📄 简略结果已保存: selected_SH_20260502_180000.txt
```

## MCP 服务器

### 配置

在 Cline MCP 设置中添加：

```json
{
  "mcpServers": {
    "futu-stock": {
      "command": "python3",
      "args": ["/path/to/fireflag/cline_mcp/futu_mcp_server.py"],
      "env": {},
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

### MCP 工具列表

| 工具 | 功能 |
|------|------|
| `get_stock_list` | 获取市场股票列表 |
| `get_kline` | 获取K线数据(日/周/月) |
| `get_market_snapshot` | 实时行情快照 |
| `scan_all` | **全市场4策略扫描** |
| `backtest_stock` | 单股历史回测验证 |

> MCP 连接后可直接对 Cline 说："帮我扫描今天的A股和港股"

## 架构说明

```
futu_core.py  (核心模块: 指标函数 + FutuClient)
      ↕
futu_mcp_server.py   ← →  run_scan.py
(MCP协议层)               (CLI启动器)
```

- `futu_core.py` — 所有技术指标和 FutuClient 封装，独立可导入
- `futu_mcp_server.py` — 仅 MCP 协议层，Cline 通过它调用
- `run_scan.py` — CLI 模式，直接调用 futu_core，适合每日定时扫描

## 前提条件

1. **启动 Futu OpenD**（默认 127.0.0.1:11111）
2. **安装依赖**：
   ```bash
   pip install futu mcp numpy pandas
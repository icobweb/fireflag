# FireFlag - 富途股票分析系统

基于富途(Futu) OpenAPI 的股票分析 MCP 服务器，集成多种技术指标筛选策略。

## 目录结构

```
fireflag/
├── cline_mcp/                    # MCP 服务器代码
│   ├── futu_mcp_server.py        # 主服务器 (Python MCP Server)
│   └── stock_data_cache/         # K线数据缓存目录
├── README.md
└── .gitignore
```

## 功能

MCP 服务器提供 8 个工具：

| 工具 | 功能 | 来源 |
|------|------|------|
| `get_stock_list` | 获取市场股票列表 | 基础API |
| `get_kline` | 获取K线数据(日/周/月) | 基础API |
| `get_market_snapshot` | 实时行情快照 | 基础API |
| `scan_bottom` | **底部筛选**: 日周月三底共振/回踩 | Check_Tu50_q.py |
| `scan_strong` | **追高筛选**: 主升浪突破/加速 | Check_Qiang.py |
| `scan_pullback` | **5日线回踩10日线选股** | Check_Tu50.py |
| `backtest_stock` | **单股历史回测验证** | Check_Tu50.py |
| `scan_divergence` | **MACD底背离扫描** | checkFt.py |

## 使用前提

1. 运行 Futu OpenD (默认 127.0.0.1:11111)
2. 安装 Python 依赖:
   ```bash
   pip install futu mcp numpy pandas
   ```

## MCP 配置

在 Cline MCP 设置中添加:

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
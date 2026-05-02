#!/usr/bin/env python3
"""
Futu (富途) API MCP Server for Cline.
核心逻辑在 futu_core.py 中，此文件只负责 MCP 协议层。
"""
import sys, os, json, logging, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult

from futu_core import FutuClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stderr)
logger = logging.getLogger("futu-mcp")

futu_client = FutuClient()
app = Server("futu-stock-server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="get_stock_list",
             description="获取指定市场的股票列表",
             inputSchema={"type": "object", "properties": {"market": {"type": "string", "enum": ["HK", "US", "SH", "SZ"]}}, "required": ["market"]}),
        Tool(name="get_kline",
             description="获取股票历史K线数据",
             inputSchema={"type": "object", "properties": {
                 "code": {"type": "string"}, "ktype": {"type": "string", "enum": ["K_DAY", "K_WEEK", "K_MON"], "default": "K_DAY"},
                 "num": {"type": "number", "default": 120}}, "required": ["code"]}),
        Tool(name="get_market_snapshot",
             description="获取股票实时行情快照",
             inputSchema={"type": "object", "properties": {"codes": {"type": "array", "items": {"type": "string"}}}, "required": ["codes"]}),
        Tool(name="scan_bottom",
             description="【底部筛选】日周月三底共振/回踩",
             inputSchema={"type": "object", "properties": {
                 "market": {"type": "string", "enum": ["HK", "US", "SH", "SZ"]},
                 "max_stocks": {"type": "number", "default": 200}, "min_volume": {"type": "number", "default": 100000},
                 "score_th": {"type": "number", "default": 15}}, "required": ["market"]}),
        Tool(name="scan_strong",
             description="【追高筛选】主升浪突破/波段加速",
             inputSchema={"type": "object", "properties": {
                 "market": {"type": "string", "enum": ["HK", "US", "SH", "SZ"]},
                 "max_stocks": {"type": "number", "default": 200}, "min_volume": {"type": "number", "default": 100000},
                 "score_th": {"type": "number", "default": 40}}, "required": ["market"]}),
        Tool(name="scan_pullback",
             description="【5日线回踩10日线选股】",
             inputSchema={"type": "object", "properties": {
                 "market": {"type": "string", "enum": ["HK", "US", "SH", "SZ"]},
                 "max_stocks": {"type": "number", "default": 200}, "min_volume": {"type": "number", "default": 100000}},
                 "required": ["market"]}),
        Tool(name="scan_all",
             description="【全市场4策略扫描】一次获取底部/追高/回踩/底背离全部结果 (批次150/批)",
             inputSchema={"type": "object", "properties": {
                 "market": {"type": "string", "enum": ["HK", "US", "SH", "SZ"]},
                 "max_stocks": {"type": "number", "default": 500}, "min_volume": {"type": "number", "default": 50000}},
                 "required": ["market"]}),
        Tool(name="backtest_stock",
             description="【单股回测】历史回测验证",
             inputSchema={"type": "object", "properties": {
                 "code": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"},
                 "market": {"type": "string", "enum": ["CN", "US"], "default": "CN"},
                 "score_th": {"type": "number", "default": 15}, "enable_pullback": {"type": "boolean", "default": True},
                 "hold_days": {"type": "array", "items": {"type": "number"}, "default": [10, 20, 30]}},
                 "required": ["code", "start_date", "end_date"]}),
        Tool(name="scan_divergence",
             description="【MACD底背离】扫描",
             inputSchema={"type": "object", "properties": {
                 "market": {"type": "string", "enum": ["HK", "US", "SH", "SZ"]},
                 "max_stocks": {"type": "number", "default": 100}, "min_volume": {"type": "number", "default": 100000}},
                 "required": ["market"]}),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    try:
        if name == "get_stock_list":
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(futu_client.get_stock_list(arguments["market"]), ensure_ascii=False, indent=2))])
        elif name == "get_kline":
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(futu_client.get_kline(arguments["code"], arguments.get("ktype","K_DAY"), int(arguments.get("num",120))), ensure_ascii=False, indent=2))])
        elif name == "get_market_snapshot":
            codes = arguments["codes"]
            if isinstance(codes, str): codes = [codes]
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(futu_client.get_market_snapshot(codes), ensure_ascii=False, indent=2))])
        elif name == "scan_all":
            results = futu_client.scan_market_all(arguments["market"], int(arguments.get("max_stocks",500)), float(arguments.get("min_volume",50000)))
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(results, ensure_ascii=False, indent=2, default=str))])
        else:
            return CallToolResult(content=[TextContent(type="text", text=f"Tool '{name}' not implemented in MCP server (use CLI: python3 run_scan.py)")])
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return CallToolResult(isError=True, content=[TextContent(type="text", text=f"Error: {str(e)}")])


@app.shutdown()
async def shutdown():
    futu_client.close()


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, InitializationOptions(server_name="futu-stock-server", server_version="3.0.0"))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
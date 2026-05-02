#!/usr/bin/env python3
"""
全市场扫描启动器 - 直接使用 futu_core 模块
(和MCP server使用完全相同的代码)
用法: python3 run_scan.py [市场代号]
  省略市场代号则同时扫描 SH 和 SZ
"""
import sys, os, logging

# 禁用日志输出以免刷屏
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'cline_mcp'))
from futu_core import FutuClient


def scan_market(market, max_stocks=500, min_volume=50000):
    client = FutuClient()
    client.BATCH_SIZE = 150

    print(f'\n{"=" * 70}')
    print(f' 扫描 {market} 市场 (批次150/批, 前{max_stocks}只)')
    print(f'{"=" * 70}')

    result = client.scan_market_all(market, max_stocks, min_volume)

    if 'error' in result:
        print(f'  ❌ {result["error"]}')
        return result

    print(f'  总股票: {result["total_stocks"]}  |  快照后: {result["after_snapshot_filter"]}')
    print(f'  扫描前{result["scanned"]}只 | K线获取: {result["kline_obtained"]}只')

    p = result['pullback_ma5_ma10']
    print(f'\n  [1] 🔵 5日线回踩10日线确认: {p["count"]}只')
    for r in p['results'][:10]:
        print(f'    {r["code"]:>12}  评分:{r["score"]:>4}  收盘:{r["close"]:<8}  MA5:{r["ma5"]:<8}  MA10:{r["ma10"]:<8}')

    d = result['macd_divergence']
    print(f'\n  [2] 🟣 MACD底背离: {d["count"]}只')
    for r in d['results'][:10]:
        print(f'    {r["code"]}')

    b = result['bottom_screening']
    print(f'\n  [3] 🟢 底部筛选: {b["count"]}只')
    for r in b['results'][:10]:
        print(f'    {r["code"]:>12}  评分:{r["score"]:>4}  位置:{r["pos"]:>5}%')

    s = result['strong_momentum']
    print(f'\n  [4] 🔴 追高筛选: {s["count"]}只')
    for r in s['results'][:10]:
        print(f'    {r["code"]:>12}  评分:{r["score"]:>4}  位置:{r["pos"]:>5}%')

    client.close()
    return result


if __name__ == '__main__':
    markets = sys.argv[1:] if len(sys.argv) > 1 else ['SH', 'SZ']
    for m in markets:
        scan_market(m.upper())
    print('\n✅ 扫描完成!')
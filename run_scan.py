#!/usr/bin/env python3
"""
全市场扫描启动器 - BATCH_SIZE=250, 全量扫描, 自动保存结果到文件
用法: python3 run_scan.py [SH] [SZ] [HK] [US]
  省略参数则同时扫描 SH SZ HK (A股+港股)

结果文件:
  selected_{市场}_{日期}_{时间}.txt - 简略版(终端输出格式)
  selected_{市场}_{日期}_{时间}.json - 完整版(全部结果JSON)
"""
import sys, os, json, logging, time
from datetime import datetime

logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'cline_mcp'))
from futu_core import FutuClient


def save_results(market, result):
    """保存结果到文件"""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    base = f'selected_{market}_{ts}'

    # 保存完整JSON
    json_path = f'{base}.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f'  📄 完整结果已保存: {json_path}')

    # 保存简略TXT
    txt_path = f'{base}.txt'
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(f'市场: {market}\n')
        f.write(f'扫描时间: {result.get("scan_time", "")}\n')
        f.write(f'总股票: {result["total_stocks"]}  |  快照后: {result["after_snapshot_filter"]}\n')
        f.write(f'全部扫描: {result["scanned"]}只  |  K线获取: {result["kline_obtained"]}只\n\n')

        p = result['pullback_ma5_ma10']
        f.write(f'=== 1. 5日线回踩10日线确认 ({p["count"]}只) ===\n')
        f.write(f'{"代码":<20} {"评分":>5} {"收盘":>8} {"MA5":>8} {"MA10":>8}\n')
        f.write('-' * 50 + '\n')
        for r in p['results']:
            f.write(f'{r["code"]:<20} {r["score"]:>5} {r["close"]:>8} {r["ma5"]:>8} {r["ma10"]:>8}\n')

        d = result['macd_divergence']
        f.write(f'\n=== 2. MACD底背离 ({d["count"]}只) ===\n')
        for r in d['results']:
            f.write(f'{r["code"]}\n')

        b = result['bottom_screening']
        f.write(f'\n=== 3. 底部筛选 ({b["count"]}只) ===\n')
        f.write(f'{"代码":<20} {"评分":>5} {"位置":>6}\n')
        f.write('-' * 32 + '\n')
        for r in b['results']:
            f.write(f'{r["code"]:<20} {r["score"]:>5} {r["pos"]:>5}%\n')

        s = result['strong_momentum']
        f.write(f'\n=== 4. 追高筛选 ({s["count"]}只) ===\n')
        f.write(f'{"代码":<20} {"评分":>5} {"位置":>6}\n')
        f.write('-' * 32 + '\n')
        for r in s['results']:
            f.write(f'{r["code"]:<20} {r["score"]:>5} {r["pos"]:>5}%\n')

    print(f'  📄 简略结果已保存: {txt_path}')
    return json_path, txt_path


def scan_market(market, min_volume=50000):
    client = FutuClient()
    client.BATCH_SIZE = 250

    print(f'\n{"=" * 70}')
    print(f' 🔥 {market} 全量扫描 (批次{client.BATCH_SIZE}/批)')
    print(f'{"=" * 70}')

    result = client.scan_market_all(market, min_volume)

    if 'error' in result:
        print(f'  ❌ {result["error"]}')
        return result

    print(f'  总股票: {result["total_stocks"]}  |  快照后: {result["after_snapshot_filter"]}')
    print(f'  全部扫描: {result["scanned"]}只 | K线获取: {result["kline_obtained"]}只')

    p = result['pullback_ma5_ma10']
    print(f'\n  [1] 🔵 5日线回踩10日线确认: {p["count"]}只')
    for r in p['results'][:10]:
        print(f'    {r["code"]:>12}  评分:{r["score"]:>4}  收盘:{r["close"]:<8}  MA5:{r["ma5"]:<8}  MA10:{r["ma10"]:<8}')
    if p['count'] > 10:
        print(f'    ... 还有 {p["count"]-10} 只')

    d = result['macd_divergence']
    print(f'\n  [2] 🟣 MACD底背离: {d["count"]}只')
    for r in d['results'][:10]:
        print(f'    {r["code"]}')
    if d['count'] > 10:
        print(f'    ... 还有 {d["count"]-10} 只')

    b = result['bottom_screening']
    print(f'\n  [3] 🟢 底部筛选: {b["count"]}只')
    for r in b['results'][:10]:
        print(f'    {r["code"]:>12}  评分:{r["score"]:>4}  位置:{r["pos"]:>5}%')

    s = result['strong_momentum']
    print(f'\n  [4] 🔴 追高筛选: {s["count"]}只')
    for r in s['results'][:10]:
        print(f'    {r["code"]:>12}  评分:{r["score"]:>4}  位置:{r["pos"]:>5}%')

    # 保存文件
    json_path, txt_path = save_results(market, result)

    client.close()
    return result


if __name__ == '__main__':
    markets = [m.upper() for m in sys.argv[1:]] if len(sys.argv) > 1 else ['SH', 'SZ', 'HK']
    for m in markets:
        scan_market(m)
    print(f'\n{"=" * 70}')
    print('✅ 全部扫描完成!')
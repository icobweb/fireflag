#!/usr/bin/env python3
"""
Futu (富途) API MCP Server - 综合股票分析系统 for Cline.

集成自现有代码:
  - Check_Tu50_q.py: 底部筛选(日周月三底共振)
  - Check_Qiang.py:  强势追高筛选(趋势突破)
  - Check_Tu50.py:   回踩确认 + 历史回测
  - checkFt.py:      MACD底背离扫描
"""

import sys
import time
import json
import logging
import traceback
import os
import pickle
from typing import Any
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent, CallToolResult

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("futu-mcp-server")

FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

# Safe __file__ handling for -c mode
_script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.path.expanduser("~")
CACHE_DIR = os.path.join(_script_dir, "stock_data_cache")
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# ============================================================
# 公用技术指标函数
# ============================================================

def td_sma(series, n, m):
    result = np.zeros(len(series))
    if len(series) == 0:
        return result
    result[0] = series.iloc[0]
    for i in range(1, len(series)):
        result[i] = (m * series.iloc[i] + (n - m) * result[i - 1]) / n
    return result


def calculate_bottom_indicators(df):
    """
    底部筛选指标 (来自 Check_Tu50_q.py)
    返回最后一根K线的特征字典
    """
    df = df.copy()
    C = df['close'].values
    H = df['high'].values
    L = df['low'].values
    O = df['open'].values
    V = df['volume'].values

    MA5 = pd.Series(C).rolling(5, min_periods=1).mean().values
    MA10 = pd.Series(C).rolling(10, min_periods=1).mean().values
    MA20 = pd.Series(C).rolling(20, min_periods=1).mean().values
    MA60 = pd.Series(C).rolling(60, min_periods=20).mean().values

    llv9 = pd.Series(L).rolling(9, min_periods=1).min().values
    hhv9 = pd.Series(H).rolling(9, min_periods=1).max().values
    rsv = (C - llv9) / (hhv9 - llv9 + 1e-9) * 100
    K = td_sma(pd.Series(rsv), 3, 1)
    D = td_sma(pd.Series(K), 3, 1)
    J = 3 * K - 2 * D

    DIF = pd.Series(C).ewm(span=12, adjust=False).mean() - pd.Series(C).ewm(span=26, adjust=False).mean()
    DEA = DIF.ewm(span=9, adjust=False).mean()
    MACD = DIF - DEA
    MACD_rising = (MACD > np.roll(MACD, 1)) & (np.roll(MACD, 1) > np.roll(MACD, 2))

    delta = np.diff(C, prepend=C[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=1).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=1).mean().values
    RS = avg_gain / (avg_loss + 1e-9)
    RSI14 = 100 - 100 / (1 + RS)

    volume_ma5 = pd.Series(V).rolling(5, min_periods=1).mean().values
    volume_ratio = V / (volume_ma5 + 1e-9)

    low250 = pd.Series(L).rolling(250, min_periods=1).min().values
    high250 = pd.Series(H).rolling(250, min_periods=1).max().values
    price_position = (C - low250) / (high250 - low250 + 1e-9) * 100
    price_position = np.clip(price_position, 0, 100)

    # 回踩逻辑
    min_low_10 = pd.Series(L).rolling(10, min_periods=5).min().values
    is_rebound = C > min_low_10 * 1.02
    ma5_below_ma10 = (MA5 < MA10).astype(float)
    has_pullback = pd.Series(ma5_below_ma10).rolling(5, min_periods=1).max().values > 0.5
    ma5_above_ma10_now = MA5 > MA10
    ma5_turn_up = MA5 > np.roll(MA5, 1)
    close_above_ma10 = C > MA10 * 0.98
    is_yang_line = C > O
    vol_normal = volume_ratio < 2.5
    ma20_turn_up = MA20 > np.roll(MA20, 3)
    pullback_signal = is_rebound & has_pullback & ma5_above_ma10_now & ma5_turn_up & close_above_ma10 & is_yang_line & vol_normal & ma20_turn_up

    cn_score = np.zeros(len(df))
    cn_score += ((np.roll(K, 1) < np.roll(D, 1)) & (K > D) & (J < 20)) * 20 + MACD_rising * 10 + (RSI14 < 30) * 15
    cn_score += volume_ratio * 10 + (price_position < 30) * 15 + pullback_signal * 30

    us_score = np.zeros(len(df))
    us_score += (MA5 > MA10) & (MA10 > MA20) & (MA20 > MA60) * 15 + (C > pd.Series(H).rolling(260, min_periods=1).max().values) * 25
    us_score += MACD_rising * 10 + (volume_ratio > 1.2) * 10 + pullback_signal * 25

    return {
        'close': float(C[-1]),
        'cn_score': float(cn_score[-1]),
        'us_score': float(us_score[-1]),
        'volume_ratio': float(volume_ratio[-1]),
        'price_position': float(price_position[-1]),
        'pullback_signal': bool(pullback_signal[-1]),
        'macd_rising': bool(MACD_rising[-1]),
        'MA5': float(MA5[-1]),
        'MA10': float(MA10[-1]),
        'MA20': float(MA20[-1]),
    }


def calculate_strong_indicators(df):
    """
    趋势追涨指标 (来自 Check_Qiang.py)
    返回最后一根K线的特征字典
    """
    df = df.copy()
    C = df['close'].values
    H = df['high'].values
    L = df['low'].values
    O = df['open'].values
    V = df['volume'].values

    MA5 = pd.Series(C).rolling(5, min_periods=1).mean().values
    MA10 = pd.Series(C).rolling(10, min_periods=1).mean().values
    MA20 = pd.Series(C).rolling(20, min_periods=1).mean().values
    MA60 = pd.Series(C).rolling(60, min_periods=20).mean().values

    DIF = pd.Series(C).ewm(span=12, adjust=False).mean() - pd.Series(C).ewm(span=26, adjust=False).mean()
    DEA = DIF.ewm(span=9, adjust=False).mean()
    MACD = (DIF - DEA) * 2
    MACD_rising = (MACD > np.roll(MACD, 1)) & (np.roll(MACD, 1) > np.roll(MACD, 2))
    macd_positive = (DIF.values > 0) & (DEA.values > 0)

    delta = np.diff(C, prepend=C[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=1).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=1).mean().values
    RS = avg_gain / (avg_loss + 1e-9)
    RSI14 = 100 - 100 / (1 + RS)
    rsi_strong = (RSI14 > 60) & (RSI14 < 85)

    volume_ma5 = pd.Series(V).rolling(5, min_periods=1).mean().values
    volume_ratio = V / (volume_ma5 + 1e-9)
    volume_surge = volume_ratio > 1.5

    low250 = pd.Series(L).rolling(250, min_periods=1).min().values
    high250 = pd.Series(H).rolling(250, min_periods=1).max().values
    price_position = (C - low250) / (high250 - low250 + 1e-9) * 100
    price_position = np.clip(price_position, 0, 100)

    bullish_alignment = (MA5 > MA10) & (MA10 > MA20) & (MA20 > MA60)
    high20 = pd.Series(H).rolling(20, min_periods=1).max().values
    high60 = pd.Series(H).rolling(60, min_periods=1).max().values
    breakout_20 = C > np.roll(high20, 1)
    breakout_60 = C > np.roll(high60, 1)
    yang_strength = np.where(C > O, (C - O) / (O + 1e-9) * 100, 0)
    strong_yang = yang_strength > 3.0
    close_to_ma10 = np.abs((C - MA10) / (MA10 + 1e-9) * 100) < 2.0
    rebound_from_ma10 = close_to_ma10 & (C > MA10) & strong_yang & bullish_alignment

    trend_score = np.zeros(len(df))
    trend_score += bullish_alignment * 20
    trend_score += breakout_20 * 15
    trend_score += breakout_60 * 25
    trend_score += macd_positive * 15
    trend_score += MACD_rising * 10
    trend_score += rsi_strong * 10
    trend_score += volume_surge * 15
    trend_score += strong_yang * 10
    trend_score += rebound_from_ma10 * 20

    return {
        'close': float(C[-1]),
        'trend_score': float(trend_score[-1]),
        'volume_ratio': float(volume_ratio[-1]),
        'price_position': float(price_position[-1]),
        'macd_rising': bool(MACD_rising[-1]),
        'macd_positive': bool(macd_positive[-1]),
        'bullish_alignment': bool(bullish_alignment[-1]),
        'breakout_20': bool(breakout_20[-1]),
        'breakout_60': bool(breakout_60[-1]),
        'strong_yang': bool(strong_yang[-1]),
        'rebound_from_ma10': bool(rebound_from_ma10[-1]),
        'volume_surge': bool(volume_surge[-1]),
        'MA5': float(MA5[-1]),
        'MA10': float(MA10[-1]),
        'MA20': float(MA20[-1]),
    }


def calculate_pullback_indicators(df):
    """
    回踩确认指标 (来自 Check_Tu50.py)
    返回完整的DataFrame with indicators + pullback signals
    """
    df = df.copy()
    C = df['close'].values
    H = df['high'].values
    L = df['low'].values
    V = df['volume'].values

    llv9 = pd.Series(L).rolling(9, min_periods=1).min().values
    hhv9 = pd.Series(H).rolling(9, min_periods=1).max().values
    rsv = (C - llv9) / (hhv9 - llv9 + 1e-9) * 100
    K = td_sma(pd.Series(rsv), 3, 1)
    D_s = td_sma(pd.Series(K), 3, 1)
    J = 3 * K - 2 * D_s

    DIF = pd.Series(C).ewm(span=12, adjust=False).mean() - pd.Series(C).ewm(span=26, adjust=False).mean()
    DEA = DIF.ewm(span=9, adjust=False).mean()
    MACD = DIF - DEA
    MACD_rising = (MACD > np.roll(MACD, 1)) & (np.roll(MACD, 1) > np.roll(MACD, 2))

    delta = np.diff(C, prepend=C[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=1).mean().values
    avg_loss = pd.Series(loss).rolling(14, min_periods=1).mean().values
    RS = avg_gain / (avg_loss + 1e-9)
    RSI14 = 100 - 100 / (1 + RS)

    volume_ma5 = pd.Series(V).rolling(5, min_periods=1).mean().values
    volume_ratio = V / (volume_ma5 + 1e-9)

    low250 = pd.Series(L).rolling(250, min_periods=1).min().values
    high250 = pd.Series(H).rolling(250, min_periods=1).max().values
    price_position = (C - low250) / (high250 - low250 + 1e-9) * 100
    price_position = np.clip(price_position, 0, 100)

    MA5 = pd.Series(C).rolling(5).mean().values
    MA10 = pd.Series(C).rolling(10).mean().values
    MA20 = pd.Series(C).rolling(20).mean().values
    MA60 = pd.Series(C).rolling(60, min_periods=20).mean().values
    bullish_alignment = (MA5 > MA10) & (MA10 > MA20) & (MA20 > MA60)

    high52 = pd.Series(H).rolling(260, min_periods=1).max().values
    near_52w_high = C > high52 * 0.95
    break_52w_high = C > high52

    k_prev = np.roll(K, 1)
    k_prev[0] = K[0]
    d_prev = np.roll(D_s, 1)
    d_prev[0] = D_s[0]
    kdj_golden = (k_prev < d_prev) & (K > D_s) & (J < 20)
    rsi_oversold = RSI14 < 30
    rsi_rising = (RSI14 > np.roll(RSI14, 1)) & (np.roll(RSI14, 1) < 30)
    volume_surge = volume_ratio > 1.2
    price_low = price_position < 30

    up_move = np.maximum(C - np.roll(C, 1), 0)
    down_move = np.abs(C - np.roll(C, 1))
    up_ma = pd.Series(up_move).rolling(20, min_periods=1).mean().values
    down_ma = pd.Series(down_move).rolling(20, min_periods=1).mean().values
    main_power = up_ma / (down_ma + 1e-9) * 100
    main_power_rising = (main_power > np.roll(main_power, 1)) & (np.roll(main_power, 1) < 40)

    close_up = C > np.roll(C, 1)
    price_up_volume_down = close_up & (volume_ratio < 0.8) & (price_position > 70)

    cn_score = np.zeros(len(df))
    cn_score += kdj_golden * 20
    cn_score += MACD_rising * 10
    cn_score += rsi_oversold * 15
    cn_score += volume_surge * 10
    cn_score += price_low * 15
    cn_score += main_power_rising * 10
    cn_score += price_up_volume_down * 5
    cn_score += rsi_rising * 10

    us_score = np.zeros(len(df))
    us_score += bullish_alignment * 15
    us_score += break_52w_high * 25
    us_score += near_52w_high * 10
    us_score += (RSI14 > 50) * 15
    us_score += MACD_rising * 10
    us_score += volume_surge * 10
    us_score += price_up_volume_down * 10
    us_score += (J > 40) * 5

    # 回踩确认检测
    lookback = 10
    rebound_threshold = 0.05
    close_s = C
    low_roll = pd.Series(close_s).rolling(lookback, min_periods=1).min().values
    rebound = (close_s - low_roll) / (low_roll + 1e-9)
    ma5_above_ma10 = MA5 > MA10
    ma5_below_ma10 = MA5 < MA10
    golden_cross = ma5_above_ma10 & (np.roll(ma5_below_ma10, 1))
    recent_golden = golden_cross.copy()
    for i in range(1, min(3, len(df))):
        recent_golden = recent_golden | np.roll(golden_cross, i)
    price_above_ma10 = close_s > MA10
    rebound_detected = rebound >= rebound_threshold
    pullback_confirm = recent_golden & price_above_ma10 & rebound_detected

    df['cn_score'] = cn_score
    df['us_score'] = us_score
    df['volume_ratio'] = volume_ratio
    df['price_position'] = price_position
    df['MA5'] = MA5
    df['MA10'] = MA10
    df['close'] = C
    df['MACD'] = MACD
    df['DIF'] = DIF
    df['DEA'] = DEA
    df['K'] = K
    df['D'] = D_s
    df['J'] = J
    df['RSI14'] = RSI14
    df['pullback_confirm'] = pullback_confirm
    return df


# ============================================================
# Futu API Wrapper
# ============================================================

class FutuClient:
    def __init__(self):
        self.quote_ctx = None
        self.subscribed = set()

    def _ensure_connected(self):
        if self.quote_ctx is not None:
            return
        from futu import OpenQuoteContext
        self.quote_ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        logger.info("Connected to Futu OpenD")

    def _force_reconnect(self):
        """强制重置连接 (用于批量扫描时重置额度)"""
        self.close()
        time.sleep(1)
        self._ensure_connected()
        logger.info("Connection reset")

    def close(self):
        if self.quote_ctx is not None:
            try:
                self.quote_ctx.close()
            except Exception:
                pass
            self.quote_ctx = None
            self.subscribed = set()
            logger.info("Disconnected from Futu OpenD")

    def _get_klines_batch_subscribe(self, codes, ktype_str='K_DAY', num=300):
        """
        订阅 + 批量获取K线 (用于扫描场景)
        codes: list of stock codes
        ktype_str: 'K_DAY', 'K_WEEK', 'K_MON'
        num: number of bars
        Returns: dict of code -> dataframe
        """
        from futu import KLType, AuType, SubType, RET_OK
        ktype_map = {'K_DAY': KLType.K_DAY, 'K_WEEK': KLType.K_WEEK, 'K_MON': KLType.K_MON}
        sub_map = {'K_DAY': SubType.K_DAY, 'K_WEEK': SubType.K_WEEK, 'K_MON': SubType.K_MON}
        kl = ktype_map.get(ktype_str.upper())
        sub_t = sub_map.get(ktype_str.upper())
        if kl is None:
            raise ValueError(f"Invalid ktype: {ktype_str}")

        self._ensure_connected()
        ret, msg = self.quote_ctx.subscribe(codes, [sub_t])
        if ret != RET_OK:
            raise RuntimeError(f"Subscribe failed: {msg}")
        time.sleep(2.5)

        result = {}
        for code in codes:
            try:
                ret2, data = self.quote_ctx.get_cur_kline(code, num, kl, autype=AuType.QFQ)
                if ret2 == RET_OK and len(data) >= 30:
                    data = data.sort_values(by='time_key').reset_index(drop=True)
                    result[code] = data
            except Exception as e:
                logger.debug(f"get_kline {code} failed: {e}")
            time.sleep(0.03)

        try:
            self.quote_ctx.unsubscribe(codes, [sub_t])
        except Exception:
            pass
        return result

    def get_stock_list(self, market: str) -> list[dict]:
        from futu import Market, SecurityType, RET_OK
        market_map = {'HK': Market.HK, 'US': Market.US, 'SH': Market.SH, 'SZ': Market.SZ}
        mkt = market_map.get(market.upper())
        if mkt is None:
            raise ValueError(f"Invalid market: {market}. Use HK, US, SH, SZ")
        self._ensure_connected()
        ret, data = self.quote_ctx.get_stock_basicinfo(mkt, SecurityType.STOCK)
        if ret != RET_OK:
            raise RuntimeError(f"Failed to get stock list: {data}")
        records = []
        for _, row in data.iterrows():
            records.append({'code': row['code'], 'name': row['name'], 'lot_size': int(row['lot_size'])})
        return records

    def get_kline(self, code: str, ktype: str = 'K_DAY', num: int = 120) -> list[dict]:
        from futu import KLType, AuType, SubType, RET_OK
        ktype_map = {'K_DAY': KLType.K_DAY, 'K_WEEK': KLType.K_WEEK, 'K_MON': KLType.K_MON}
        kl = ktype_map.get(ktype.upper())
        if kl is None:
            raise ValueError(f"Invalid ktype: {ktype}")
        self._ensure_connected()
        if code not in self.subscribed:
            if self.subscribed:
                self.quote_ctx.unsubscribe(list(self.subscribed), [SubType.K_DAY])
            ret, msg = self.quote_ctx.subscribe([code], [SubType.K_DAY])
            if ret != RET_OK:
                raise RuntimeError(f"Subscribe failed: {msg}")
            self.subscribed = {code}
            time.sleep(0.3)
        ret, data = self.quote_ctx.get_cur_kline(code, num, kl, autype=AuType.QFQ)
        if ret != RET_OK:
            raise RuntimeError(f"Failed to get kline: {data}")
        data = data.sort_values(by='time_key')
        records = []
        for _, row in data.iterrows():
            records.append({
                'time_key': str(row['time_key']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
                'turnover': float(row.get('turnover', 0)),
            })
        return records

    def get_market_snapshot(self, codes: list[str]) -> list[dict]:
        from futu import RET_OK
        self._ensure_connected()
        ret, snap = self.quote_ctx.get_market_snapshot(codes)
        if ret != RET_OK:
            raise RuntimeError(f"Failed to get snapshot: {snap}")
        records = []
        for _, row in snap.iterrows():
            records.append({
                'code': row['code'],
                'name': row.get('name', ''),
                'last_price': float(row.get('last_price', 0)),
                'open_price': float(row.get('open_price', 0)),
                'high_price': float(row.get('high_price', 0)),
                'low_price': float(row.get('low_price', 0)),
                'volume': float(row.get('volume', 0)),
                'turnover': float(row.get('turnover', 0)),
                'change_val': float(row.get('change_val', 0)),
                'change_rate': float(row.get('change_rate', 0)),
                'time': str(row.get('time', '')),
            })
        return records

    # ======== 底部筛选 (来自 Check_Tu50_q.py) ========
    def scan_bottom(self, market: str, max_stocks: int = 200, min_volume: float = 100000, score_th: float = 15, sub_batch_size: int = 80) -> list[dict]:
        """
        底部筛选：日周月三底共振 + 回踩形态
        """
        stocks = self.get_stock_list(market)
        codes = [s['code'] for s in stocks[:max_stocks]]

        # 快照过滤成交量
        valid_codes = []
        for i in range(0, len(codes), 400):
            try:
                snaps = self.get_market_snapshot(codes[i:i + 400])
                for s in snaps:
                    if s['volume'] >= min_volume and s['last_price'] > 0:
                        valid_codes.append(s['code'])
            except Exception:
                pass
            time.sleep(0.3)

        if not valid_codes:
            return []

        results = []
        total_batches = (len(valid_codes) + sub_batch_size - 1) // sub_batch_size

        for b in range(total_batches):
            batch = valid_codes[b * sub_batch_size:(b + 1) * sub_batch_size]
            logger.info(f"Bottom scan: batch {b + 1}/{total_batches}, {len(batch)} stocks")
            try:
                self._force_reconnect()
                day_data = self._get_klines_batch_subscribe(batch, 'K_DAY', 300)
                time.sleep(0.5)
                self._force_reconnect()
                week_data = self._get_klines_batch_subscribe(batch, 'K_WEEK', 100)
                time.sleep(0.5)
                self._force_reconnect()
                mon_data = self._get_klines_batch_subscribe(batch, 'K_MON', 50)

                for code in batch:
                    df_d = day_data.get(code)
                    df_w = week_data.get(code)
                    df_m = mon_data.get(code)
                    if df_d is None or df_w is None or df_m is None:
                        continue

                    feat_d = calculate_bottom_indicators(df_d)
                    feat_w = calculate_bottom_indicators(df_w)
                    feat_m = calculate_bottom_indicators(df_m)

                    if feat_d['close'] == 0:
                        continue

                    is_d_pullback = feat_d['pullback_signal']
                    base_score = feat_d['us_score'] if market == 'US' else feat_d['cn_score']
                    final_score = base_score
                    signal_tag = "常规信号"
                    pass_filter = False

                    if (feat_m['price_position'] > 90 and not feat_m['macd_rising']) or (feat_w['price_position'] > 95):
                        pass_filter = False
                    elif is_d_pullback and feat_m['price_position'] < 30 and feat_w['price_position'] < 40 and feat_w['macd_rising']:
                        signal_tag = "★【日周月三底共振】"
                        final_score += 40
                        pass_filter = True
                    elif is_d_pullback and feat_w['close'] > feat_w['MA5'] and feat_w['macd_rising'] and feat_m['price_position'] < 70:
                        signal_tag = "☆【日周波段共振】"
                        final_score += 20
                        pass_filter = True
                    else:
                        if is_d_pullback and final_score >= 25:
                            signal_tag = "◆【纯日线回踩】"
                            pass_filter = True
                        elif market == 'US' and final_score >= score_th and feat_d['volume_ratio'] >= 0.6 and feat_d['price_position'] <= 90:
                            pass_filter = True
                        elif market != 'US' and final_score >= score_th and feat_d['volume_ratio'] >= 0.8 and feat_d['price_position'] <= 50:
                            pass_filter = True

                    if pass_filter:
                        results.append({
                            'code': code,
                            'market': market,
                            'score': round(final_score, 1),
                            'd_pos': round(feat_d['price_position'], 1),
                            'w_pos': round(feat_w['price_position'], 1),
                            'm_pos': round(feat_m['price_position'], 1),
                            'tag': signal_tag,
                            'signal_date': datetime.now().strftime('%Y-%m-%d'),
                        })
            except Exception as e:
                logger.warning(f"Batch {b} failed: {e}")

            if b < total_batches - 1:
                logger.info(f"Cooling down 60s between batches...")
                time.sleep(60)

        return results

    # ======== 追高筛选 (来自 Check_Qiang.py) ========
    def scan_strong(self, market: str, max_stocks: int = 200, min_volume: float = 100000, score_th: float = 40, sub_batch_size: int = 80) -> list[dict]:
        """
        强势追高筛选：日周月主升浪突破 / 多头回踩起跳 / 波段加速
        """
        stocks = self.get_stock_list(market)
        codes = [s['code'] for s in stocks[:max_stocks]]

        valid_codes = []
        for i in range(0, len(codes), 400):
            try:
                snaps = self.get_market_snapshot(codes[i:i + 400])
                for s in snaps:
                    if s['volume'] >= min_volume and s['last_price'] > 0 and s.get('change_rate', 0) > -2.0:
                        valid_codes.append(s['code'])
            except Exception:
                pass
            time.sleep(0.3)

        if not valid_codes:
            return []

        results = []
        total_batches = (len(valid_codes) + sub_batch_size - 1) // sub_batch_size

        for b in range(total_batches):
            batch = valid_codes[b * sub_batch_size:(b + 1) * sub_batch_size]
            logger.info(f"Strong scan: batch {b + 1}/{total_batches}, {len(batch)} stocks")
            try:
                self._force_reconnect()
                day_data = self._get_klines_batch_subscribe(batch, 'K_DAY', 300)
                time.sleep(0.5)
                self._force_reconnect()
                week_data = self._get_klines_batch_subscribe(batch, 'K_WEEK', 100)
                time.sleep(0.5)
                self._force_reconnect()
                mon_data = self._get_klines_batch_subscribe(batch, 'K_MON', 50)

                for code in batch:
                    df_d = day_data.get(code)
                    df_w = week_data.get(code)
                    df_m = mon_data.get(code)
                    if df_d is None or df_w is None or df_m is None:
                        continue

                    feat_day = calculate_strong_indicators(df_d)
                    feat_week = calculate_strong_indicators(df_w)
                    feat_mon = calculate_strong_indicators(df_m)

                    if feat_day['close'] == 0:
                        continue

                    base_score = feat_day['trend_score']
                    final_score = base_score
                    signal_tag = "常规信号"
                    pass_filter = False

                    is_day_volume_surge = feat_day.get('volume_surge', False) or (feat_day['volume_ratio'] > 1.5)

                    mon_bearish = not feat_mon['bullish_alignment'] and (feat_mon['close'] < feat_mon['MA20'])
                    week_breakdown = (feat_week['close'] < feat_week['MA20']) and (not feat_week['macd_rising'])

                    if mon_bearish or week_breakdown:
                        pass_filter = False
                    elif (feat_mon['bullish_alignment'] or feat_mon['close'] > feat_mon['MA20']) \
                            and feat_week['bullish_alignment'] \
                            and feat_day['breakout_60'] \
                            and is_day_volume_surge:
                        signal_tag = "★【日周月主升浪突破】"
                        final_score += 50
                        pass_filter = True
                    elif feat_week['bullish_alignment'] \
                            and feat_day['rebound_from_ma10'] \
                            and feat_day['volume_ratio'] > 1.0:
                        signal_tag = "☆【多头回踩精确起跳】"
                        final_score += 30
                        pass_filter = True
                    elif feat_week['close'] > feat_week['MA10'] \
                            and feat_day['breakout_20'] \
                            and feat_day['strong_yang'] \
                            and feat_day['macd_positive']:
                        signal_tag = "◆【波段加速突破】"
                        final_score += 20
                        pass_filter = True
                    else:
                        if final_score >= score_th and feat_day['bullish_alignment'] and feat_day['price_position'] > 50:
                            signal_tag = "▲【日线强势形态】"
                            pass_filter = True

                    if pass_filter:
                        results.append({
                            'code': code,
                            'market': market,
                            'score': round(final_score, 1),
                            'd_pos': round(feat_day['price_position'], 1),
                            'w_pos': round(feat_week['price_position'], 1),
                            'm_pos': round(feat_mon['price_position'], 1),
                            'tag': signal_tag,
                            'signal_date': datetime.now().strftime('%Y-%m-%d'),
                        })
            except Exception as e:
                logger.warning(f"Batch {b} failed: {e}")

            if b < total_batches - 1:
                logger.info(f"Cooling down 60s between batches...")
                time.sleep(60)

        return results

    # ======== 5日线回踩10日线选股 ========
    def scan_pullback(self, market: str, max_stocks: int = 200, min_volume: float = 100000, sub_batch_size: int = 80) -> list[dict]:
        """
        5日线回踩10日线选股 (来自 Check_Tu50.py detect_pullback_confirm)
        """
        stocks = self.get_stock_list(market)
        codes = [s['code'] for s in stocks[:max_stocks]]

        valid_codes = []
        for i in range(0, len(codes), 400):
            try:
                snaps = self.get_market_snapshot(codes[i:i + 400])
                for s in snaps:
                    if s['volume'] >= min_volume and s['last_price'] > 0:
                        valid_codes.append(s['code'])
            except Exception:
                pass
            time.sleep(0.3)

        if not valid_codes:
            return []

        results = []
        total_batches = (len(valid_codes) + sub_batch_size - 1) // sub_batch_size

        for b in range(total_batches):
            batch = valid_codes[b * sub_batch_size:(b + 1) * sub_batch_size]
            logger.info(f"Pullback scan: batch {b + 1}/{total_batches}, {len(batch)} stocks")
            try:
                self._force_reconnect()
                day_data = self._get_klines_batch_subscribe(batch, 'K_DAY', 200)

                for code in batch:
                    df = day_data.get(code)
                    if df is None or len(df) < 60:
                        continue

                    df_indicators = calculate_pullback_indicators(df)
                    last = df_indicators.iloc[-1]

                    has_pullback = last['pullback_confirm']
                    cn_score_val = last['cn_score']
                    us_score_val = last['us_score']
                    price_pos = last['price_position']
                    score_val = us_score_val if market == 'US' else cn_score_val

                    if has_pullback and score_val >= 10:
                        results.append({
                            'code': code,
                            'market': market,
                            'score': round(float(score_val), 1),
                            'price_position': round(float(price_pos), 1),
                            'signal': '5日线回踩10日线确认',
                            'signal_date': datetime.now().strftime('%Y-%m-%d'),
                            'close': round(float(last['close']), 3),
                            'ma5': round(float(last['MA5']), 3),
                            'ma10': round(float(last['MA10']), 3),
                        })
            except Exception as e:
                logger.warning(f"Batch {b} failed: {e}")

            if b < total_batches - 1:
                time.sleep(60)

        return results

    # ======== 单只股票回测验证 (来自 Check_Tu50.py BacktestEngine) ========
    def backtest_stock(self, code: str, start_date: str, end_date: str,
                       market: str = 'CN', score_th: float = 15,
                       enable_pullback: bool = True,
                       hold_days: list = None) -> dict:
        """
        单只股票历史回测验证
        code: 股票代码如 HK.00700
        start_date/end_date: YYYY-MM-DD
        market: 'CN' or 'US' (决定使用cn_score或us_score)
        hold_days: 持有天数列表 [10,20,30]
        """
        if hold_days is None:
            hold_days = [10, 20, 30]

        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        # 获取日线数据 - 直接使用get_kline (不需要批量订阅)
        try:
            kline_raw = self.get_kline(code, 'K_DAY', 500)
            if len(kline_raw) < 100:
                return {"error": f"数据不足: 仅{len(kline_raw)}条K线"}
        except Exception as e:
            return {"error": f"获取K线失败: {str(e)}"}

        df = pd.DataFrame(kline_raw)
        df['time_key'] = pd.to_datetime(df['time_key'])
        df = df.sort_values('time_key').reset_index(drop=True)

        # 计算全部指标
        df_full = calculate_pullback_indicators(df)
        df_full.set_index('time_key', inplace=True)

        # 截取回测期间
        mask = (df_full.index >= start_dt) & (df_full.index <= end_dt)
        df_period = df_full.loc[mask].copy()
        if len(df_period) == 0:
            return {"error": "回测期间无数据"}

        signals = []
        for i in range(len(df_period)):
            current_date = df_period.index[i]
            hist = df_full[df_full.index <= current_date]
            if len(hist) < 50:
                continue
            current = hist.iloc[-1]

            if market == 'US':
                daily_score = current['us_score']
                daily_cond = (daily_score >= score_th) and (current['volume_ratio'] >= 0.6) and (current['price_position'] <= 95)
            else:
                daily_score = current['cn_score']
                daily_cond = (daily_score >= score_th) and (current['volume_ratio'] >= 0.8) and (current['price_position'] <= 50)

            signal_type = []
            if daily_cond:
                signal_type.append("评分")
            if enable_pullback and current['pullback_confirm']:
                signal_type.append("回踩确认")
            if not signal_type:
                continue

            future = df_full[df_full.index > current_date]
            if len(future) == 0:
                continue

            buy_price = float(current['close'])
            returns = {}
            for hold in hold_days:
                if hold > len(future):
                    ret = None
                else:
                    ret = float((future.iloc[hold - 1]['close'] - buy_price) / buy_price)
                returns[f'ret_{hold}d'] = ret

            signals.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'type': '+'.join(signal_type),
                'score': float(daily_score),
                'buy_price': round(buy_price, 4),
                **returns,
            })

        # 汇总统计
        stats = {}
        for hold in hold_days:
            col = f'ret_{hold}d'
            vals = [s[col] for s in signals if s[col] is not None]
            if vals:
                arr = np.array(vals)
                stats[f'hold_{hold}d'] = {
                    'signal_count': len(arr),
                    'win_rate': round(float((arr > 0).mean()), 4),
                    'avg_return': round(float(arr.mean()), 4),
                    'cumulative_return': round(float((1 + arr).prod() - 1), 4),
                    'max_return': round(float(arr.max()), 4),
                    'min_return': round(float(arr.min()), 4),
                }
            else:
                stats[f'hold_{hold}d'] = {'signal_count': 0}

        return {
            'code': code,
            'market': market,
            'start_date': start_date,
            'end_date': end_date,
            'total_signals': len(signals),
            'signals': signals,
            'statistics': stats,
        }

    # ======== MACD底背离扫描 (来自 checkFt.py) ========
    def scan_divergence(self, market: str, max_stocks: int = 100, min_volume: float = 100000) -> list[dict]:
        stocks = self.get_stock_list(market)
        codes = [s['code'] for s in stocks[:max_stocks]]

        results = []
        for i in range(0, len(codes), 400):
            batch = codes[i:i + 400]
            try:
                snapshots = self.get_market_snapshot(batch)
                for snap in snapshots:
                    if snap['volume'] >= min_volume and snap['last_price'] > 0:
                        results.append({
                            'code': snap['code'],
                            'name': snap['name'],
                            'last_price': snap['last_price'],
                            'volume': snap['volume'],
                        })
            except Exception as e:
                logger.warning(f"Snapshot batch failed: {e}")
            time.sleep(0.3)

        candidates = []
        for r in results[:50]:
            try:
                kline = self.get_kline(r['code'], 'K_DAY', 120)
                if len(kline) < 50:
                    continue
                closes = [k['close'] for k in kline]
                macd_line, signal_line = self._calculate_macd(closes)
                has_divergence, info = self._detect_bottom_divergence(closes, macd_line)
                if has_divergence:
                    candidates.append({
                        'code': r['code'],
                        'name': r['name'],
                        'last_price': r['last_price'],
                        'signal': 'MACD底部背离',
                        'macd_value': round(float(macd_line[-1]), 4),
                    })
            except Exception as e:
                logger.debug(f"Skip {r['code']}: {e}")
            time.sleep(0.1)

        return candidates

    def _calculate_macd(self, closes: list[float]):
        close_series = pd.Series(closes)
        ema12 = close_series.ewm(span=12, adjust=False).mean()
        ema26 = close_series.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        return macd_line.values, signal_line.values

    def _detect_bottom_divergence(self, closes: list[float], macd_line):
        n = len(closes)
        price_valleys = []
        for i in range(5, n - 5):
            if closes[i] == min(closes[i - 5:i + 6]):
                price_valleys.append(i)
        macd_valleys = []
        for i in range(5, n - 5):
            val = macd_line[i]
            if np.isnan(val):
                continue
            window = [x for x in macd_line[i - 5:i + 6] if not np.isnan(x)]
            if len(window) > 0 and val == min(window):
                macd_valleys.append(i)
        if len(price_valleys) < 2 or len(macd_valleys) < 2:
            return False, None
        last_pv = price_valleys[-1]
        prev_pv = price_valleys[-2]
        last_mv = macd_valleys[-1]
        prev_mv = macd_valleys[-2]
        if abs(last_pv - last_mv) > 5:
            return False, None
        price_down = closes[last_pv] < closes[prev_pv]
        macd_up = macd_line[last_mv] > macd_line[prev_mv]
        return (price_down and macd_up), {
            'price_valley_prev_idx': int(prev_pv),
            'price_valley_last_idx': int(last_pv),
            'macd_valley_prev_idx': int(prev_mv),
            'macd_valley_last_idx': int(last_mv),
        }


# ------------------------------------------------------------
# Global client
# ------------------------------------------------------------
futu_client = FutuClient()
app = Server("futu-stock-server")


# ------------------------------------------------------------
# MCP Tool Definitions
# ------------------------------------------------------------
@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_stock_list",
            description="获取指定市场的股票列表 (HK/US/SH/SZ)",
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "description": "市场代码: HK(港股), US(美股), SH(沪市), SZ(深市)",
                        "enum": ["HK", "US", "SH", "SZ"],
                    }
                },
                "required": ["market"],
            },
        ),
        Tool(
            name="get_kline",
            description="获取股票历史K线数据 (日/周/月线)",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码, 如 HK.00700, US.AAPL"},
                    "ktype": {"type": "string", "description": "K线类型", "enum": ["K_DAY", "K_WEEK", "K_MON"], "default": "K_DAY"},
                    "num": {"type": "number", "description": "K线数量", "default": 120, "minimum": 10, "maximum": 300},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="get_market_snapshot",
            description="获取股票实时行情快照",
            inputSchema={
                "type": "object",
                "properties": {
                    "codes": {"type": "array", "items": {"type": "string"}, "description": "股票代码列表"},
                },
                "required": ["codes"],
            },
        ),
        Tool(
            name="scan_bottom",
            description="【底部筛选】扫描底部+回踩形态股票 (日周月三底共振 / 日周波段共振 / 纯日线回踩)",
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "市场", "enum": ["HK", "US", "SH", "SZ"]},
                    "max_stocks": {"type": "number", "description": "最大扫描数", "default": 200},
                    "min_volume": {"type": "number", "description": "最低成交量", "default": 100000},
                    "score_th": {"type": "number", "description": "评分阈值", "default": 15},
                },
                "required": ["market"],
            },
        ),
        Tool(
            name="scan_strong",
            description="【追高筛选】扫描强势追涨股票 (日周月主升浪突破 / 多头回踩起跳 / 波段加速)",
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "市场", "enum": ["HK", "US", "SH", "SZ"]},
                    "max_stocks": {"type": "number", "description": "最大扫描数", "default": 200},
                    "min_volume": {"type": "number", "description": "最低成交量", "default": 100000},
                    "score_th": {"type": "number", "description": "评分阈值(默认40)", "default": 40},
                },
                "required": ["market"],
            },
        ),
        Tool(
            name="scan_pullback",
            description="【5日线回踩10日线选股】扫描MA5回踩MA10确认形态的股票",
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "市场", "enum": ["HK", "US", "SH", "SZ"]},
                    "max_stocks": {"type": "number", "description": "最大扫描数", "default": 200},
                    "min_volume": {"type": "number", "description": "最低成交量", "default": 100000},
                },
                "required": ["market"],
            },
        ),
        Tool(
            name="backtest_stock",
            description="【单股回测】对单只股票进行历史回测验证 (使用历史K线数据，不受额度限制)",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码, 如 HK.00700"},
                    "start_date": {"type": "string", "description": "回测开始日期 YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "回测结束日期 YYYY-MM-DD"},
                    "market": {"type": "string", "description": "市场类型 CN=A股/港股评分, US=美股评分", "enum": ["CN", "US"], "default": "CN"},
                    "score_th": {"type": "number", "description": "评分阈值(默认15)", "default": 15},
                    "enable_pullback": {"type": "boolean", "description": "启用回踩确认筛选", "default": True},
                    "hold_days": {"type": "array", "items": {"type": "number"}, "description": "持有天数列表", "default": [10, 20, 30]},
                },
                "required": ["code", "start_date", "end_date"],
            },
        ),
        Tool(
            name="scan_divergence",
            description="【MACD底背离】扫描MACD底部背离信号股票",
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "市场", "enum": ["HK", "US", "SH", "SZ"]},
                    "max_stocks": {"type": "number", "description": "最大扫描数", "default": 100},
                    "min_volume": {"type": "number", "description": "最低成交量", "default": 100000},
                },
                "required": ["market"],
            },
        ),
    ]


# ------------------------------------------------------------
# Tool Call Handler
# ------------------------------------------------------------
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    try:
        if name == "get_stock_list":
            stocks = futu_client.get_stock_list(arguments["market"])
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(stocks, ensure_ascii=False, indent=2))])

        elif name == "get_kline":
            ktype = arguments.get("ktype", "K_DAY")
            num = int(arguments.get("num", 120))
            data = futu_client.get_kline(arguments["code"], ktype, num)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])

        elif name == "get_market_snapshot":
            codes = arguments["codes"]
            if isinstance(codes, str):
                codes = [codes]
            data = futu_client.get_market_snapshot(codes)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])

        elif name == "scan_bottom":
            market = arguments["market"]
            max_stocks = int(arguments.get("max_stocks", 200))
            min_volume = float(arguments.get("min_volume", 100000))
            score_th = float(arguments.get("score_th", 15))
            results = futu_client.scan_bottom(market, max_stocks, min_volume, score_th)
            text = f"底部筛选结果 ({market}):\n共 {len(results)} 只\n\n"
            for r in results:
                text += f"[{r['tag']}] {r['code']} 评分:{r['score']} 日:{r['d_pos']}% 周:{r['w_pos']}% 月:{r['m_pos']}%\n"
            text += f"\nJSON:\n{json.dumps(results, ensure_ascii=False, indent=2)}"
            return CallToolResult(content=[TextContent(type="text", text=text)])

        elif name == "scan_strong":
            market = arguments["market"]
            max_stocks = int(arguments.get("max_stocks", 200))
            min_volume = float(arguments.get("min_volume", 100000))
            score_th = float(arguments.get("score_th", 40))
            results = futu_client.scan_strong(market, max_stocks, min_volume, score_th)
            text = f"追高筛选结果 ({market}):\n共 {len(results)} 只\n\n"
            for r in results:
                text += f"[{r['tag']}] {r['code']} 评分:{r['score']} 日:{r['d_pos']}% 周:{r['w_pos']}% 月:{r['m_pos']}%\n"
            text += f"\nJSON:\n{json.dumps(results, ensure_ascii=False, indent=2)}"
            return CallToolResult(content=[TextContent(type="text", text=text)])

        elif name == "scan_pullback":
            market = arguments["market"]
            max_stocks = int(arguments.get("max_stocks", 200))
            min_volume = float(arguments.get("min_volume", 100000))
            results = futu_client.scan_pullback(market, max_stocks, min_volume)
            text = f"5日线回踩10日线选股结果 ({market}):\n共 {len(results)} 只\n\n"
            for r in results:
                text += f"{r['code']} 评分:{r['score']} 位置:{r['price_position']}% 收盘:{r['close']} MA5:{r['ma5']} MA10:{r['ma10']}\n"
            text += f"\nJSON:\n{json.dumps(results, ensure_ascii=False, indent=2)}"
            return CallToolResult(content=[TextContent(type="text", text=text)])

        elif name == "backtest_stock":
            code = arguments["code"]
            start_date = arguments["start_date"]
            end_date = arguments["end_date"]
            market = arguments.get("market", "CN")
            score_th = float(arguments.get("score_th", 15))
            enable_pullback = arguments.get("enable_pullback", True)
            hold_days = arguments.get("hold_days", [10, 20, 30])
            results = futu_client.backtest_stock(code, start_date, end_date, market, score_th, enable_pullback, hold_days)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(results, ensure_ascii=False, indent=2))])

        elif name == "scan_divergence":
            market = arguments["market"]
            max_stocks = int(arguments.get("max_stocks", 100))
            min_volume = float(arguments.get("min_volume", 100000))
            results = futu_client.scan_divergence(market, max_stocks, min_volume)
            text = f"MACD底背离扫描结果 ({market}):\n共 {len(results)} 只\n\n"
            for r in results:
                text += f"{r['code']} {r['name']} 现价:{r['last_price']} MACD值:{r.get('macd_value','N/A')}\n"
            text += f"\nJSON:\n{json.dumps(results, ensure_ascii=False, indent=2)}"
            return CallToolResult(content=[TextContent(type="text", text=text)])

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Error calling {name}: {e}", exc_info=True)
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=f"Error: {str(e)}\n{traceback.format_exc()}")]
        )


@app.shutdown()
async def shutdown():
    futu_client.close()


async def main():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="futu-stock-server",
                server_version="2.0.0",
            ),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
#!/usr/bin/env python3
"""
Futu MCP Server 核心模块 - 指标函数 + FutuClient
供 futu_mcp_server.py (MCP) 和 run_scan.py (CLI) 共同使用

v3.1 - BATCH_SIZE=250, 全市场扫描(无数量限制)
"""
import sys, os, time, json, logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from futu import *

logger = logging.getLogger("futu-core")

# ============================================================
# 公用技术指标函数
# ============================================================

def td_sma(series, n, m):
    result = np.zeros(len(series))
    if len(series) == 0: return result
    result[0] = series.iloc[0]
    for i in range(1, len(series)):
        result[i] = (m * series.iloc[i] + (n - m) * result[i - 1]) / n
    return result


def calculate_bottom_indicators(df):
    """底部筛选指标 (Check_Tu50_q.py)"""
    df = df.copy()
    C=df['close'].values; H=df['high'].values; L=df['low'].values; O=df['open'].values; V=df['volume'].values
    MA5=pd.Series(C).rolling(5,min_periods=1).mean().values; MA10=pd.Series(C).rolling(10,min_periods=1).mean().values
    MA20=pd.Series(C).rolling(20,min_periods=1).mean().values; MA60=pd.Series(C).rolling(60,min_periods=20).mean().values
    llv9=pd.Series(L).rolling(9,min_periods=1).min().values; hhv9=pd.Series(H).rolling(9,min_periods=1).max().values
    rsv=(C-llv9)/(hhv9-llv9+1e-9)*100; K=td_sma(pd.Series(rsv),3,1); D=td_sma(pd.Series(K),3,1); J=3*K-2*D
    DIF=pd.Series(C).ewm(span=12,adjust=False).mean()-pd.Series(C).ewm(span=26,adjust=False).mean()
    DEA=DIF.ewm(span=9,adjust=False).mean(); MACD=DIF-DEA
    MACD_rising=(MACD>np.roll(MACD,1))&(np.roll(MACD,1)>np.roll(MACD,2))
    delta=np.diff(C,prepend=C[0]); gain=np.where(delta>0,delta,0); loss=np.where(delta<0,-delta,0)
    avg_gain=pd.Series(gain).rolling(14,min_periods=1).mean().values; avg_loss=pd.Series(loss).rolling(14,min_periods=1).mean().values
    RS=avg_gain/(avg_loss+1e-9); RSI14=100-100/(1+RS)
    volume_ma5=pd.Series(V).rolling(5,min_periods=1).mean().values; volume_ratio=V/(volume_ma5+1e-9)
    low250=pd.Series(L).rolling(250,min_periods=1).min().values; high250=pd.Series(H).rolling(250,min_periods=1).max().values
    price_position=np.clip((C-low250)/(high250-low250+1e-9)*100,0,100)
    min_low_10=pd.Series(L).rolling(10,min_periods=5).min().values; is_rebound=C>min_low_10*1.02
    has_pullback=pd.Series((MA5<MA10).astype(float)).rolling(5,min_periods=1).max().values>0.5
    ma5_above_ma10_now=MA5>MA10; ma5_turn_up=MA5>np.roll(MA5,1)
    close_above_ma10=C>MA10*0.98; is_yang_line=C>O; vol_normal=volume_ratio<2.5; ma20_turn_up=MA20>np.roll(MA20,3)
    pullback_signal=is_rebound&has_pullback&ma5_above_ma10_now&ma5_turn_up&close_above_ma10&is_yang_line&vol_normal&ma20_turn_up
    cn_score=np.zeros(len(df))
    cn_score+=((np.roll(K,1)<np.roll(D,1))&(K>D)&(J<20))*20+MACD_rising*10+(RSI14<30)*15
    cn_score+=volume_ratio*10+(price_position<30)*15+pullback_signal*30
    us_score=np.zeros(len(df))
    us_score+=(MA5>MA10)&(MA10>MA20)&(MA20>MA60)*15+(C>pd.Series(H).rolling(260,min_periods=1).max().values)*25
    us_score+=MACD_rising*10+(volume_ratio>1.2)*10+pullback_signal*25
    return {'close':float(C[-1]),'cn_score':float(cn_score[-1]),'us_score':float(us_score[-1]),
            'volume_ratio':float(volume_ratio[-1]),'price_position':float(price_position[-1]),
            'pullback_signal':bool(pullback_signal[-1]),'macd_rising':bool(MACD_rising[-1]),
            'MA5':float(MA5[-1]),'MA10':float(MA10[-1]),'MA20':float(MA20[-1])}


def calculate_strong_indicators(df):
    """趋势追涨指标 (Check_Qiang.py)"""
    df=df.copy(); C=df['close'].values; H=df['high'].values; L=df['low'].values; O=df['open'].values; V=df['volume'].values
    MA5=pd.Series(C).rolling(5,min_periods=1).mean().values; MA10=pd.Series(C).rolling(10,min_periods=1).mean().values
    MA20=pd.Series(C).rolling(20,min_periods=1).mean().values; MA60=pd.Series(C).rolling(60,min_periods=20).mean().values
    DIF=pd.Series(C).ewm(span=12,adjust=False).mean()-pd.Series(C).ewm(span=26,adjust=False).mean()
    DEA=DIF.ewm(span=9,adjust=False).mean(); MACD=(DIF-DEA)*2
    MACD_rising=(MACD>np.roll(MACD,1))&(np.roll(MACD,1)>np.roll(MACD,2)); macd_positive=(DIF.values>0)&(DEA.values>0)
    delta=np.diff(C,prepend=C[0]); gain=np.where(delta>0,delta,0); loss=np.where(delta<0,-delta,0)
    avg_gain=pd.Series(gain).rolling(14,min_periods=1).mean().values; avg_loss=pd.Series(loss).rolling(14,min_periods=1).mean().values
    RS=avg_gain/(avg_loss+1e-9); RSI14=100-100/(1+RS); rsi_strong=(RSI14>60)&(RSI14<85)
    volume_ma5=pd.Series(V).rolling(5,min_periods=1).mean().values; volume_ratio=V/(volume_ma5+1e-9); volume_surge=volume_ratio>1.5
    low250=pd.Series(L).rolling(250,min_periods=1).min().values; high250=pd.Series(H).rolling(250,min_periods=1).max().values
    price_position=np.clip((C-low250)/(high250-low250+1e-9)*100,0,100)
    bullish_alignment=(MA5>MA10)&(MA10>MA20)&(MA20>MA60)
    high20=pd.Series(H).rolling(20,min_periods=1).max().values; high60=pd.Series(H).rolling(60,min_periods=1).max().values
    breakout_20=C>np.roll(high20,1); breakout_60=C>np.roll(high60,1)
    yang_strength=np.where(C>O,(C-O)/(O+1e-9)*100,0); strong_yang=yang_strength>3.0
    close_to_ma10=np.abs((C-MA10)/(MA10+1e-9)*100)<2.0; rebound_from_ma10=close_to_ma10&(C>MA10)&strong_yang&bullish_alignment
    trend_score=np.zeros(len(df))
    trend_score+=bullish_alignment*20+breakout_20*15+breakout_60*25+macd_positive*15+MACD_rising*10+rsi_strong*10+volume_surge*15+strong_yang*10+rebound_from_ma10*20
    return {'close':float(C[-1]),'trend_score':float(trend_score[-1]),'volume_ratio':float(volume_ratio[-1]),
            'price_position':float(price_position[-1]),'macd_rising':bool(MACD_rising[-1]),'macd_positive':bool(macd_positive[-1]),
            'bullish_alignment':bool(bullish_alignment[-1]),'breakout_20':bool(breakout_20[-1]),'breakout_60':bool(breakout_60[-1]),
            'strong_yang':bool(strong_yang[-1]),'rebound_from_ma10':bool(rebound_from_ma10[-1]),
            'volume_surge':bool(volume_surge[-1]),'MA5':float(MA5[-1]),'MA10':float(MA10[-1]),'MA20':float(MA20[-1])}


def calculate_pullback_indicators(df):
    """回踩确认指标 (Check_Tu50.py) - 返回完整 DataFrame"""
    df=df.copy(); C=df['close'].values; H=df['high'].values; L=df['low'].values; V=df['volume'].values
    llv9=pd.Series(L).rolling(9,min_periods=1).min().values; hhv9=pd.Series(H).rolling(9,min_periods=1).max().values
    rsv=(C-llv9)/(hhv9-llv9+1e-9)*100; K=td_sma(pd.Series(rsv),3,1); D_s=td_sma(pd.Series(K),3,1); J=3*K-2*D_s
    DIF=pd.Series(C).ewm(span=12,adjust=False).mean()-pd.Series(C).ewm(span=26,adjust=False).mean()
    DEA=DIF.ewm(span=9,adjust=False).mean(); MACD=DIF-DEA
    MACD_rising=(MACD>np.roll(MACD,1))&(np.roll(MACD,1)>np.roll(MACD,2))
    delta=np.diff(C,prepend=C[0]); gain=np.where(delta>0,delta,0); loss=np.where(delta<0,-delta,0)
    avg_gain=pd.Series(gain).rolling(14,min_periods=1).mean().values; avg_loss=pd.Series(loss).rolling(14,min_periods=1).mean().values
    RS=avg_gain/(avg_loss+1e-9); RSI14=100-100/(1+RS)
    volume_ma5=pd.Series(V).rolling(5,min_periods=1).mean().values; volume_ratio=V/(volume_ma5+1e-9)
    low250=pd.Series(L).rolling(250,min_periods=1).min().values; high250=pd.Series(H).rolling(250,min_periods=1).max().values
    price_position=np.clip((C-low250)/(high250-low250+1e-9)*100,0,100)
    MA5=pd.Series(C).rolling(5).mean().values; MA10=pd.Series(C).rolling(10).mean().values
    MA20=pd.Series(C).rolling(20).mean().values; MA60=pd.Series(C).rolling(60,min_periods=20).mean().values
    bullish_alignment=(MA5>MA10)&(MA10>MA20)&(MA20>MA60)
    high52=pd.Series(H).rolling(260,min_periods=1).max().values; near_52w_high=C>high52*0.95; break_52w_high=C>high52
    k_prev=np.roll(K,1);k_prev[0]=K[0];d_prev=np.roll(D_s,1);d_prev[0]=D_s[0]
    kdj_golden=(k_prev<d_prev)&(K>D_s)&(J<20); rsi_oversold=RSI14<30
    rsi_rising=(RSI14>np.roll(RSI14,1))&(np.roll(RSI14,1)<30); volume_surge=volume_ratio>1.2; price_low=price_position<30
    up_move=np.maximum(C-np.roll(C,1),0); down_move=np.abs(C-np.roll(C,1))
    up_ma=pd.Series(up_move).rolling(20,min_periods=1).mean().values; down_ma=pd.Series(down_move).rolling(20,min_periods=1).mean().values
    main_power=up_ma/(down_ma+1e-9)*100; main_power_rising=(main_power>np.roll(main_power,1))&(np.roll(main_power,1)<40)
    close_up=C>np.roll(C,1); price_up_volume_down=close_up&(volume_ratio<0.8)&(price_position>70)
    cn_score=np.zeros(len(df))
    cn_score+=kdj_golden*20+MACD_rising*10+rsi_oversold*15+volume_surge*10+price_low*15+main_power_rising*10+price_up_volume_down*5+rsi_rising*10
    us_score=np.zeros(len(df))
    us_score+=bullish_alignment*15+break_52w_high*25+near_52w_high*10+(RSI14>50)*15+MACD_rising*10+volume_surge*10+price_up_volume_down*10+(J>40)*5
    lookback=10;rebound_threshold=0.05;close_s=C
    low_roll=pd.Series(close_s).rolling(lookback,min_periods=1).min().values
    rebound=(close_s-low_roll)/(low_roll+1e-9)
    ma5_above_ma10=MA5>MA10;ma5_below_ma10=MA5<MA10;golden_cross=ma5_above_ma10&(np.roll(ma5_below_ma10,1))
    recent_golden=golden_cross.copy()
    for i in range(1,min(3,len(df))): recent_golden=recent_golden|np.roll(golden_cross,i)
    price_above_ma10=close_s>MA10;rebound_detected=rebound>=rebound_threshold
    pullback_confirm=recent_golden&price_above_ma10&rebound_detected
    df['cn_score']=cn_score;df['us_score']=us_score;df['volume_ratio']=volume_ratio;df['price_position']=price_position
    df['MA5']=MA5;df['MA10']=MA10;df['close']=C;df['MACD']=MACD;df['DIF']=DIF;df['DEA']=DEA
    df['K']=K;df['D']=D_s;df['J']=J;df['RSI14']=RSI14;df['pullback_confirm']=pullback_confirm
    return df


def calculate_macd(closes):
    s = pd.Series(closes)
    return (s.ewm(span=12,adjust=False).mean() - s.ewm(span=26,adjust=False).mean()).values


def detect_divergence(closes, macd_line):
    n = len(closes); pv = []; mv = []
    for i in range(5, n-5):
        if closes[i] == min(closes[i-5:i+6]): pv.append(i)
        v = macd_line[i]
        if not np.isnan(v):
            w = [x for x in macd_line[i-5:i+6] if not np.isnan(x)]
            if len(w) and v == min(w): mv.append(i)
    if len(pv) < 2 or len(mv) < 2: return False
    lp=pv[-1];pp=pv[-2];lm=mv[-1];pm=mv[-2]
    if abs(lp-lm) > 5: return False
    return closes[lp] < closes[pp] and macd_line[lm] > macd_line[pm]


# ============================================================
# FutuClient - 富途API封装
# ============================================================

class FutuClient:
    def __init__(self, host='127.0.0.1', port=11111):
        self.host = host
        self.port = port
        self.ctx = None
        self.subscribed = set()
        self.BATCH_SIZE = 250  # 每批250只 (250×1=250 ≤ 300额度上限)

    def ensure(self):
        if self.ctx is None:
            self.ctx = OpenQuoteContext(host=self.host, port=self.port)
            logger.info("Connected")

    def reconnect(self):
        self.close()
        time.sleep(1)
        self.ctx = OpenQuoteContext(host=self.host, port=self.port)
        logger.info("Reconnected, quota reset")

    def close(self):
        if self.ctx:
            try: self.ctx.close()
            except Exception: pass
            self.ctx = None
            self.subscribed = set()

    def get_stock_list(self, market):
        mm = {'HK': Market.HK, 'US': Market.US, 'SH': Market.SH, 'SZ': Market.SZ}
        m = mm.get(market.upper())
        if not m: raise ValueError(f"Invalid market: {market}")
        self.ensure()
        ret, data = self.ctx.get_stock_basicinfo(m, SecurityType.STOCK)
        return data['code'].tolist() if ret == RET_OK else []

    def get_snapshots(self, codes):
        self.ensure()
        valid = []
        for i in range(0, len(codes), 400):
            try:
                ret, snap = self.ctx.get_market_snapshot(codes[i:i+400])
                if ret == RET_OK and not snap.empty:
                    for _, row in snap.iterrows():
                        valid.append({'code': row['code'], 'volume': float(row.get('volume',0)),
                                      'price': float(row.get('last_price',0))})
            except Exception: pass
            time.sleep(0.2)
        return valid

    def get_kline(self, code, ktype='K_DAY', num=120):
        kl_map = {'K_DAY': KLType.K_DAY, 'K_WEEK': KLType.K_WEEK, 'K_MON': KLType.K_MON}
        kl = kl_map.get(ktype.upper())
        if not kl: raise ValueError(f"Invalid ktype: {ktype}")
        self.ensure()
        try:
            if code not in self.subscribed:
                self.ctx.subscribe([code], [SubType.K_DAY])
                self.subscribed = {code}
                time.sleep(0.3)
            ret, data = self.ctx.get_cur_kline(code, num, kl, autype=AuType.QFQ)
            if ret == RET_OK and len(data) >= 30:
                return data.sort_values('time_key').reset_index(drop=True)
        except: pass
        return None

    def _batch_kline(self, codes, num=200):
        """批量获取K线 - BATCH_SIZE=250只/批"""
        all_data = {}
        total = len(codes)
        bs = self.BATCH_SIZE
        for i in range(0, total, bs):
            batch = codes[i:i+bs]
            bn = i//bs + 1; tb = (total+bs-1)//bs
            logger.info(f"Kline {bn}/{tb} ({len(batch)} stocks)")
            if i > 0:
                logger.info("  Cooldown 60s...")
                time.sleep(60)
            self.reconnect()
            ret, msg = self.ctx.subscribe(batch, [SubType.K_DAY])
            if ret != RET_OK:
                logger.warning(f"Batch {bn} subscribe failed: {msg}")
                continue
            time.sleep(2.5)
            for code in batch:
                try:
                    r2, d = self.ctx.get_cur_kline(code, num, KLType.K_DAY, autype=AuType.QFQ)
                    if r2 == RET_OK and len(d) >= 30:
                        all_data[code] = d.sort_values('time_key').reset_index(drop=True)
                except: pass
                time.sleep(0.03)
            try: self.ctx.unsubscribe(batch, [SubType.K_DAY])
            except: pass
            logger.info(f"  Accumulated: {len(all_data)} stocks")
        return all_data

    def scan_market_all(self, market, min_volume=50000):
        """
        全市场4策略扫描 (无数量限制，全部扫描)
        返回: dict with pullback_ma5_ma10, macd_divergence, bottom_screening, strong_momentum
        """
        all_codes = self.get_stock_list(market)
        logger.info(f"Total {market} stocks: {len(all_codes)}")

        snaps = self.get_snapshots(all_codes)
        valid = [s['code'] for s in snaps if s['volume'] >= min_volume and s['price'] > 0]
        logger.info(f"After snapshot filter: {len(valid)} with volume>={min_volume}")

        if not valid: return {'error': '无符合条件的股票'}

        logger.info(f"Scanning ALL {len(valid)} stocks (batch={self.BATCH_SIZE})")
        day_data = self._batch_kline(valid)
        if not day_data: return {'error': '未获取到K线数据'}

        # 1. 5日线回踩10日线
        pb = []
        for code, df in day_data.items():
            try:
                dfi = calculate_pullback_indicators(df)
                last = dfi.iloc[-1]
                if last['pullback_confirm'] and last['cn_score'] >= 10:
                    pb.append({'code':code, 'score':round(float(last['cn_score']),1),
                               'close':round(float(last['close']),3),
                               'ma5':round(float(last['MA5']),3), 'ma10':round(float(last['MA10']),3)})
            except: pass
        pb.sort(key=lambda x: x['score'], reverse=True)

        # 2. MACD底背离
        div = []
        for code, df in day_data.items():
            try:
                macd_line = calculate_macd(df['close'].astype(float).tolist())
                if detect_divergence(df['close'].astype(float).tolist(), macd_line):
                    div.append(code)
            except: pass

        # 3. 底部筛选
        bot = []
        for code, df in day_data.items():
            try:
                f = calculate_bottom_indicators(df)
                if f['close'] == 0: continue
                if (f['pullback_signal'] and f['cn_score'] >= 15) or (f['cn_score'] >= 20 and f['price_position'] <= 50):
                    bot.append({'code':code, 'score':round(f['cn_score'],1), 'pos':round(f['price_position'],1)})
            except: pass
        bot.sort(key=lambda x: x['score'], reverse=True)

        # 4. 追高筛选
        strong = []
        for code, df in day_data.items():
            try:
                f = calculate_strong_indicators(df)
                if f['close'] == 0: continue
                if f['trend_score'] >= 40 and f['bullish_alignment'] and f['price_position'] > 50:
                    strong.append({'code':code, 'score':round(f['trend_score'],1), 'pos':round(f['price_position'],1)})
            except: pass
        strong.sort(key=lambda x: x['score'], reverse=True)

        return {
            'market': market, 'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_stocks': len(all_codes), 'after_snapshot_filter': len(valid),
            'scanned': len(valid), 'kline_obtained': len(day_data),
            'pullback_ma5_ma10': {'count': len(pb), 'results': pb},
            'macd_divergence': {'count': len(div), 'results': [{'code':c} for c in div]},
            'bottom_screening': {'count': len(bot), 'results': bot},
            'strong_momentum': {'count': len(strong), 'results': strong},
        }
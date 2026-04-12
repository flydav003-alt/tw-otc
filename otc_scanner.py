"""
上櫃操盤手選股系統 v7.1 — 新增綜合分 (composite_score) + 三區塊 HTML
修改摘要（v7.0 → v7.1）：
  ① export_csvs()        — 新增 composite_score 欄位
  ② export_html()        — HTML 重構為三區塊，各區塊嵌入 Top5 K線圖
  ③ main()               — 新增 composite_charts 繪製流程
  ④ 系統常數             — 新增 TOP_COMPOSITE = 8
"""

# ============================================================
# 區塊 0：安裝字型（GitHub Actions 環境）
# ============================================================
import subprocess
import sys
import os

def install_system_deps():
    """安裝中文字型（GitHub Actions Ubuntu 環境）"""
    try:
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "-q", "fonts-noto-cjk"],
            capture_output=True, check=False
        )
        print("✅ 中文字型安裝完成")
    except Exception as e:
        print(f"⚠️  字型安裝失敗（不影響主流程）：{e}")

# ============================================================
# 區塊 1：載入套件
# ============================================================
import time
import warnings
import base64
import io
import pickle
import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import mplfinance as mpf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib as mpl
from scipy import stats as scipy_stats
from FinMind.data import DataLoader

warnings.filterwarnings('ignore')
pd.set_option('display.max_columns', 30)

# ============================================================
# 區塊 2：設定
# ============================================================

FINMIND_TOKEN   = os.environ.get("FINMIND_TOKEN", "")
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER      = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASS", "")
EMAIL_TO        = os.environ.get("EMAIL_TO", "")
GITHUB_PAGES_URL = os.environ.get("REPORT_URL", "")

OTC_CSV_PATH = os.environ.get("OTC_CSV_PATH", "otc200.csv")

A_VOL_MA5_MIN     = 800
A_TURNOVER_MIN    = 80_000_000
A_PRICE_MIN       = 10
A_LIMIT_DAYS      = 3
A_LIMIT_THRESHOLD = 0.095

B1_VOL_RATIO_MIN  = 1.5
B2_RETURN_MIN     = 0.01
B4_CLOSE_RATIO    = 0.65
B_PASS_COUNT      = 2

C_CONSEC_DAYS_MIN = 3
C_SINGLE_MIN      = 200

D_RSI_MAX    = 78
D_RETURN_MAX = 0.10

W_VOL_RATIO  = 1.6
W_HIGH20     = 1.4
W_MA28_BIAS  = 1.0
W_INST_DAYS  = 3.0
W_RETURN_PCT = 0.8

EW_VOL_RATIO_MIN  = 1.10
EW_VOL_RATIO_MAX  = 2.8
EW_RETURN_MAX     = 6.5
EW_RETURN_MIN     = -2.5
EW_MA28_BIAS_MAX  = 18.5
EW_CONSOL_RATIO   = 1.12
EW_TURNOVER_MIN   = 90_000_000
EW_ABOVE_MA20_MIN = 0
EW_MAX20D_RET_MAX = 20.0
EW_INST_MIN       = 80
EW_PAST60D_MAX    = 60.0
EW_PAST60D_BIAS   = 20.0

EW_BONUS_YOY      = 16.0
EW_BONUS_INST     = 24.0
EW_BONUS_60D      = 22.0

TOP_STRONG   = 10
TOP_EARLY    = 10
TOP_CHART    = 5
# ════════════════════════════════════════════
# ★ 新增常數（v7.1）
TOP_COMPOSITE = 10   # 綜合分 Top 10
# ════════════════════════════════════════════
MIN_DAYS     = 60
BATCH_SIZE   = 40
BATCH_DELAY  = 1.5
ERROR_LOG    = "error_log.txt"

TODAY      = datetime.today()
END_DATE   = TODAY.strftime('%Y-%m-%d')
START_DATE = (TODAY - timedelta(days=400)).strftime('%Y-%m-%d')
TODAY_STR  = TODAY.strftime('%Y%m%d')
TODAY_DISP = TODAY.strftime('%Y/%m/%d')

print(f"[系統] 啟動時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"[系統] 資料區間：{START_DATE} → {END_DATE}")

# ============================================================
# 區塊 3：字型初始化
# ============================================================

def init_chinese_font():
    mpl.rcParams['axes.unicode_minus'] = False
    candidate_paths = [
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf',
        '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJKtc-Regular.otf',
    ]
    found = next((p for p in candidate_paths if os.path.exists(p)), None)
    if found is None:
        print("  ⚠️  找不到中文字型，K線標題將使用英文")
        return None, None
    try:
        prop = fm.FontProperties(fname=found)
        font_name = prop.get_name()
        mpl.rcParams['font.sans-serif'] = [font_name, 'DejaVu Sans']
        fm.fontManager.addfont(found)
        print(f"  ✅ 中文字型：{font_name}（{found}）")
        return found, prop
    except Exception as e:
        print(f"  ⚠️  字型載入失敗：{e}")
        return None, None

# ============================================================
# 區塊 4：工具函式
# ============================================================

def log_error(msg):
    with open(ERROR_LOG, 'a', encoding='utf-8') as f:
        f.write(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}\n')

def calc_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast,   adjust=False).mean()
    ema_s = series.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal, adjust=False).mean()
    return macd - sig

def consec_buy_days(series):
    if series is None or len(series) == 0:
        return 0
    vals  = series.dropna().values[::-1]
    count = 0
    for v in vals:
        if v > 0:
            count += 1
        else:
            break
    return count

def safe_zscore(arr):
    a = np.array(arr, dtype=float)
    if len(a) < 2 or np.nanstd(a) == 0:
        return np.zeros_like(a)
    return scipy_stats.zscore(a, nan_policy='omit')

def calc_indicators(df):
    if df is None or df.empty:
        return None
    df = df.rename(columns={
        'max': 'high', 'min': 'low',
        'Trading_Volume': 'volume', 'Trading_money': 'turnover',
    })
    for col in ['date','open','high','low','close','volume']:
        if col not in df.columns:
            return None
    df = df.sort_values('date').reset_index(drop=True)
    for col in ['open','high','low','close','volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close','volume'])
    if len(df) < MIN_DAYS:
        return None
    if 'turnover' in df.columns:
        df['turnover'] = pd.to_numeric(df['turnover'], errors='coerce').fillna(0)
        m = df['turnover'] <= 0
        df.loc[m, 'turnover'] = df.loc[m,'close'] * df.loc[m,'volume'] * 1000
    else:
        df['turnover'] = df['close'] * df['volume'] * 1000

    df['MA5']          = df['close'].rolling(5).mean()
    df['MA20']         = df['close'].rolling(20).mean()
    df['MA28']         = df['close'].rolling(28).mean()
    df['vol_ma5']      = df['volume'].rolling(5).mean()
    df['high20']       = df['high'].rolling(20).max()
    df['daily_return'] = df['close'].pct_change()
    df['RSI14']        = calc_rsi(df['close'], 14)
    hist               = calc_macd(df['close'])
    df['MACD_hist']      = hist
    df['MACD_hist_prev'] = hist.shift(1)
    df['amplitude']    = df['high'] - df['low']
    return df

def fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor='#0d1117')
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')

# ============================================================
# 區塊 5：讀取 CSV + FinMind 登入
# ============================================================

def load_stock_list():
    df_csv = None
    for enc in ['cp950','utf-8-sig','utf-8','big5','latin1']:
        try:
            df_csv = pd.read_csv(OTC_CSV_PATH, encoding=enc, dtype=str)
            print(f'✅ CSV 讀取成功（{enc}），共 {len(df_csv)} 筆')
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except FileNotFoundError:
            raise FileNotFoundError(f'找不到 {OTC_CSV_PATH}，請先上傳到倉庫根目錄')

    df_csv.columns = df_csv.columns.str.strip()
    df_csv['stock_id'] = df_csv['stock_id'].astype(str).str.strip()
    df_csv['name']     = df_csv['name'].astype(str).str.strip()
    df_csv = df_csv[df_csv['stock_id'].str.match(r'^\d{4,5}$')].copy()
    stock_ids = df_csv['stock_id'].tolist()
    name_map  = dict(zip(df_csv['stock_id'], df_csv['name']))
    return stock_ids, name_map

def load_industry_map():
    industry_map = {}
    try:
        r = requests.get(
            'https://api.finmindtrade.com/api/v4/data',
            params={'dataset': 'TaiwanStockInfo', 'token': FINMIND_TOKEN},
            timeout=30
        )
        rj = r.json()
        if rj.get('status') == 200 and rj.get('data'):
            df_info = pd.DataFrame(rj['data'])
            if 'industry_category' in df_info.columns:
                industry_map = dict(zip(
                    df_info['stock_id'].astype(str),
                    df_info['industry_category'].fillna('')
                ))
        print(f'✅ 產業別載入：{len(industry_map)} 檔')
    except Exception as e:
        print(f'⚠️  產業別載入失敗：{e}')
    return industry_map

def login_finmind():
    api = DataLoader()
    try:
        api.login_by_token(api_token=FINMIND_TOKEN)
        print('✅ FinMind 登入成功')
    except Exception as e:
        print(f'❌ 登入失敗：{e}')
        raise
    return api

def detect_api_mode(api, stock_ids):
    test_sid = stock_ids[0]
    try:
        t = api.taiwan_stock_daily(stock_id=test_sid, start_date=START_DATE, end_date=END_DATE)
        if isinstance(t, pd.DataFrame) and not t.empty:
            print(f'✅ SDK 方式正常（{len(t)} 筆）')
            return False
        raise ValueError('empty')
    except Exception as e:
        print(f'SDK 失敗（{e}），改用 REST API')
        return True

# ============================================================
# 區塊 6：抓取 K 線
# ============================================================

def fetch_price(sid, api, use_rest):
    try:
        if use_rest:
            r = requests.get(
                'https://api.finmindtrade.com/api/v4/data',
                params={'dataset':'TaiwanStockPrice','data_id':sid,
                        'start_date':START_DATE,'end_date':END_DATE,
                        'token':FINMIND_TOKEN}, timeout=20)
            rj = r.json()
            return pd.DataFrame(rj['data']) if rj.get('status')==200 and rj.get('data') else None
        else:
            raw = api.taiwan_stock_daily(stock_id=sid, start_date=START_DATE, end_date=END_DATE)
            return raw if isinstance(raw, pd.DataFrame) and not raw.empty else None
    except Exception as e:
        log_error(f'{sid} K線：{e}')
        return None

def fetch_all_prices(stock_ids, api, use_rest):
    price_data = {}
    total   = len(stock_ids)
    batches = (total - 1) // BATCH_SIZE + 1
    print(f'[K線抓取] {total} 檔，{batches} 批次...')
    for i in range(0, total, BATCH_SIZE):
        batch    = stock_ids[i:i+BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f'  批次 {batch_no}/{batches}...', end=' ', flush=True)
        ok = 0
        for sid in batch:
            raw  = fetch_price(sid, api, use_rest)
            if raw is None:
                continue
            proc = calc_indicators(raw)
            if proc is not None:
                price_data[sid] = proc
                ok += 1
        print(f'✓{ok} ✗{len(batch)-ok}  累計 {len(price_data)} 檔')
        if i + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)
    print(f'\n✅ K線完成  有效 {len(price_data)} / {total} 檔')
    return price_data

# ============================================================
# 區塊 7：抓取籌碼
# ============================================================

def parse_inst(raw):
    if raw is None or raw.empty or 'name' not in raw.columns:
        return None, None
    df = raw.sort_values('date').copy()
    def net_s(kws):
        pat = '|'.join(kws)
        sub = df[df['name'].str.contains(pat, na=False)].copy()
        if sub.empty:
            return pd.Series(dtype=float)
        sub['net'] = (pd.to_numeric(sub['buy'],  errors='coerce').fillna(0) -
                      pd.to_numeric(sub['sell'], errors='coerce').fillna(0))
        return sub.groupby('date')['net'].sum()
    return net_s(['Foreign_Investor','外資']), net_s(['Investment_Trust','投信'])

def fetch_all_inst(valid_ids, api, use_rest):
    EMPTY = {'foreign_consec':0,'trust_consec':0,'foreign_today':0.0,'trust_today':0.0,
             'foreign_3d':0.0,'trust_3d':0.0}
    inst_data = {sid: dict(EMPTY) for sid in valid_ids}
    total   = len(valid_ids)
    batches = (total - 1) // BATCH_SIZE + 1
    print(f'[籌碼抓取] {total} 檔...')
    for i in range(0, total, BATCH_SIZE):
        batch    = valid_ids[i:i+BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f'  批次 {batch_no}/{batches}...', end=' ', flush=True)
        ok = 0
        for sid in batch:
            try:
                if use_rest:
                    r  = requests.get(
                        'https://api.finmindtrade.com/api/v4/data',
                        params={'dataset':'TaiwanStockInstitutionalInvestors',
                                'data_id':sid,'start_date':START_DATE,
                                'end_date':END_DATE,'token':FINMIND_TOKEN}, timeout=20)
                    rj = r.json()
                    raw = pd.DataFrame(rj['data']) if rj.get('status')==200 and rj.get('data') else None
                else:
                    raw = api.taiwan_stock_institutional_investors(
                        stock_id=sid, start_date=START_DATE, end_date=END_DATE)
                    if not isinstance(raw, pd.DataFrame) or raw.empty:
                        raw = None
                f_net, t_net = parse_inst(raw)
                inst_data[sid] = {
                    'foreign_consec': consec_buy_days(f_net),
                    'trust_consec':   consec_buy_days(t_net),
                    'foreign_today':  float(f_net.iloc[-1]) if f_net is not None and len(f_net)>0 else 0.0,
                    'trust_today':    float(t_net.iloc[-1]) if t_net is not None and len(t_net)>0 else 0.0,
                    'foreign_3d':     float(f_net.iloc[-3:].sum()) if f_net is not None and len(f_net)>=3 else 0.0,
                    'trust_3d':       float(t_net.iloc[-3:].sum()) if t_net is not None and len(t_net)>=3 else 0.0,
                }
                ok += 1
            except Exception as e:
                log_error(f'{sid} 籌碼：{e}')
        print(f'OK {ok}/{len(batch)}')
        if i + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)
    print('✅ 籌碼完成')
    return inst_data

# ============================================================
# 區塊 8：抓取月營收 YoY
# ============================================================

def calc_yoy_revenue(sid, api, use_rest):
    try:
        if use_rest:
            r = requests.get(
                'https://api.finmindtrade.com/api/v4/data',
                params={'dataset':'TaiwanStockMonthRevenue','data_id':sid,
                        'start_date':(TODAY - timedelta(days=400)).strftime('%Y-%m-%d'),
                        'end_date':END_DATE,'token':FINMIND_TOKEN}, timeout=20)
            rj = r.json()
            rev_df = pd.DataFrame(rj['data']) if rj.get('status')==200 and rj.get('data') else None
        else:
            rev_df = api.taiwan_stock_month_revenue(
                stock_id=sid,
                start_date=(TODAY - timedelta(days=400)).strftime('%Y-%m-%d'),
                end_date=END_DATE)
            if not isinstance(rev_df, pd.DataFrame) or rev_df.empty:
                rev_df = None

        if rev_df is None or rev_df.empty:
            return None
        rev_df = rev_df.sort_values('date').reset_index(drop=True)
        rev_col = next((c for c in ['revenue','Revenue','monthly_revenue'] if c in rev_df.columns), None)
        if rev_col is None:
            return None
        rev_df[rev_col] = pd.to_numeric(rev_df[rev_col], errors='coerce')
        rev_df = rev_df.dropna(subset=[rev_col])
        if len(rev_df) < 13:
            return None
        latest_rev = rev_df[rev_col].iloc[-1]
        prev_rev   = rev_df[rev_col].iloc[-13]
        if prev_rev <= 0 or np.isnan(prev_rev):
            return None
        return round((latest_rev - prev_rev) / abs(prev_rev) * 100, 1)
    except Exception as e:
        log_error(f'{sid} 月營收YoY：{e}')
        return None

def fetch_all_revenue(valid_ids, api, use_rest):
    fin_data = {}
    total   = len(valid_ids)
    batches = (total - 1) // BATCH_SIZE + 1
    print(f'[月營收抓取] {total} 檔...')
    for i in range(0, total, BATCH_SIZE):
        batch    = valid_ids[i:i+BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f'  批次 {batch_no}/{batches}...', end=' ', flush=True)
        ok = fail = 0
        for sid in batch:
            yoy = calc_yoy_revenue(sid, api, use_rest)
            fin_data[sid] = yoy
            if yoy is not None:
                ok += 1
            else:
                fail += 1
        print(f'有YoY {ok} / 無資料 {fail}')
        if i + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)
    print(f'✅ 月營收完成  有YoY {sum(1 for v in fin_data.values() if v is not None)} / {total} 檔')
    return fin_data

# ============================================================
# 區塊 9：篩選模組（A/B/C/D）— 強勢確認股
# ============================================================

def compute_limit_flag(df):
    if len(df) < A_LIMIT_DAYS:
        return False
    r = df.tail(A_LIMIT_DAYS)['daily_return'].fillna(0)
    return all(v >= A_LIMIT_THRESHOLD for v in r) or all(v <= -A_LIMIT_THRESHOLD for v in r)

def module_a(r):
    if r.get('vol_ma5',0)        <= A_VOL_MA5_MIN:  return False
    if r.get('turnover_today',0) <= A_TURNOVER_MIN: return False
    if r.get('close',0)          <= A_PRICE_MIN:    return False
    if r.get('limit_flag',False):                    return False
    return True

def module_b(r):
    signals, close = [], r.get('close',0)
    if r.get('vol_ratio',0) >= B1_VOL_RATIO_MIN:
        signals.append(f'爆量{r["vol_ratio"]:.1f}倍')
    if close >= (r.get('high20') or float('inf')) and r.get('daily_return',0) > B2_RETURN_MIN:
        signals.append('突破20日高點')
    ma5, ma28 = r.get('MA5',0) or 0, r.get('MA28',0) or 0
    if close > ma28 > 0 and close > ma5 > 0:
        signals.append('均線多頭排列')
    h, l, o = r.get('high',0), r.get('low',0), r.get('open',0)
    hl = h - l
    if close > o and hl > 0 and close >= l + hl * B4_CLOSE_RATIO:
        signals.append('強勢紅K收盤')
    return len(signals) >= B_PASS_COUNT, signals

def module_c(sid, inst_data):
    info, signals = inst_data.get(sid,{}), []
    for tag, ck, tk in [('外資','foreign_consec','foreign_today'),('投信','trust_consec','trust_today')]:
        c, t = info.get(ck,0), info.get(tk,0)
        if c >= C_CONSEC_DAYS_MIN:   signals.append(f'{tag}連買{c}天')
        elif t >= C_SINGLE_MIN:      signals.append(f'{tag}買超{int(t)}張')
    return len(signals) >= 1, signals

def module_d(r):
    rsi  = r.get('RSI14',0)
    ret  = r.get('daily_return',0)
    macd = r.get('MACD_hist',None)
    macp = r.get('MACD_hist_prev',None)
    if rsi >= D_RSI_MAX and ret >= D_RETURN_MAX:
        return False
    if macd is not None and macp is not None and not np.isnan(macd) and not np.isnan(macp):
        if macd < -0.5 and macd < macp:
            return False
    return True

# ============================================================
# 區塊 10：執行強勢確認股篩選 + 評分
# ============================================================

def run_strong_filter(price_data, inst_data, fin_data, name_map, industry_map):
    funnel = {'總有效':len(price_data),'A流動性':0,'B技術':0,'C籌碼':0,'D過濾':0}
    candidates = []

    for sid, df in price_data.items():
        if df is None or df.empty:
            continue
        last = df.iloc[-1].to_dict()
        last['stock_id'] = sid
        vm5 = last.get('vol_ma5',0) or 0
        last['vol_ratio']      = (last.get('volume',0)/vm5) if vm5>0 else 0
        last['turnover_today'] = last.get('turnover',0) or 0
        last['limit_flag']     = compute_limit_flag(df)

        if not module_a(last): continue
        funnel['A流動性'] += 1
        b_ok, b_sig = module_b(last)
        if not b_ok: continue
        funnel['B技術'] += 1
        c_ok, c_sig = module_c(sid, inst_data)
        if not c_ok: continue
        funnel['C籌碼'] += 1
        if not module_d(last): continue
        funnel['D過濾'] += 1

        info        = inst_data.get(sid,{})
        inst_consec = max(info.get('foreign_consec',0), info.get('trust_consec',0))
        ma28        = last.get('MA28',0) or 0
        ma28_bias   = ((last['close']-ma28)/ma28*100) if ma28>0 else 0
        dpct        = last.get('daily_return',0)*100
        h20         = 1.0 if last.get('close',0) >= (last.get('high20') or float('inf')) else 0.0

        score = (last['vol_ratio'] * W_VOL_RATIO +
                 h20 * W_HIGH20 +
                 ma28_bias * W_MA28_BIAS +
                 inst_consec * W_INST_DAYS +
                 dpct * W_RETURN_PCT)

        yoy_rev = fin_data.get(sid, None)
        if len(df) >= 61:
            c60 = df['close'].iloc[-61]
            past_60d = ((last.get('close',0) - c60) / c60 * 100) if c60 > 0 else 0.0
        else:
            past_60d = 0.0

        candidates.append({
            'stock_id': sid, 'name': name_map.get(sid,sid),
            'industry': industry_map.get(sid, ''),
            'score': round(score,2),
            'close': last.get('close',0),
            'turnover_today': last.get('turnover_today',0),
            'vol_ratio': round(last['vol_ratio'],2),
            'ma28_bias': round(ma28_bias,2),
            'daily_return_pct': round(dpct,2),
            'rsi14': round(last.get('RSI14',0) or 0,1),
            'inst_consec': inst_consec,
            'foreign_today': info.get('foreign_today',0),
            'trust_today': info.get('trust_today',0),
            'signal_b': ' + '.join(b_sig),
            'signal_c': ' + '.join(c_sig),
            'signal': ' | '.join(b_sig+c_sig),
            'hold_days': 2 if score>15 else 1,
            'strength': '強' if score>18 else ('中' if score>=12 else '弱'),
            'yoy_revenue_pct': yoy_rev,
            'past_60d_cum': round(past_60d, 1),
            '_vr': last['vol_ratio'], '_mb': ma28_bias,
            '_ic': float(inst_consec), '_dp': dpct, '_h20': h20,
        })

    if len(candidates) >= 2:
        vz = safe_zscore([c['_vr'] for c in candidates])
        mz = safe_zscore([c['_mb'] for c in candidates])
        iz = safe_zscore([c['_ic'] for c in candidates])
        dz = safe_zscore([c['_dp'] for c in candidates])
        hz = safe_zscore([c['_h20'] for c in candidates])
        w_sum = W_VOL_RATIO + W_HIGH20 + W_MA28_BIAS + W_INST_DAYS + W_RETURN_PCT
        for i, c in enumerate(candidates):
            z = (W_VOL_RATIO/w_sum * vz[i] + W_HIGH20/w_sum * mz[i] +
                 W_MA28_BIAS/w_sum * iz[i] + W_INST_DAYS/w_sum * dz[i] +
                 W_RETURN_PCT/w_sum * hz[i])
            c['z_score']    = round(float(z),3)
            c['total_score'] = round(c['score'] + float(z),2)
    else:
        for c in candidates:
            c['z_score'] = 0.0
            c['total_score'] = c['score']

    for c in candidates:
        if c.get('ma28_bias',0) > 35:      c['total_score'] -= 18
        elif c.get('ma28_bias',0) > 25:    c['total_score'] -= 10
        if c.get('daily_return_pct',0) > 9.5: c['total_score'] -= 12
        if c.get('rsi14',0) > 78:          c['total_score'] -= 8
        c['total_score'] = round(max(c['total_score'], 0), 2)

    strong_df = (pd.DataFrame(candidates)
                 .sort_values('total_score', ascending=False)
                 .reset_index(drop=True))
    if not strong_df.empty:
        strong_df.insert(0, 'rank', range(1, len(strong_df)+1))

    print(f'\n【強勢確認股漏斗】')
    base = funnel['總有效'] or 1
    for k, v in funnel.items():
        print(f'  {k}：{v} 檔 ({v/base*100:.1f}%)')
    print(f'強勢確認股候選：{len(candidates)} 檔')
    return strong_df, candidates

# ============================================================
# 區塊 11：起漲預警篩選 + 評分
# ============================================================

def run_early_filter(price_data, inst_data, fin_data, name_map, industry_map):
    candidates = []

    for sid, df in price_data.items():
        if df is None or len(df) < 30:
            continue
        last      = df.iloc[-1].to_dict()
        vm5       = last.get('vol_ma5', 0) or 0
        if vm5 <= 0:
            continue
        vol_ratio = last.get('volume', 0) / vm5
        dpct      = last.get('daily_return', 0) * 100
        close     = last.get('close', 0)
        ma20      = last.get('MA20', 0) or 0
        ma28      = last.get('MA28', 0) or 0
        ma28_bias = ((close - ma28) / ma28 * 100) if ma28 > 0 else 0
        to_day    = last.get('turnover', 0) or 0
        rsi14     = last.get('RSI14', 0) or 0
        amp10     = df['amplitude'].tail(10).mean()
        amp20_val = df['amplitude'].tail(20).mean()
        consol    = (amp10 / amp20_val) if amp20_val > 0 else 999

        if len(df) >= 61:
            c60d = df['close'].iloc[-61]
            past_60d = ((close - c60d) / c60d * 100) if c60d > 0 else 0
        else:
            past_60d = 0.0

        if to_day < EW_TURNOVER_MIN:                        continue
        if not (EW_VOL_RATIO_MIN <= vol_ratio <= EW_VOL_RATIO_MAX): continue
        if not (EW_RETURN_MIN <= dpct <= EW_RETURN_MAX):   continue
        if ma28_bias > EW_MA28_BIAS_MAX:                   continue
        if consol >= EW_CONSOL_RATIO:                      continue
        ret20 = df['daily_return'].tail(20) * 100
        if ret20.max() >= EW_MAX20D_RET_MAX:               continue
        tail7    = df.tail(7)
        below_ma = (tail7['close'] <= tail7['MA20']).sum()
        if below_ma < EW_ABOVE_MA20_MIN:                   continue
        if close < ma20 * 0.975:                           continue

        info        = inst_data.get(sid, {})
        f_today     = info.get('foreign_today', 0)
        t_today     = info.get('trust_today',  0)
        inst_consec = max(info.get('foreign_consec', 0), info.get('trust_consec', 0))

        def normalize_shares(v):
            return v / 1000 if abs(v) > 1_000_000 else v

        f_today_n = normalize_shares(f_today)
        yoy = fin_data.get(sid, None)

        vol_ratio_score = vol_ratio * 15
        consol_score    = max(0, (1.20 - consol)) * 14
        inst_score = (1 if inst_consec >= 2 or f_today_n >= 80 else 0) * 18
        ew_score = 0.35*vol_ratio_score + 0.30*consol_score + 0.35*inst_score

        bonus_total = 8.0
        bonus_flags = []
        if yoy is not None and not pd.isna(yoy):
            yov = float(yoy)
            if yov > 80:
                bonus_total += EW_BONUS_YOY
                bonus_flags.append(f'營收YoY+{yov:.0f}%✨')
            elif yov > 30:
                bonus_total += EW_BONUS_YOY * 0.7
                bonus_flags.append(f'營收YoY+{yov:.0f}%')
            else:
                bonus_total += EW_BONUS_YOY * 0.4
                bonus_flags.append(f'營收YoY{yov:.0f}%')

        if inst_consec >= 2 or f_today_n > 80:
            bonus_total += EW_BONUS_INST
            bonus_flags.append(f'法人連買{inst_consec}天')
        if past_60d < 25:
            bonus_total += EW_BONUS_60D
            bonus_flags.append(f'低位階{past_60d:.0f}%')

        ew_score += bonus_total

        if dpct > 6.5:      ew_score -= 8
        if ma28_bias > 18.5: ew_score -= 9
        elif ma28_bias > 15.0: ew_score -= 5

        total_ew_score = round(max(ew_score, 0), 2)

        inst_sig = []
        if f_today_n >= EW_INST_MIN:   inst_sig.append(f'外資+{int(f_today_n)}張')
        elif f_today_n < -EW_INST_MIN: inst_sig.append(f'外資賣{int(abs(f_today_n))}張')
        if t_today >= EW_INST_MIN:     inst_sig.append(f'投信+{int(t_today)}張')
        if not inst_sig:               inst_sig = ['法人觀望']

        tech_sig_str = f'量比{vol_ratio:.2f}倍 | 收斂{consol:.2f}'
        inst_sig_str = ' + '.join(inst_sig)
        if bonus_flags:
            inst_sig_str += '\n' + ' | '.join(bonus_flags)

        candidates.append({
            'stock_id':          sid,
            'name':              name_map.get(sid, sid),
            'industry':          industry_map.get(sid, ''),
            'total_ew_score':    total_ew_score,
            'ew_score':          total_ew_score,
            'tech_score':        round(0.35*vol_ratio_score + 0.30*consol_score + 0.35*inst_score, 2),
            'bonus_score':       round(bonus_total, 2),
            'close':             close,
            'turnover_today':    to_day,
            'vol_ratio':         round(vol_ratio, 2),
            'ma28_bias':         round(ma28_bias, 2),
            'daily_return_pct':  round(dpct, 2),
            'rsi14':             round(rsi14, 1),
            'consol_ratio':      round(consol, 2),
            'past_60d_cum':      round(past_60d, 1),
            'yoy_revenue_pct':   yoy,
            'inst_consec_days':  inst_consec,
            'foreign_today':     f_today,
            'trust_today':       t_today,
            'days_below_ma20':   int(below_ma),
            'bonus_flags':       ' | '.join(bonus_flags) if bonus_flags else '-',
            'tech_signal':       tech_sig_str,
            'inst_signal':       inst_sig_str,
            'signal':            tech_sig_str + '\n' + inst_sig_str,
        })

    if candidates:
        early_df = (pd.DataFrame(candidates)
                    .sort_values('total_ew_score', ascending=False)
                    .reset_index(drop=True))
        early_df.insert(0, 'rank', range(1, len(early_df)+1))
    else:
        early_df = pd.DataFrame()

    print(f'起漲預警候選：{len(candidates)} 檔')
    return early_df, candidates

# ============================================================
# 區塊 12：繪製 K 線圖
# ============================================================

def draw_kline(sid, price_data, name_map, font_path, label=''):
    df_p = price_data.get(sid)
    if df_p is None or len(df_p) < 30:
        return None

    df_p = df_p.tail(60).copy()
    df_p['date'] = pd.to_datetime(df_p['date'])
    df_p = df_p.set_index('date')
    df_p = df_p.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    for c in ['Open','High','Low','Close','Volume']:
        if c not in df_p.columns:
            return None

    add_plots = []
    for ma_col, color, lw in [('MA5','#F5A623',1.0),('MA20','#4A90E2',1.0),('MA28','#BD10E0',1.2)]:
        if ma_col in df_p.columns:
            add_plots.append(mpf.make_addplot(df_p[ma_col], panel=0, color=color, width=lw))

    if 'RSI14' in df_p.columns:
        add_plots.append(mpf.make_addplot(df_p['RSI14'], panel=2, color='#E8D44D', width=1.2, ylabel='RSI', ylim=(0,100)))
        add_plots.append(mpf.make_addplot(pd.Series(70, index=df_p.index), panel=2, color='#ff4444', width=0.6, linestyle='--'))
        add_plots.append(mpf.make_addplot(pd.Series(30, index=df_p.index), panel=2, color='#44ff44', width=0.6, linestyle='--'))

    if 'MACD_hist' in df_p.columns:
        colors = ['#26a641' if v>=0 else '#f85149' for v in df_p['MACD_hist'].fillna(0)]
        add_plots.append(mpf.make_addplot(df_p['MACD_hist'], panel=3, type='bar', color=colors, ylabel='MACD'))

    arrow = pd.Series(np.nan, index=df_p.index)
    arrow.iloc[-1] = df_p['Low'].iloc[-1] * 0.982
    add_plots.append(mpf.make_addplot(arrow, panel=0, type='scatter', markersize=130, marker='^', color='#FFD700'))

    mc = mpf.make_marketcolors(up='#f85149', down='#26a641', edge='inherit', wick='inherit',
                                volume={'up':'#f85149','down':'#26a641'})

    mpl.rcParams['axes.unicode_minus'] = False
    if font_path:
        rc_font = fm.FontProperties(fname=font_path).get_name()
    else:
        rc_font = 'DejaVu Sans'

    style = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc,
                                rc={'font.family': rc_font,
                                    'axes.labelcolor':'#c9d1d9',
                                    'xtick.color':'#c9d1d9',
                                    'ytick.color':'#c9d1d9'})

    name = name_map.get(sid, sid)
    title_str = f'  {sid} {name}  {label}' if font_path else f'  {sid}  {label}'

    try:
        fig, _ = mpf.plot(
            df_p[['Open','High','Low','Close','Volume']],
            type='candle', style=style,
            title=title_str,
            volume=True, addplot=add_plots,
            panel_ratios=(4,1,1.2,1.2),
            figsize=(14, 10), returnfig=True,
            warn_too_much_data=200,
        )
        b64 = fig_to_base64(fig)
        plt.close(fig)
        return b64
    except Exception as e:
        log_error(f'{sid} K線圖失敗：{e}')
        return None

# ============================================================
# ★ 區塊 13：輸出 CSV（新增 composite_score 欄位）
# ★ 修改重點（v7.1）
# ============================================================

def export_csvs(price_data, inst_data, fin_data, name_map, strong_df, early_df):
    strong_set = set(strong_df['stock_id'].tolist()) if not strong_df.empty else set()
    early_set  = set(early_df['stock_id'].tolist())  if not early_df.empty else set()
    strong_score_map = ({r['stock_id']: r.get('total_score',0) for _,r in strong_df.iterrows()}
                        if not strong_df.empty else {})
    early_score_map  = ({r['stock_id']: r.get('ew_score',0) for _,r in early_df.iterrows()}
                        if not early_df.empty else {})

    full_rows = []
    for sid, df in price_data.items():
        if df is None or df.empty:
            continue
        last   = df.iloc[-1].to_dict()
        vm5    = last.get('vol_ma5', 0) or 0
        close  = last.get('close', 0)
        ma28   = last.get('MA28',  0) or 0
        vol_r  = (last.get('volume', 0) / vm5) if vm5 > 0 else 0
        dpct   = last.get('daily_return', 0) * 100
        mb     = ((close - ma28) / ma28 * 100) if ma28 > 0 else 0
        to_day = last.get('turnover', 0) or 0
        info   = inst_data.get(sid, {})
        f_today = info.get('foreign_today', 0)
        t_today = info.get('trust_today',  0)
        f_3d    = info.get('foreign_3d',   0)
        t_3d    = info.get('trust_3d',     0)
        inst_c  = max(info.get('foreign_consec', 0), info.get('trust_consec', 0))
        yoy_rev = fin_data.get(sid, None)
        is_strong = sid in strong_set
        is_early  = sid in early_set

        reject_parts = []
        if vm5 <= A_VOL_MA5_MIN:     reject_parts.append(f'均量{vm5:.0f}≤{A_VOL_MA5_MIN}張')
        if to_day <= A_TURNOVER_MIN: reject_parts.append(f'成交額不足')
        if close <= A_PRICE_MIN:     reject_parts.append(f'股價{close}≤{A_PRICE_MIN}元')
        if compute_limit_flag(df):   reject_parts.append('連停排除')
        ma5 = last.get('MA5', 0) or 0
        b_signals = []
        if vol_r >= B1_VOL_RATIO_MIN: b_signals.append('爆量')
        high20 = last.get('high20') or 0
        if close >= high20 and dpct/100 > B2_RETURN_MIN: b_signals.append('20日新高')
        if close > ma28 > 0 and close > ma5 > 0:          b_signals.append('均線多頭')
        hl = last.get('high',0) - last.get('low',0)
        if close > last.get('open',0) and hl>0 and close >= last.get('low',0)+hl*B4_CLOSE_RATIO:
            b_signals.append('強勢紅K')
        if not reject_parts and len(b_signals) < B_PASS_COUNT:
            reject_parts.append(f'技術不足{len(b_signals)}/{B_PASS_COUNT}')

        ew_reject = []
        if not (EW_VOL_RATIO_MIN <= vol_r <= EW_VOL_RATIO_MAX): ew_reject.append(f'量比{vol_r:.2f}')
        if not (EW_RETURN_MIN <= dpct <= EW_RETURN_MAX):         ew_reject.append(f'漲幅{dpct:.1f}%')
        if mb > EW_MA28_BIAS_MAX:                                ew_reject.append(f'MA28乖離{mb:.1f}%')

        reject_str = '；'.join(reject_parts) if reject_parts else ('通過強勢篩選' if is_strong else '未通過')
        ew_rej_str = '；'.join(ew_reject)    if ew_reject    else ('通過預警篩選' if is_early  else '未通過預警')

        # ── 取出各自原始分 ──
        ts  = strong_score_map.get(sid, '')   # total_score（強勢）
        es  = early_score_map.get(sid,  '')   # early_score（起漲）

        # ════════════════════════════════════════════════════════
        # ★ 新增：計算綜合分
        #   composite_score = early_score × 0.45 + total_score × 0.55
        #   兩分都有才計算；只有一分用單分；都沒有則空白
        # ════════════════════════════════════════════════════════
        try:
            ts_f = float(ts) if ts != '' else None
            es_f = float(es) if es != '' else None
            if ts_f is not None and es_f is not None:
                composite = round(es_f * 0.45 + ts_f * 0.55, 2)
            elif ts_f is not None:
                composite = round(ts_f * 0.55, 2)
            elif es_f is not None:
                composite = round(es_f * 0.45, 2)
            else:
                composite = ''
        except (ValueError, TypeError):
            composite = ''

        full_rows.append({
            'stock_id': sid, 'name': name_map.get(sid, sid),
            'close': round(close, 2), 'vol_ratio': round(vol_r, 2),
            'daily_return_pct': round(dpct, 2), 'ma28_bias_pct': round(mb, 2),
            'turnover_億': round(to_day / 1e8, 2),
            'rsi14': round(last.get('RSI14', 0) or 0, 1),
            'inst_consec_days': inst_c,
            'yoy_revenue_pct': yoy_rev,
            'foreign_today': f_today, 'trust_today': t_today,
            'foreign_3d': f_3d, 'trust_3d': t_3d,
            'is_strong_confirm': is_strong, 'is_early_breakout': is_early,
            'total_score': ts,
            'early_score': es,
            'composite_score': composite,   # ★ 新增欄位
            'reject_reason': reject_str, 'early_reject_reason': ew_rej_str,
        })

    full_out = (pd.DataFrame(full_rows)
                .sort_values(['is_strong_confirm','is_early_breakout','composite_score'],
                             ascending=[False, False, False])
                .reset_index(drop=True))

    os.makedirs('output', exist_ok=True)
    csv_fname = f'output/full_filtered_{TODAY_STR}.csv'
    full_out.to_csv(csv_fname, index=False, encoding='utf-8-sig')
    print(f'✅ CSV 已儲存：{csv_fname}（{len(full_out)} 筆）')
    print(f'   強勢確認：{full_out["is_strong_confirm"].sum()} 筆  '
          f'起漲預警：{full_out["is_early_breakout"].sum()} 筆')
    return csv_fname, full_out


# ============================================================
# ★ 區塊 14：產生 HTML 報告（三區塊重構 + 各區塊嵌入 K線圖）
# ★ 修改重點（v7.1 + Yahoo連結）：
#   - 新增 yahoo_link() 輔助函式
#   - build_composite_rows() 代碼欄改為 Yahoo 連結（紫色）
#   - build_early_rows()     代碼欄改為 Yahoo 連結（綠色）
#   - build_strong_rows()    代碼欄改為 Yahoo 連結（金色）
#   - 連結格式：https://tw.stock.yahoo.com/quote/{代碼}.TW
#   - 每天篩出的任何代碼都自動生成連結，零維護
# ============================================================

def export_html(price_data, inst_data, fin_data, name_map, strong_df, early_df,
                strong_candidates, early_candidates,
                strong_charts, early_charts, composite_charts,
                full_out):

    # ── 格式化輔助函式 ──
    def fmt_num(v, decimals=2):
        try: return f'{float(v):,.{decimals}f}'
        except: return str(v)

    def fmt_turnover(v):
        try: return f'{float(v)/1e8:.2f} 億'
        except: return '-'

    def pct_color(v, threshold=5.0):
        try:
            f = float(v)
            if f >= threshold: return f'<span style="color:#f85149;font-weight:700">{f:+.2f}%</span>'
            if f >= 1.0:       return f'<span style="color:#e6a817">{f:+.2f}%</span>'
            if f <= -3.0:      return f'<span style="color:#3fb950">{f:+.2f}%</span>'
            return f'{f:+.2f}%'
        except: return str(v)

    def rsi_color(v):
        try:
            f = float(v)
            if f >= 78: return f'<span style="color:#f85149;font-weight:700">{f:.1f} ⚠️</span>'
            if f >= 65: return f'<span style="color:#e6a817">{f:.1f}</span>'
            return f'{f:.1f}'
        except: return str(v)

    def fmt_yoy(v):
        try:
            f = float(v)
            if f >= 20:  return f'<span style="color:#3fb950;font-weight:700">+{f:.0f}%✨</span>'
            if f >= 0:   return f'<span style="color:#e6a817">+{f:.0f}%</span>'
            if f >= -30: return f'<span style="color:#8b949e">{f:.0f}%</span>'
            return f'<span style="color:#f85149">{f:.0f}%⚠️</span>'
        except: return '<span style="color:#8b949e">-</span>'

    def fmt_inst_consec(v):
        try:
            n = int(v)
            if n >= 3: return f'<span style="color:#3fb950;font-weight:700">{n}天🔥</span>'
            if n >= 2: return f'<span style="color:#e6a817">{n}天</span>'
            if n >= 1: return f'{n}天'
            return '<span style="color:#8b949e">-</span>'
        except: return '-'

    def fmt_60d(v):
        try:
            f = float(v)
            if f < 20:  return f'<span style="color:#3fb950;font-weight:700">{f:+.0f}%</span>'
            if f < 40:  return f'<span style="color:#e6a817">{f:+.0f}%</span>'
            if f < 60:  return f'<span style="color:#c9d1d9">{f:+.0f}%</span>'
            return f'<span style="color:#f85149">{f:+.0f}%</span>'
        except: return '-'

    def strength_badge(v):
        colors = {'強':'#f85149','中':'#e6a817','弱':'#8b949e'}
        c = colors.get(v,'#8b949e')
        return f'<span style="background:{c};color:#fff;padding:2px 10px;border-radius:12px;font-weight:700;font-size:0.85em">{v}</span>'

    def composite_badge(v):
        try:
            f = float(v)
            return (f'<span style="background:linear-gradient(135deg,#1a2a3a,#2d4a6a);'
                    f'color:#fff;padding:3px 12px;border-radius:20px;font-weight:700;'
                    f'font-size:1.05em;white-space:nowrap">{f:.2f}</span>')
        except:
            return '<span style="color:#8b949e">-</span>'

    # ════════════════════════════════════════════════════════
    # ★ 新增：自動產生 Yahoo 股市連結輔助函式
    #   用法：yahoo_link('2376', '#e6a817')
    #   輸出：可點擊的連結，自動跳 https://tw.stock.yahoo.com/quote/2376.TW
    # ════════════════════════════════════════════════════════
    def yahoo_link(code, color):
        url = f'https://tw.stock.yahoo.com/quote/{code}.TW'
        return (
            f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="color:{color};font-weight:700;text-decoration:none;'
            f'display:inline-flex;align-items:center;gap:3px;white-space:nowrap;'
            f'transition:opacity .15s;" '
            f'onmouseover="this.style.opacity=\'0.7\'" '
            f'onmouseout="this.style.opacity=\'1\'">'
            f'<span style="font-size:1.05em">{code}</span>'
            f'<svg width="10" height="10" viewBox="0 0 10 10" fill="none" '
            f'xmlns="http://www.w3.org/2000/svg" style="opacity:.55;flex-shrink:0">'
            f'<path d="M2 8L8 2M8 2H4M8 2V6" stroke="currentColor" '
            f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg></a>'
        )

    # ── 強勢確認股 表格列 ──
    def build_strong_rows(df):
        rows = ''
        for _, r in df.head(TOP_STRONG).iterrows():
            rank  = int(r['rank'])
            sid   = r['stock_id']
            medal = ['🥇','🥈','🥉'][rank-1] if rank <= 3 else f'#{rank}'
            yoy_rev = fin_data.get(sid, None)
            df_p    = price_data.get(sid)
            p60 = 0.0
            if df_p is not None and len(df_p) >= 61:
                c0 = df_p['close'].iloc[-61]
                c1 = df_p['close'].iloc[-1]
                p60 = ((c1-c0)/c0*100) if c0 > 0 else 0.0
            sig_b = r.get('signal_b', r.get('signal',''))
            sig_c = r.get('signal_c', '')
            signal_html = f'{sig_b}<br><span style="color:#8b949e">{sig_c}</span>' if sig_c else sig_b
            rows += f"""
        <tr>
          <td style="font-size:1.1em;text-align:center;white-space:nowrap">{medal}</td>
          <td style="font-size:1.5em">{yahoo_link(sid, '#e6a817')}</td>
          <td style="font-weight:600;white-space:nowrap;font-size:1.5em">{r['name']}<br><span style="font-size:0.52em;color:#8b949e">{r.get('industry','')}</span></td>
          <td><span style="background:linear-gradient(135deg,#1a3a5c,#2d6a9f);color:#fff;padding:3px 12px;border-radius:20px;font-weight:700;font-size:1.05em;white-space:nowrap">{fmt_num(r['total_score'])}</span></td>
          <td style="font-weight:600;white-space:nowrap">{fmt_num(r['close'],1)}</td>
          <td style="white-space:nowrap">{fmt_turnover(r['turnover_today'])}</td>
          <td style="white-space:nowrap">{fmt_num(r['vol_ratio'])}x</td>
          <td style="white-space:nowrap">{pct_color(r['ma28_bias'])}</td>
          <td style="white-space:nowrap">{pct_color(r['daily_return_pct'])}</td>
          <td style="white-space:nowrap">{rsi_color(r['rsi14'])}</td>
          <td style="text-align:center;white-space:nowrap">{int(r['inst_consec'])}天</td>
          <td style="text-align:center">{strength_badge(r['strength'])}</td>
          <td style="text-align:center">{fmt_yoy(yoy_rev)}</td>
          <td style="text-align:center">{fmt_60d(p60)}</td>
        </tr>"""
        return rows

    # ── 起漲預警 表格列 ──
    def build_early_rows(df):
        if df.empty:
            return '<tr><td colspan="14" style="text-align:center;color:#8b949e;padding:24px">今日無符合起漲預警條件個股</td></tr>'
        rows = ''
        for _, r in df.head(TOP_EARLY).iterrows():
            rank  = int(r['rank'])
            medal = ['🌱','🌿','🍃'][rank-1] if rank <= 3 else f'#{rank}'
            tech_sig = r.get('tech_signal', '')
            inst_sig = r.get('inst_signal', '')
            signal_html = f'{tech_sig}<br><span style="color:#8b949e">{inst_sig}</span>'
            rows += f"""
        <tr>
          <td style="font-size:1.1em;text-align:center;white-space:nowrap">{medal}</td>
          <td style="font-size:1.5em">{yahoo_link(r['stock_id'], '#3fb950')}</td>
          <td style="font-weight:600;white-space:nowrap;font-size:1.5em">{r['name']}<br><span style="font-size:0.52em;color:#8b949e">{r.get('industry','')}</span></td>
          <td><span style="background:linear-gradient(135deg,#1a3a2c,#2d6a4a);color:#fff;padding:3px 12px;border-radius:20px;font-weight:700;font-size:1.05em;white-space:nowrap">{fmt_num(r['total_ew_score'])}</span></td>
          <td style="font-weight:600;white-space:nowrap">{fmt_num(r['close'],1)}</td>
          <td style="white-space:nowrap">{fmt_turnover(r['turnover_today'])}</td>
          <td style="white-space:nowrap">{fmt_num(r['vol_ratio'])}x</td>
          <td style="white-space:nowrap">{pct_color(r['ma28_bias'])}</td>
          <td style="white-space:nowrap">{pct_color(r['daily_return_pct'])}</td>
          <td style="white-space:nowrap">{rsi_color(r['rsi14'])}</td>
          <td style="text-align:center;white-space:nowrap">{fmt_num(r['consol_ratio'])} 📉</td>
          <td style="text-align:center">{fmt_yoy(r['yoy_revenue_pct'])}</td>
          <td style="text-align:center">{fmt_inst_consec(r['inst_consec_days'])}</td>
          <td style="text-align:center">{fmt_60d(r['past_60d_cum'])}</td>
        </tr>"""
        return rows

    # ── 綜合轉強 表格列 ──
    def build_composite_rows(full_df, strong_df_ref, early_df_ref):
        if full_df.empty:
            return '<tr><td colspan="13" style="text-align:center;color:#8b949e;padding:24px">無綜合分資料</td></tr>'

        comp_df = full_df[full_df['composite_score'] != ''].copy()
        comp_df['_cs'] = pd.to_numeric(comp_df['composite_score'], errors='coerce')
        comp_df = comp_df.dropna(subset=['_cs']).sort_values('_cs', ascending=False).head(TOP_COMPOSITE).reset_index(drop=True)

        strong_sig_map = {}
        if not strong_df_ref.empty:
            for _, sr in strong_df_ref.iterrows():
                strong_sig_map[sr['stock_id']] = sr.get('signal', '')
        early_sig_map = {}
        if not early_df_ref.empty:
            for _, er in early_df_ref.iterrows():
                early_sig_map[er['stock_id']] = er.get('signal', '')

        rows = ''
        medals_comp = ['🏅','🎖️','⭐','✨','💫','🔸','🔹','▪️']
        for i, r in comp_df.iterrows():
            rank_no = i + 1
            medal   = medals_comp[i] if i < len(medals_comp) else f'#{rank_no}'
            sid     = r['stock_id']

            sig = strong_sig_map.get(sid, '') or early_sig_map.get(sid, '') or r.get('reject_reason','')
            sig_html = f'<span style="font-size:0.82em;color:#c9d1d9">{sig[:60]}</span>'

            yoy_rev = fin_data.get(sid, r.get('yoy_revenue_pct', None))
            inst_c  = r.get('inst_consec_days', 0)
            es_val  = r.get('early_score', '')
            ts_val  = r.get('total_score', '')
            cs_val  = r.get('_cs', '')

            rows += f"""
        <tr>
          <td style="font-size:1.1em;text-align:center;white-space:nowrap">{medal}</td>
          <td style="font-size:1.5em">{yahoo_link(sid, '#bd8af5')}</td>
          <td style="font-weight:600;white-space:nowrap;font-size:1.5em">{r['name']}</td>
          <td>{composite_badge(cs_val)}</td>
          <td style="font-weight:600;white-space:nowrap">{fmt_num(r['close'],1)}</td>
          <td style="white-space:nowrap">{fmt_num(r['vol_ratio'])}x</td>
          <td style="white-space:nowrap">{pct_color(r['ma28_bias_pct'])}</td>
          <td style="white-space:nowrap">{pct_color(r['daily_return_pct'])}</td>
          <td style="white-space:nowrap">{rsi_color(r['rsi14'])}</td>
          <td style="text-align:center">{fmt_yoy(yoy_rev)}</td>
          <td style="text-align:center">{fmt_inst_consec(inst_c)}</td>
          <td style="text-align:center;color:#3fb950">{fmt_num(es_val) if es_val != '' else '-'}</td>
          <td style="text-align:center;color:#e6a817">{fmt_num(ts_val) if ts_val != '' else '-'}</td>
        </tr>"""
        return rows

    # ── K線圖區塊建構 ──
    def build_chart_html(chart_dict, df_ref, score_col='total_score', label_prefix=''):
        html = ''
        for sid, b64 in chart_dict.items():
            name = name_map.get(sid, sid)
            row  = df_ref[df_ref['stock_id'] == sid] if not df_ref.empty else pd.DataFrame()
            if not row.empty:
                r = row.iloc[0]
                caption = f'#{int(r["rank"])} &nbsp; {sid} {name} &nbsp;｜&nbsp; 評分 {fmt_num(r.get(score_col,0))}'
            else:
                caption = f'{sid} {name} {label_prefix}'
            html += f"""
        <div class="chart-wrap">
          <div class="chart-caption">{caption}</div>
          <img src="data:image/png;base64,{b64}" style="width:90%;border-radius:6px;margin:12px auto;display:block"/>
        </div>"""
        return html

    # ── 建構各區塊 ──
    strong_rows    = (build_strong_rows(strong_df) if not strong_df.empty
                      else '<tr><td colspan="14" style="text-align:center;color:#8b949e;padding:24px">今日無符合條件個股</td></tr>')
    early_rows     = build_early_rows(early_df)
    composite_rows = build_composite_rows(full_out, strong_df, early_df)

    _comp_top = full_out[full_out['composite_score'] != ''].copy()
    _comp_top['_cs'] = pd.to_numeric(_comp_top['composite_score'], errors='coerce')
    _comp_top = _comp_top.dropna(subset=['_cs']).sort_values('_cs', ascending=False)
    top1_id    = _comp_top.iloc[0]['stock_id']     if not _comp_top.empty else '-'
    top1_name  = _comp_top.iloc[0]['name']         if not _comp_top.empty else ''
    top1_score = fmt_num(_comp_top.iloc[0]['_cs']) if not _comp_top.empty else '-'

    strong_chart_html = build_chart_html(strong_charts, strong_df, score_col='total_score')
    early_chart_html  = build_chart_html(early_charts,  early_df,  score_col='total_ew_score')

    comp_df_tmp = full_out[full_out['composite_score'] != ''].copy()
    comp_df_tmp['_cs'] = pd.to_numeric(comp_df_tmp['composite_score'], errors='coerce')
    comp_df_tmp = comp_df_tmp.dropna(subset=['_cs']).sort_values('_cs', ascending=False).reset_index(drop=True)
    comp_df_tmp.insert(0, 'rank', range(1, len(comp_df_tmp)+1))
    composite_chart_html = build_chart_html(composite_charts, comp_df_tmp, score_col='_cs', label_prefix='| 綜合潛力')

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>上櫃操盤手 — {TODAY_DISP} 選股報告</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&display=swap');
  :root {{
    --bg:#0d1117; --bg2:#161b22; --bg3:#1c2129;
    --border:#30363d; --gold:#e6a817; --blue2:#4A90E2;
    --red:#f85149; --green:#3fb950; --purple:#bd8af5;
    --text:#e6edf3; --text2:#c9d1d9; --text3:#8b949e;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text);
    font-family:'Noto Sans TC','Microsoft JhengHei',sans-serif;
    font-size:15px; line-height:1.65; }}
  .header {{ background:linear-gradient(135deg,#0a1628 0%,#1a2744 50%,#0a1628 100%);
    border-bottom:2px solid var(--gold); padding:36px 48px; }}
  .header-label {{ color:var(--gold); font-size:0.8em; font-weight:700;
    letter-spacing:4px; margin-bottom:8px; }}
  .header h1 {{ font-size:1.85em; font-weight:900; }}
  .header h1 span {{ color:var(--gold); }}
  .header-meta {{ margin-top:12px; color:var(--text3); font-size:0.88em; }}
  .header-meta strong {{ color:var(--text2); }}
  .stats-bar {{ display:flex; border-bottom:1px solid var(--border); }}
  .stat-item {{ flex:1; padding:18px 24px; border-right:1px solid var(--border); background:var(--bg2); }}
  .stat-item:last-child {{ border-right:none; }}
  .stat-label {{ font-size:0.76em; color:var(--text3); letter-spacing:1px; margin-bottom:4px; }}
  .stat-value {{ font-size:1.55em; font-weight:900; color:var(--gold); }}
  .stat-sub   {{ font-size:0.76em; color:var(--text3); margin-top:2px; }}
  .container {{ max-width:1440px; margin:0 auto; padding:32px; }}
  .section {{ margin-bottom:48px; border:1px solid var(--border); border-radius:12px; overflow:hidden; }}
  .section-header {{ padding:20px 28px; display:flex; align-items:center; gap:14px; }}
  .section-header.strong    {{ background:linear-gradient(90deg,#1a2a4a 0%,#1c2129 100%); border-bottom:1px solid #2d4a7a; }}
  .section-header.early     {{ background:linear-gradient(90deg,#1a2a1a 0%,#1c2129 100%); border-bottom:1px solid #2d4a2d; }}
  .section-header.composite {{ background:linear-gradient(90deg,#2a1a3a 0%,#1c2129 100%); border-bottom:1px solid #5a2d7a; }}
  .section-header.charts    {{ background:linear-gradient(90deg,#1a1a2a 0%,#1c2129 100%); border-bottom:1px solid #3a3a5a; }}
  .section-icon {{ font-size:1.6em; }}
  .section-title-text h2 {{ font-size:1.2em; font-weight:900; }}
  .section-title-text p  {{ font-size:0.82em; color:var(--text3); margin-top:2px; }}
  .table-wrap {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.88em; }}
  thead tr {{ background:var(--bg3); border-bottom:1px solid var(--border); }}
  th {{ padding:12px 14px; text-align:left; color:var(--text3); font-weight:700;
    font-size:0.8em; letter-spacing:0.4px; white-space:nowrap; }}
  td {{ padding:14px 14px; border-bottom:1px solid var(--border); color:var(--text2); vertical-align:top; }}
  tbody tr:hover {{ background:rgba(255,255,255,0.03); }}
  tbody tr:nth-child(1) {{ background:rgba(230,168,23,0.07); }}
  tbody tr:nth-child(2) {{ background:rgba(192,192,192,0.04); }}
  tbody tr:nth-child(3) {{ background:rgba(205,127,50,0.04); }}
  tbody tr:last-child td {{ border-bottom:none; }}
  .charts-grid {{ background:var(--bg2); padding:24px; }}
  .chart-wrap {{ margin-bottom:28px; border:1px solid var(--border); border-radius:8px;
    overflow:hidden; background:#0d1117; }}
  .chart-caption {{ padding:10px 18px; background:var(--bg3); font-size:0.85em;
    color:var(--text2); font-weight:600; border-bottom:1px solid var(--border); }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; padding:12px 28px;
    background:var(--bg3); border-top:1px solid var(--border); font-size:0.76em; color:var(--text3); }}
  .dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
  .footer {{ text-align:center; padding:24px; color:var(--text3);
    font-size:0.8em; border-top:1px solid var(--border); }}
  .chart-jump-link {{ margin-left:auto; flex-shrink:0; color:var(--gold); font-size:1.1em; font-weight:700;
    text-decoration:none; white-space:nowrap; padding:6px 14px;
    border:1px solid rgba(230,168,23,0.5); border-radius:8px;
    background:rgba(230,168,23,0.1); letter-spacing:1px; }}
  .fixed-nav {{ position:fixed; bottom:24px; right:16px; z-index:9999;
    display:flex; flex-direction:column; gap:6px; }}
  .fixed-nav a {{ display:block; text-align:center; padding:11px 10px;
    background:rgba(13,17,23,0.88); color:var(--gold); font-size:15px;
    font-weight:700; text-decoration:none; border-radius:10px;
    border:1px solid rgba(230,168,23,0.45); letter-spacing:2px; min-width:72px; }}
</style>
</head>
<body>
<div class="header">
  <div class="header-label">上櫃操盤手 · 機密報告 v7.1</div>
  <h1>上櫃操盤手 — <span>{TODAY_DISP}</span> 收盤後最高勝率短線選股報告</h1>
  <div class="header-meta">
    掃描 <strong>{len(price_data)}</strong> 檔 ｜
    強勢確認股 <strong>{len(strong_candidates)}</strong> 檔 ｜
    起漲預警股 <strong>{len(early_candidates)}</strong> 檔
    &nbsp;·&nbsp;
    <span style="color:var(--purple)">↗ 點擊代碼直接開 Yahoo 股市走勢圖</span>
  </div>
</div>

<div class="stats-bar">
  <div class="stat-item">
    <div class="stat-label">掃描標的</div>
    <div class="stat-value">{len(price_data)}</div>
    <div class="stat-sub">上櫃活躍股</div>
  </div>
  <div class="stat-item">
    <div class="stat-label">強勢確認股</div>
    <div class="stat-value" style="color:var(--red)">{len(strong_candidates)}</div>
    <div class="stat-sub">追高吃肉首選</div>
  </div>
  <div class="stat-item">
    <div class="stat-label">起漲預警股</div>
    <div class="stat-value" style="color:var(--green)">{len(early_candidates)}</div>
    <div class="stat-sub">提前布局候選</div>
  </div>
  <div class="stat-item">
    <div class="stat-label">綜合轉強 Top 1</div>
    <div class="stat-value" style="font-size:1.15em">{top1_id} {top1_name}</div>
    <div class="stat-sub">綜合分 {top1_score}</div>
  </div>
  <div class="stat-item">
    <div class="stat-label">報告日期</div>
    <div class="stat-value" style="font-size:1.15em">{TODAY_DISP}</div>
    <div class="stat-sub">收盤後分析</div>
  </div>
</div>

<div class="container">

  <!-- ══════════════ 綜合轉強潛力股 表格 ══════════════ -->
  <div class="section" id="composite-section">
    <div class="section-header composite">
      <div class="section-icon">🔮</div>
      <div class="section-title-text">
        <h2>綜合轉強潛力股 Top {TOP_COMPOSITE}</h2>
        <p>綜合分 = 起漲分 × 0.45 + 強勢分 × 0.55，兼具短線爆發力與基本面支撐</p>
      </div>
      <a href="#composite-charts" class="chart-jump-link">K線圖 ↓</a>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>排名</th><th>代碼</th><th>名稱</th><th>綜合分</th>
          <th>收盤價</th><th>量比</th>
          <th>MA28乖離</th><th>漲幅%</th><th>RSI14</th>
          <th>營收YoY</th><th>法人連買</th>
          <th>起漲分</th><th>強勢分</th>
        </tr></thead>
        <tbody>{composite_rows}</tbody>
      </table>
    </div>
    <div class="legend">
      <div style="display:flex;align-items:center;gap:5px">
        <span class="dot" style="background:var(--purple)"></span>
        綜合分公式：起漲分（early_score）× 0.45 + 強勢分（total_score）× 0.55
      </div>
      <div>🔮 適合同時具備蓄勢特徵與當日量能的中短線多頭候選</div>
      <div style="color:var(--purple)">↗ 點擊紫色代碼直接開 Yahoo 股市走勢圖</div>
    </div>
  </div>

  <!-- ══════════════ 即將起漲的潛力股 表格 ══════════════ -->
  <div class="section" id="early-section">
    <div class="section-header early">
      <div class="section-icon">🌱</div>
      <div class="section-title-text">
        <h2>即將起漲的潛力股 Top {min(TOP_EARLY, len(early_candidates))}（早布局用）</h2>
        <p>硬條件過濾 + 財務/籌碼加分排名，提前布局，目標漲幅 15~25%</p>
      </div>
      <a href="#early-charts" class="chart-jump-link">K線圖 ↓</a>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>排名</th><th>代碼</th><th>名稱</th><th>總分</th>
          <th>收盤價</th><th>成交值</th><th>量比</th>
          <th>MA28乖離</th><th>漲幅%</th><th>RSI14</th>
          <th>收斂比</th>
          <th>營收YoY</th><th>法人連買</th><th>60日漲幅</th>
        </tr></thead>
        <tbody>{early_rows}</tbody>
      </table>
    </div>
    <div class="legend">
      <div style="display:flex;align-items:center;gap:5px">
        <span class="dot" style="background:var(--green)"></span>
        硬條件通過後加分：營收YoY&gt;20%→+16 ｜ 法人連買≥2天→+24 ｜ 60日漲幅&lt;25%→+22
      </div>
      <div>📉收斂比 = 10日均振幅÷20日均振幅（&lt;1.12才通過）</div>
      <div style="color:var(--green)">↗ 點擊綠色代碼直接開 Yahoo 股市走勢圖</div>
    </div>
  </div>

  <!-- ══════════════ 強勢確認股 表格 ══════════════ -->
  <div class="section" id="strong-section">
    <div class="section-header strong">
      <div class="section-icon">🔥</div>
      <div class="section-title-text">
        <h2>強勢確認股 Top {min(TOP_STRONG, len(strong_candidates))}（追高吃肉用）</h2>
        <p>量價齊揚 + 法人認同 + 技術突破，明日開盤強勢可積極追進，建議持倉 1~2 天</p>
      </div>
      <a href="#strong-charts" class="chart-jump-link">K線圖 ↓</a>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>排名</th><th>代碼</th><th>名稱</th><th>總分</th>
          <th>收盤價</th><th>成交值</th><th>量比</th>
          <th>MA28乖離</th><th>漲幅%</th><th>RSI14</th>
          <th>連買天數</th><th>強弱</th><th>營收YoY</th><th>60日漲幅</th>
        </tr></thead>
        <tbody>{strong_rows}</tbody>
      </table>
    </div>
    <div class="legend">
      <div style="display:flex;align-items:center;gap:5px">
        <span class="dot" style="background:var(--gold)"></span>
        評分：量比×1.5 + 20日新高×1.2 + MA28乖離×1.0 + 連買天數×2.0 + 漲幅×0.8 + Z-score
      </div>
      <div><span style="color:var(--red)">RSI⚠️</span> ≥78 追高需謹慎</div>
      <div style="color:var(--gold)">↗ 點擊金色代碼直接開 Yahoo 股市走勢圖</div>
    </div>
  </div>

  <!-- ══════════════ 綜合轉強潛力股 K線圖 ══════════════ -->
  <div class="section" id="composite-charts">
    <div class="section-header composite">
      <div class="section-icon">🔮</div>
      <div class="section-title-text">
        <h2>綜合轉強潛力股 K線圖 Top {min(TOP_CHART, len(composite_charts))}</h2>
        <p>綜合分最高前5檔走勢圖</p>
      </div>
    </div>
    <div class="charts-grid">
      {composite_chart_html if composite_chart_html else '<p style="color:var(--text3);text-align:center;padding:20px">無綜合分 K 線圖</p>'}
    </div>
  </div>

  <!-- ══════════════ 即將起漲的潛力股 K線圖 ══════════════ -->
  <div class="section" id="early-charts">
    <div class="section-header early">
      <div class="section-icon">🌱</div>
      <div class="section-title-text">
        <h2>即將起漲的潛力股 K線圖 Top {min(TOP_CHART, len(early_charts))}</h2>
        <p>起漲預警分數最高前5檔走勢圖</p>
      </div>
    </div>
    <div class="charts-grid">
      {early_chart_html if early_chart_html else '<p style="color:var(--text3);text-align:center;padding:20px">無起漲預警 K 線圖</p>'}
    </div>
  </div>

  <!-- ══════════════ 強勢確認股 K線圖 ══════════════ -->
  <div class="section" id="strong-charts">
    <div class="section-header strong">
      <div class="section-icon">🔥</div>
      <div class="section-title-text">
        <h2>強勢確認股 K線圖 Top {min(TOP_CHART, len(strong_charts))}</h2>
        <p>強勢確認分數最高前5檔走勢圖</p>
      </div>
    </div>
    <div class="charts-grid">
      {strong_chart_html if strong_chart_html else '<p style="color:var(--text3);text-align:center;padding:20px">K線圖產生失敗</p>'}
    </div>
  </div>

</div>
<div class="footer">
  上櫃操盤手選股系統 v7.1 ｜ {TODAY_DISP} ｜ 綜合分公式：early×0.45 + total×0.55 ｜ 僅供內部參考，不構成投資建議
</div>

<nav class="fixed-nav">
  <a href="#composite-section">綜合轉強</a>
  <a href="#early-section">即將起漲</a>
  <a href="#strong-section">強勢確認</a>
</nav>

</body></html>"""

    os.makedirs('output', exist_ok=True)
    html_fname = f'output/OTC_report_{TODAY_STR}.html'
    with open(html_fname, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ HTML 報告已儲存：{html_fname}（{len(html)//1024} KB）')
    return html_fname

# ============================================================
# 區塊 15：Telegram 通知
# ============================================================

def send_telegram(strong_df, early_df, strong_candidates, early_candidates, html_fname):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print('⚠️  未設定 Telegram Token，跳過通知')
        return

    lines = [
        f"📊 *上櫃操盤手選股系統 — {TODAY_DISP}*",
        "",
        f"🔍 掃描標的：{len(price_data_global)} 檔",
        f"🔥 強勢確認股：{len(strong_candidates)} 檔",
        f"🌱 起漲預警股：{len(early_candidates)} 檔",
        "",
    ]

    if not strong_df.empty:
        lines.append("*🔥 強勢確認股 Top 5：*")
        for _, r in strong_df.head(5).iterrows():
            yoy = fin_data_global.get(r['stock_id'], None)
            yoy_str = f"YoY:{float(yoy):+.0f}%" if yoy is not None else "YoY:-"
            lines.append(f"  #{int(r['rank'])} {r['stock_id']} {r['name']} | 分:{r['total_score']:.1f} | {yoy_str}")
        lines.append("")

    if not early_df.empty:
        lines.append("*🌱 起漲預警 Top 5：*")
        for _, r in early_df.head(5).iterrows():
            lines.append(f"  #{int(r['rank'])} {r['stock_id']} {r['name']} | 分:{r['total_ew_score']:.1f}")
        lines.append("")

    if GITHUB_PAGES_URL:
        lines.append(f"🌐 [完整報告點這裡]({GITHUB_PAGES_URL})")

    msg = '\n'.join(lines)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': msg,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': False
        }, timeout=15)
        if resp.status_code == 200:
            print('✅ Telegram 通知已發送')
        else:
            print(f'⚠️  Telegram 發送失敗：{resp.text}')
    except Exception as e:
        print(f'⚠️  Telegram 錯誤：{e}')

# ============================================================
# 區塊 16：Email 通知（含 CSV + HTML 附件）
# ============================================================

def send_email(csv_fname, html_fname, strong_df, early_df, strong_candidates, early_candidates):
    if not GMAIL_USER or not GMAIL_APP_PASS or not EMAIL_TO:
        print('⚠️  未設定 Email，跳過通知')
        return

    msg = MIMEMultipart('mixed')
    msg['Subject'] = f'上櫃操盤手選股報告 {TODAY_DISP} — 強勢{len(strong_candidates)}檔 預警{len(early_candidates)}檔'
    msg['From']    = GMAIL_USER
    msg['To']      = EMAIL_TO

    body_lines = [
        f'上櫃操盤手選股系統 — {TODAY_DISP}',
        '',
        f'掃描標的：{len(price_data_global)} 檔',
        f'強勢確認股：{len(strong_candidates)} 檔',
        f'起漲預警股：{len(early_candidates)} 檔',
        '',
    ]

    if not strong_df.empty:
        body_lines.append('強勢確認股 Top 5：')
        for _, r in strong_df.head(5).iterrows():
            yoy = fin_data_global.get(r['stock_id'], None)
            yoy_str = f"YoY:{float(yoy):+.0f}%" if yoy is not None else "YoY:-"
            body_lines.append(f"  #{int(r['rank'])} {r['stock_id']} {r['name']} | 評分:{r['total_score']:.1f} | 漲幅:{r['daily_return_pct']:+.2f}% | {yoy_str}")
        body_lines.append('')

    if not early_df.empty:
        body_lines.append('起漲預警 Top 5：')
        for _, r in early_df.head(5).iterrows():
            body_lines.append(f"  #{int(r['rank'])} {r['stock_id']} {r['name']} | 評分:{r['total_ew_score']:.1f} | 量比:{r['vol_ratio']:.2f}x")
        body_lines.append('')

    if GITHUB_PAGES_URL:
        body_lines.append(f'完整報告：{GITHUB_PAGES_URL}')

    body_lines.extend(['', '--- 本郵件由系統自動發送，不構成投資建議 ---'])
    msg.attach(MIMEText('\n'.join(body_lines), 'plain', 'utf-8'))

    for fpath in [csv_fname, html_fname]:
        if os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment',
                            filename=os.path.basename(fpath))
            msg.attach(part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, EMAIL_TO.split(','), msg.as_string())
        print('✅ Email 已發送')
    except Exception as e:
        print(f'⚠️  Email 發送失敗：{e}')

# ============================================================
# 全域變數
# ============================================================
price_data_global = {}
fin_data_global   = {}

# ============================================================
# ★ 主程式（v7.1 修改：新增 composite_charts 繪製 + 傳入 export_html）
# ============================================================

def main():
    global price_data_global, fin_data_global

    print("=" * 60)
    print("上櫃操盤手選股系統 v7.1 — 開始執行")
    print("=" * 60)

    install_system_deps()
    font_path, font_prop = init_chinese_font()

    stock_ids, name_map = load_stock_list()
    industry_map = load_industry_map()
    print(f'有效代碼：{len(stock_ids)} 檔')

    api = login_finmind()
    use_rest = detect_api_mode(api, stock_ids)

    price_data = fetch_all_prices(stock_ids, api, use_rest)
    price_data_global = price_data

    valid_ids = list(price_data.keys())
    inst_data  = fetch_all_inst(valid_ids, api, use_rest)
    fin_data   = fetch_all_revenue(valid_ids, api, use_rest)
    fin_data_global = fin_data

    strong_df, strong_candidates = run_strong_filter(price_data, inst_data, fin_data, name_map, industry_map)
    early_df,  early_candidates  = run_early_filter(price_data, inst_data, fin_data, name_map, industry_map)

    # ── 先輸出 CSV（需要 full_out 才能決定 composite Top8 的圖）──
    csv_fname, full_out = export_csvs(price_data, inst_data, fin_data, name_map, strong_df, early_df)

    # ────────────────────────────────────────────────────────
    # ★ 計算 composite Top5 的 sid 清單（用於 K線圖）
    # ────────────────────────────────────────────────────────
    comp_chart_df = full_out[full_out['composite_score'] != ''].copy()
    comp_chart_df['_cs'] = pd.to_numeric(comp_chart_df['composite_score'], errors='coerce')
    comp_chart_df = (comp_chart_df.dropna(subset=['_cs'])
                     .sort_values('_cs', ascending=False)
                     .head(TOP_CHART))
    composite_chart_sids = comp_chart_df['stock_id'].tolist()

    # ── 強勢確認股 K 線圖（原有邏輯）──
    print('\n[強勢確認股] 繪製 K 線圖...')
    strong_charts = {}
    if not strong_df.empty:
        for sid in strong_df['stock_id'].head(TOP_CHART).tolist():
            rank = int(strong_df[strong_df['stock_id']==sid]['rank'].values[0])
            b64  = draw_kline(sid, price_data, name_map, font_path, label=f'| 強勢確認 #{rank}')
            if b64:
                strong_charts[sid] = b64
            print(f'  {sid} {name_map.get(sid,"")}: {"OK" if b64 else "失敗"}')

    # ── 起漲預警 K 線圖（原有邏輯）──
    print('\n[起漲預警] 繪製 K 線圖...')
    early_charts = {}
    if not early_df.empty:
        for sid in early_df['stock_id'].head(TOP_CHART).tolist():
            rank = int(early_df[early_df['stock_id']==sid]['rank'].values[0])
            b64  = draw_kline(sid, price_data, name_map, font_path, label=f'| 起漲預警 #{rank}')
            if b64:
                early_charts[sid] = b64
            print(f'  {sid} {name_map.get(sid,"")}: {"OK" if b64 else "失敗"}')

    # ★ 綜合分 Top5 K 線圖（新增）
    print('\n[綜合分 Top5] 繪製 K 線圖...')
    composite_charts = {}
    for sid in composite_chart_sids:
        b64 = draw_kline(sid, price_data, name_map, font_path, label='| 綜合轉強')
        if b64:
            composite_charts[sid] = b64
        print(f'  {sid} {name_map.get(sid,"")}: {"OK" if b64 else "失敗"}')

    # ── 輸出 HTML（傳入三組 charts + full_out）──
    html_fname = export_html(price_data, inst_data, fin_data, name_map,
                             strong_df, early_df, strong_candidates, early_candidates,
                             strong_charts, early_charts, composite_charts,  # ★
                             full_out)                                         # ★

    send_telegram(strong_df, early_df, strong_candidates, early_candidates, html_fname)
    send_email(csv_fname, html_fname, strong_df, early_df, strong_candidates, early_candidates)

    print("\n" + "=" * 60)
    print(f"✅ 全部完成！輸出目錄：output/")
    print(f"   CSV ：{csv_fname}")
    print(f"   HTML：{html_fname}")
    print("=" * 60)


if __name__ == '__main__':
    main()

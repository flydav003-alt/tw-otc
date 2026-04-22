"""
上櫃操盤手選股系統 v7.3
HTML 修改（對齊台股GOGOGO版本）：
  - 綜合轉強 Top 15、即將起漲 Top 15、強勢確認 Top 10
  - 欄位：收盤價→漲幅%→量比→RSI14→MA28乖離→營收YoY→法人連買（無分數欄）
  - K線圖直接顯示於每檔下方（不需點開）
  - 每檔股票資料列前方加欄位標題列
  - ⭐ 星星自動標示（OTC專用條件）
  - 暗色科技配色保留
"""

# ============================================================
# 區塊 0：安裝字型
# ============================================================
import subprocess
import sys
import os

def install_system_deps():
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
import smtplib
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

FINMIND_TOKEN    = os.environ.get("FINMIND_TOKEN", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER       = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS   = os.environ.get("GMAIL_APP_PASS", "")
EMAIL_TO         = os.environ.get("EMAIL_TO", "")
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

EW_BONUS_YOY  = 16.0
EW_BONUS_INST = 24.0
EW_BONUS_60D  = 22.0

# ★ v7.2 修改：各區顯示數量
TOP_STRONG    = 10
TOP_EARLY     = 15
TOP_COMPOSITE = 15
TOP_CHART     = 5

MIN_DAYS    = 60
BATCH_SIZE  = 40
BATCH_DELAY = 1.5
ERROR_LOG   = "error_log.txt"

# ★ 統一匯出欄位（19欄，與TSE對齊）
EXPORT_COLS = [
    'stock_id', 'name', 'close', 'vol_ratio', 'daily_return_pct',
    'ma28_bias_pct', 'turnover_億', 'rsi14', 'inst_consec_days',
    'yoy_revenue_pct', 'foreign_today', 'trust_today',
    'foreign_3d', 'trust_3d', 'is_strong_confirm', 'is_early_breakout',
    'total_score', 'early_score', 'composite_score'
]

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
        print("  ⚠️  找不到中文字型")
        return None, None
    try:
        prop      = fm.FontProperties(fname=found)
        font_name = prop.get_name()
        mpl.rcParams['font.sans-serif'] = [font_name, 'DejaVu Sans']
        fm.fontManager.addfont(found)
        print(f"  ✅ 中文字型：{font_name}")
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
# 區塊 5：讀取 CSV + FinMind
# ============================================================

def load_stock_list():
    df_csv = None
    for enc in ['cp950','utf-8-sig','utf-8','big5','latin1']:
        try:
            df_csv = pd.read_csv(OTC_CSV_PATH, encoding=enc, dtype=str)
            print(f'✅ CSV（{enc}），共 {len(df_csv)} 筆')
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except FileNotFoundError:
            raise FileNotFoundError(f'找不到 {OTC_CSV_PATH}')
    df_csv.columns       = df_csv.columns.str.strip()
    df_csv['stock_id']   = df_csv['stock_id'].astype(str).str.strip()
    df_csv['name']       = df_csv['name'].astype(str).str.strip()
    df_csv               = df_csv[df_csv['stock_id'].str.match(r'^\d{4,5}$')].copy()
    return df_csv['stock_id'].tolist(), dict(zip(df_csv['stock_id'], df_csv['name']))

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
        print(f'✅ 產業別：{len(industry_map)} 檔')
    except Exception as e:
        print(f'⚠️  產業別失敗：{e}')
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
            print(f'✅ SDK 正常')
            return False
        raise ValueError('empty')
    except Exception as e:
        print(f'SDK 失敗（{e}），改用 REST')
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
    print(f'[K線] {total} 檔，{batches} 批...')
    for i in range(0, total, BATCH_SIZE):
        batch    = stock_ids[i:i+BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f'  批次 {batch_no}/{batches}...', end=' ', flush=True)
        ok = 0
        for sid in batch:
            raw  = fetch_price(sid, api, use_rest)
            if raw is None: continue
            proc = calc_indicators(raw)
            if proc is not None:
                price_data[sid] = proc
                ok += 1
        print(f'✓{ok} ✗{len(batch)-ok}  累計 {len(price_data)}')
        if i + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)
    print(f'✅ K線完成 {len(price_data)}/{total}')
    return price_data

# ============================================================
# 區塊 7：籌碼
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
    print(f'[籌碼] {total} 檔...')
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
# 區塊 8：月營收 YoY
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
        rev_df  = rev_df.sort_values('date').reset_index(drop=True)
        rev_col = next((c for c in ['revenue','Revenue','monthly_revenue'] if c in rev_df.columns), None)
        if rev_col is None:
            return None
        rev_df[rev_col] = pd.to_numeric(rev_df[rev_col], errors='coerce')
        rev_df = rev_df.dropna(subset=[rev_col])
        if len(rev_df) < 13:
            return None
        lt, pv = rev_df[rev_col].iloc[-1], rev_df[rev_col].iloc[-13]
        if pv <= 0 or np.isnan(pv):
            return None
        return round((lt - pv) / abs(pv) * 100, 1)
    except Exception as e:
        log_error(f'{sid} 月營收YoY：{e}')
        return None

def fetch_all_revenue(valid_ids, api, use_rest):
    fin_data = {}
    total   = len(valid_ids)
    batches = (total - 1) // BATCH_SIZE + 1
    print(f'[月營收] {total} 檔...')
    for i in range(0, total, BATCH_SIZE):
        batch    = valid_ids[i:i+BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f'  批次 {batch_no}/{batches}...', end=' ', flush=True)
        ok = fail = 0
        for sid in batch:
            yoy = calc_yoy_revenue(sid, api, use_rest)
            fin_data[sid] = yoy
            if yoy is not None: ok += 1
            else: fail += 1
        print(f'有YoY {ok} / 無 {fail}')
        if i + BATCH_SIZE < total:
            time.sleep(BATCH_DELAY)
    print(f'✅ 月營收完成')
    return fin_data

# ============================================================
# 區塊 9：篩選模組
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
        if c >= C_CONSEC_DAYS_MIN:  signals.append(f'{tag}連買{c}天')
        elif t >= C_SINGLE_MIN:     signals.append(f'{tag}買超{int(t)}張')
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
# 區塊 10：強勢確認股篩選
# ============================================================

def run_strong_filter(price_data, inst_data, fin_data, name_map, industry_map):
    funnel     = {'總有效':len(price_data),'A流動性':0,'B技術':0,'C籌碼':0,'D過濾':0}
    candidates = []

    for sid, df in price_data.items():
        if df is None or df.empty: continue
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
        score       = (last['vol_ratio']*W_VOL_RATIO + h20*W_HIGH20 +
                       ma28_bias*W_MA28_BIAS + inst_consec*W_INST_DAYS + dpct*W_RETURN_PCT)
        yoy_rev = fin_data.get(sid, None)
        if len(df) >= 61:
            c60      = df['close'].iloc[-61]
            past_60d = ((last.get('close',0)-c60)/c60*100) if c60>0 else 0.0
        else:
            past_60d = 0.0

        # ★ v7.3 新增：60日過熱前置濾（對齊TSE邏輯）
        # 60日漲幅 > 40% 且 MA28乖離 > 20% → 兩個條件同時成立才排除
        # 單純乖離大（但60日漲幅未過熱）仍保留，避免誤殺剛啟動的個股
        if past_60d > 40.0 and ma28_bias > 20.0:
            continue

        candidates.append({
            'stock_id': sid, 'name': name_map.get(sid,sid),
            'industry': industry_map.get(sid,''),
            'score': round(score,2),
            'close': last.get('close',0),
            'turnover_today': last.get('turnover_today',0),
            'vol_ratio': round(last['vol_ratio'],2),
            'ma28_bias': round(ma28_bias,2),
            'daily_return_pct': round(dpct,2),
            'rsi14': round(last.get('RSI14',0) or 0,1),
            'inst_consec': inst_consec,
            'foreign_today': info.get('foreign_today',0),
            'trust_today':   info.get('trust_today',0),
            'foreign_3d':    info.get('foreign_3d',0),
            'trust_3d':      info.get('trust_3d',0),
            'signal_b': ' + '.join(b_sig),
            'signal_c': ' + '.join(c_sig),
            'signal':   ' | '.join(b_sig+c_sig),
            'hold_days': 2 if score>15 else 1,
            'strength': '強' if score>18 else ('中' if score>=12 else '弱'),
            'yoy_revenue_pct': yoy_rev,
            'past_60d_cum': round(past_60d,1),
            'turnover_億': round((last.get('turnover',0) or 0)/1e8, 2),
            '_vr': last['vol_ratio'], '_mb': ma28_bias,
            '_ic': float(inst_consec), '_dp': dpct, '_h20': h20,
        })

    # 保底：確保每筆都有 total_score
    for c in candidates:
        c.setdefault('total_score', c['score'])

    if len(candidates) >= 2:
        vz = safe_zscore([c['_vr'] for c in candidates])
        mz = safe_zscore([c['_mb'] for c in candidates])
        iz = safe_zscore([c['_ic'] for c in candidates])
        dz = safe_zscore([c['_dp'] for c in candidates])
        hz = safe_zscore([c['_h20'] for c in candidates])
        w_sum = W_VOL_RATIO+W_HIGH20+W_MA28_BIAS+W_INST_DAYS+W_RETURN_PCT
        for i, c in enumerate(candidates):
            z = (W_VOL_RATIO/w_sum*vz[i] + W_HIGH20/w_sum*mz[i] +
                 W_MA28_BIAS/w_sum*iz[i] + W_INST_DAYS/w_sum*dz[i] +
                 W_RETURN_PCT/w_sum*hz[i])
            c['z_score']     = round(float(z),3)
            c['total_score'] = round(c['score']+float(z),2)

    for c in candidates:
        if c.get('ma28_bias',0) > 35:          c['total_score'] -= 18
        elif c.get('ma28_bias',0) > 25:        c['total_score'] -= 10
        if c.get('daily_return_pct',0) > 9.5:  c['total_score'] -= 12
        if c.get('rsi14',0) > 78:              c['total_score'] -= 8
        c['total_score'] = round(max(c['total_score'],0),2)

    if not candidates:
        print('\n【強勢確認股】0 檔')
        return pd.DataFrame(), []

    strong_df = (pd.DataFrame(candidates)
                 .sort_values('total_score', ascending=False)
                 .reset_index(drop=True))
    strong_df.insert(0, 'rank', range(1, len(strong_df)+1))

    print(f'\n【強勢確認股漏斗】')
    base = funnel['總有效'] or 1
    for k, v in funnel.items():
        print(f'  {k}：{v} ({v/base*100:.1f}%)')
    print(f'候選：{len(candidates)} 檔')
    return strong_df, candidates

# ============================================================
# 區塊 11：起漲預警
# ============================================================

def run_early_filter(price_data, inst_data, fin_data, name_map, industry_map):
    candidates = []

    for sid, df in price_data.items():
        if df is None or len(df) < 30: continue
        last      = df.iloc[-1].to_dict()
        vm5       = last.get('vol_ma5',0) or 0
        if vm5 <= 0: continue
        vol_ratio = last.get('volume',0)/vm5
        dpct      = last.get('daily_return',0)*100
        close     = last.get('close',0)
        ma20      = last.get('MA20',0) or 0
        ma28      = last.get('MA28',0) or 0
        ma28_bias = ((close-ma28)/ma28*100) if ma28>0 else 0
        to_day    = last.get('turnover',0) or 0
        rsi14     = last.get('RSI14',0) or 0
        amp10     = df['amplitude'].tail(10).mean()
        amp20_val = df['amplitude'].tail(20).mean()
        consol    = (amp10/amp20_val) if amp20_val>0 else 999
        if len(df) >= 61:
            c60d     = df['close'].iloc[-61]
            past_60d = ((close-c60d)/c60d*100) if c60d>0 else 0
        else:
            past_60d = 0.0

        if to_day < EW_TURNOVER_MIN:                               continue
        if not (EW_VOL_RATIO_MIN <= vol_ratio <= EW_VOL_RATIO_MAX): continue
        if not (EW_RETURN_MIN <= dpct <= EW_RETURN_MAX):           continue
        if ma28_bias > EW_MA28_BIAS_MAX:                           continue
        if consol >= EW_CONSOL_RATIO:                              continue
        if df['daily_return'].tail(20).max()*100 >= EW_MAX20D_RET_MAX: continue
        tail7    = df.tail(7)
        below_ma = (tail7['close'] <= tail7['MA20']).sum()
        if below_ma < EW_ABOVE_MA20_MIN: continue
        if close < ma20*0.975:           continue

        info        = inst_data.get(sid,{})
        f_today     = info.get('foreign_today',0)
        t_today     = info.get('trust_today',0)
        inst_consec = max(info.get('foreign_consec',0), info.get('trust_consec',0))

        def normalize_shares(v):
            return v/1000 if abs(v)>1_000_000 else v

        f_today_n = normalize_shares(f_today)
        yoy = fin_data.get(sid, None)

        vol_ratio_score = vol_ratio*15
        consol_score    = max(0,(1.20-consol))*14
        inst_score      = (1 if inst_consec>=2 or f_today_n>=80 else 0)*18
        ew_score        = 0.35*vol_ratio_score + 0.30*consol_score + 0.35*inst_score

        bonus_total = 8.0
        bonus_flags = []
        if yoy is not None and not pd.isna(yoy):
            yov = float(yoy)
            if yov > 80:
                bonus_total += EW_BONUS_YOY;       bonus_flags.append(f'營收YoY+{yov:.0f}%✨')
            elif yov > 30:
                bonus_total += EW_BONUS_YOY*0.7;   bonus_flags.append(f'營收YoY+{yov:.0f}%')
            else:
                bonus_total += EW_BONUS_YOY*0.4;   bonus_flags.append(f'營收YoY{yov:.0f}%')

        if inst_consec >= 2 or f_today_n > 80:
            bonus_total += EW_BONUS_INST; bonus_flags.append(f'法人連買{inst_consec}天')
        if past_60d < 25:
            bonus_total += EW_BONUS_60D;  bonus_flags.append(f'低位階{past_60d:.0f}%')

        ew_score += bonus_total
        if dpct > 6.5:        ew_score -= 8
        if ma28_bias > 18.5:  ew_score -= 9
        elif ma28_bias > 15:  ew_score -= 5

        total_ew_score = round(max(ew_score,0),2)

        inst_sig = []
        if f_today_n >= EW_INST_MIN:    inst_sig.append(f'外資+{int(f_today_n)}張')
        elif f_today_n < -EW_INST_MIN:  inst_sig.append(f'外資賣{int(abs(f_today_n))}張')
        if t_today >= EW_INST_MIN:      inst_sig.append(f'投信+{int(t_today)}張')
        if not inst_sig:                inst_sig = ['法人觀望']

        candidates.append({
            'stock_id':         sid,
            'name':             name_map.get(sid,sid),
            'industry':         industry_map.get(sid,''),
            'total_ew_score':   total_ew_score,
            'ew_score':         total_ew_score,
            'close':            close,
            'turnover_today':   to_day,
            'vol_ratio':        round(vol_ratio,2),
            'ma28_bias':        round(ma28_bias,2),
            'daily_return_pct': round(dpct,2),
            'rsi14':            round(rsi14,1),
            'consol_ratio':     round(consol,2),
            'past_60d_cum':     round(past_60d,1),
            'yoy_revenue_pct':  yoy,
            'inst_consec_days': inst_consec,
            'foreign_today':    f_today,
            'trust_today':      t_today,
            'foreign_3d':       info.get('foreign_3d',0),
            'trust_3d':         info.get('trust_3d',0),
            'turnover_億':      round(to_day/1e8,2),
            'is_early_breakout': True,
            'bonus_flags':      ' | '.join(bonus_flags) if bonus_flags else '-',
            'tech_signal':      f'量比{vol_ratio:.2f}倍 | 收斂{consol:.2f}',
            'inst_signal':      ' + '.join(inst_sig),
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
# 區塊 12：K 線圖
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
        if c not in df_p.columns: return None

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
    arrow.iloc[-1] = df_p['Low'].iloc[-1]*0.982
    add_plots.append(mpf.make_addplot(arrow, panel=0, type='scatter', markersize=130, marker='^', color='#FFD700'))

    mc = mpf.make_marketcolors(up='#f85149', down='#26a641', edge='inherit', wick='inherit',
                                volume={'up':'#f85149','down':'#26a641'})
    mpl.rcParams['axes.unicode_minus'] = False
    rc_font = fm.FontProperties(fname=font_path).get_name() if font_path else 'DejaVu Sans'
    style   = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc,
                                  rc={'font.family':rc_font,'axes.labelcolor':'#c9d1d9',
                                      'xtick.color':'#c9d1d9','ytick.color':'#c9d1d9'})
    name      = name_map.get(sid, sid)
    title_str = f'  {sid} {name}  {label}' if font_path else f'  {sid}  {label}'
    try:
        fig, _ = mpf.plot(
            df_p[['Open','High','Low','Close','Volume']],
            type='candle', style=style, title=title_str,
            volume=True, addplot=add_plots,
            panel_ratios=(4,1,1.2,1.2),
            figsize=(14,10), returnfig=True, warn_too_much_data=200,
        )
        b64 = fig_to_base64(fig)
        plt.close(fig)
        return b64
    except Exception as e:
        log_error(f'{sid} K線圖：{e}')
        return None

# ============================================================
# 區塊 13：CSV
# ============================================================

def export_csvs(price_data, inst_data, fin_data, name_map, strong_df, early_df):
    strong_set       = set(strong_df['stock_id'].tolist()) if not strong_df.empty else set()
    early_set        = set(early_df['stock_id'].tolist())  if not early_df.empty else set()
    strong_score_map = ({r['stock_id']:r.get('total_score',0) for _,r in strong_df.iterrows()}
                        if not strong_df.empty else {})
    early_score_map  = ({r['stock_id']:r.get('ew_score',0) for _,r in early_df.iterrows()}
                        if not early_df.empty else {})

    full_rows = []
    for sid, df in price_data.items():
        if df is None or df.empty: continue
        is_strong = sid in strong_set
        is_early  = sid in early_set
        if not is_strong and not is_early: continue  # 只匯出入選股
        last    = df.iloc[-1].to_dict()
        vm5     = last.get('vol_ma5',0) or 0
        close   = last.get('close',0)
        ma28    = last.get('MA28',0) or 0
        vol_r   = (last.get('volume',0)/vm5) if vm5>0 else 0
        dpct    = last.get('daily_return',0)*100
        mb      = ((close-ma28)/ma28*100) if ma28>0 else 0
        to_day  = last.get('turnover',0) or 0
        info    = inst_data.get(sid,{})
        inst_c  = max(info.get('foreign_consec',0), info.get('trust_consec',0))
        yoy_rev = fin_data.get(sid, None)
        ts = strong_score_map.get(sid, 0)
        es = early_score_map.get(sid, 0)
        try:
            ts_f = float(ts) if ts else 0.0
            es_f = float(es) if es else 0.0
            if is_strong and is_early:
                composite = round(es_f*0.45 + ts_f*0.55, 2)
            elif is_strong:
                composite = round(ts_f*0.55, 2)
            elif is_early:
                composite = round(es_f*0.45, 2)
            else:
                composite = 0.0
        except:
            composite = 0.0

        full_rows.append({
            'stock_id': sid, 'name': name_map.get(sid,sid),
            'close': round(close,2), 'vol_ratio': round(vol_r,2),
            'daily_return_pct': round(dpct,2), 'ma28_bias_pct': round(mb,2),
            'turnover_億': round(to_day/1e8,2),
            'rsi14': round(last.get('RSI14',0) or 0,1),
            'inst_consec_days': inst_c,
            'yoy_revenue_pct': yoy_rev,
            'foreign_today': info.get('foreign_today',0),
            'trust_today':   info.get('trust_today',0),
            'foreign_3d':    info.get('foreign_3d',0),
            'trust_3d':      info.get('trust_3d',0),
            'is_strong_confirm': is_strong,
            'is_early_breakout': is_early,
            'total_score': ts if ts else 0,
            'early_score': es if es else 0,
            'composite_score': composite,
        })

    full_out = (pd.DataFrame(full_rows)
                .sort_values('composite_score', ascending=False)
                .reset_index(drop=True)) if full_rows else pd.DataFrame(columns=EXPORT_COLS)
    os.makedirs('output', exist_ok=True)
    csv_fname = f'output/otc_{TODAY_STR}.csv'
    full_out[EXPORT_COLS].to_csv(csv_fname, index=False, encoding='utf-8-sig')
    print(f'✅ CSV：{csv_fname}（{len(full_out)} 筆，僅入選股）')
    return csv_fname, full_out


# ============================================================
# ★ 區塊 14：星星條件判斷（OTC 版本）
# ============================================================

def check_star(row):
    """
    OTC 精選條件，符合全部則顯示 ⭐
    - is_early_breakout == True
    - 2.5 <= daily_return_pct <= 6.0
    - inst_consec_days >= 2
    - foreign_3d > 0
    - trust_3d >= 0
    - trust_today > 0
    - vol_ratio >= 1.3
    - 6.0 <= ma28_bias_pct <= 12.0
    - 52 <= rsi14 <= 65
    - yoy_revenue_pct > 5
    - turnover_億 >= 1.5
    """
    try:
        is_early = row.get('is_early_breakout', False)
        if not is_early:
            return False
        ret   = float(row.get('daily_return_pct', 0))
        ic    = int(row.get('inst_consec_days', row.get('inst_consec', 0)))
        f3d   = float(row.get('foreign_3d', 0))
        t3d   = float(row.get('trust_3d', 0))
        tt    = float(row.get('trust_today', 0))
        vr    = float(row.get('vol_ratio', 0))
        bias  = float(row.get('ma28_bias', row.get('ma28_bias_pct', 0)))
        rsi   = float(row.get('rsi14', 0))
        yoy   = row.get('yoy_revenue_pct', None)
        to    = float(row.get('turnover_億', row.get('turnover_today', 0)/1e8
                              if row.get('turnover_today', 0) > 1e6 else 0))
        if yoy is None or (isinstance(yoy, float) and np.isnan(yoy)):
            return False
        yoy = float(yoy)
        return (
            2.5 <= ret <= 6.0 and
            ic >= 2 and
            f3d > 0 and
            t3d >= 0 and
            tt > 0 and
            vr >= 1.3 and
            6.0 <= bias <= 12.0 and
            52 <= rsi <= 65 and
            yoy > 5 and
            to >= 1.5
        )
    except:
        return False


# ============================================================
# ★ 區塊 15：HTML 報告 v7.2
# ============================================================

def export_html(price_data, inst_data, fin_data, name_map, strong_df, early_df,
                strong_candidates, early_candidates,
                strong_charts, early_charts, composite_charts,
                full_out):

    # ── 格式化輔助 ──
    def fn(v, d=2):
        try: return f'{float(v):,.{d}f}'
        except: return str(v)

    def ft(v):
        try: return f'{float(v)/1e8:.2f} 億'
        except: return '-'

    def pc(v):
        try:
            f = float(v)
            if f >= 5:   return f'<span style="color:#f85149;font-weight:700">{f:+.2f}%</span>'
            if f >= 1:   return f'<span style="color:#e6a817">{f:+.2f}%</span>'
            if f <= -3:  return f'<span style="color:#3fb950">{f:+.2f}%</span>'
            return f'{f:+.2f}%'
        except: return str(v)

    def rc(v):
        try:
            f = float(v)
            if f >= 78: return f'<span style="color:#f85149;font-weight:700">{f:.1f} ⚠️</span>'
            if f >= 65: return f'<span style="color:#e6a817">{f:.1f}</span>'
            return f'{f:.1f}'
        except: return str(v)

    def fy(v):
        try:
            f = float(v)
            if f >= 20: return f'<span style="color:#3fb950;font-weight:700">+{f:.0f}%</span>'
            if f >= 0:  return f'<span style="color:#e6a817">+{f:.0f}%</span>'
            return f'<span style="color:#f85149">{f:.0f}%</span>'
        except: return '<span style="color:#8b949e">-</span>'

    def yahoo_link(code, color):
        url = f'https://tw.stock.yahoo.com/quote/{code}.TWO'
        return (
            f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="color:{color};font-weight:700;text-decoration:none;font-size:1.7em;'
            f'display:inline-flex;align-items:center;gap:3px;" '
            f'onmouseover="this.style.opacity=\'0.75\'" '
            f'onmouseout="this.style.opacity=\'1\'">'
            f'{code}'
            f'<svg width="10" height="10" viewBox="0 0 10 10" fill="none" '
            f'xmlns="http://www.w3.org/2000/svg" style="opacity:.6">'
            f'<path d="M2 8L8 2M8 2H4M8 2V6" stroke="currentColor" '
            f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg></a>'
        )

    # ── 每檔前方的欄位標題列 ──
    INLINE_TH = (
        '<tr style="background:#1c2129;border-top:2px solid #30363d;">'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">排名</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">代碼</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">名稱</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">收盤價</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">漲幅%</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">量比</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">RSI14</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">MA28乖離</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">營收YoY</th>'
        '<th style="padding:5px 14px;font-size:.72em;color:#8b949e;font-weight:700;letter-spacing:.5px;white-space:nowrap;">法人連買</th>'
        '</tr>'
    )

    TH_COMMON = ('<th>排名</th><th>代碼</th><th>名稱</th><th>收盤價</th><th>漲幅%</th>'
                 '<th>量比</th><th>RSI14</th><th>MA28乖離</th><th>營收YoY</th><th>法人連買</th>')

    # ── FIX 1: row_inline_chart — 移除重複 <tr> tag ──
    def row_inline_chart(sid, charts, section_prefix=''):
        b64 = charts.get(sid)
        if not b64: return ''
        anchor_id = f'{section_prefix}-{sid}' if section_prefix else sid
        return (
            f'<tr id="{anchor_id}" style="background:#0d1117;">'
            f'<td colspan="10" style="padding:6px 16px 10px;">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="width:100%;max-width:1200px;border-radius:6px;display:block;"/>'
            f'</td></tr>'
        )

    # ── 綜合轉強 ──
    comp_df = full_out[full_out['composite_score'] != ''].copy()
    comp_df['_cs'] = pd.to_numeric(comp_df['composite_score'], errors='coerce')
    comp_df = (comp_df.dropna(subset=['_cs'])
               .sort_values('_cs', ascending=False)
               .head(TOP_COMPOSITE)
               .reset_index(drop=True))
    comp_df2 = comp_df.copy()
    comp_df2 = comp_df2.drop(columns=['rank'], errors='ignore')
    comp_df2.insert(0, 'rank', range(1, len(comp_df2)+1))

    medals_c = ['🏅','🎖️','⭐','✨','💫']
    cr = ''
    if not comp_df2.empty:
        for i, (_, r) in enumerate(comp_df2.iterrows()):
            sid       = r['stock_id']
            star      = check_star(r)
            name_cell = r['name'] + (' ⭐' if star else '')
            yr        = fin_data.get(sid, r.get('yoy_revenue_pct', None))
            ic        = int(r.get('inst_consec_days', 0))
            cr += INLINE_TH
            cr += (
                f'<tr>'
                f'<td style="text-align:center">{medals_c[i] if i<5 else "▪️"}</td>'
                f'<td>{yahoo_link(sid, "#bd8af5")}</td>'
                f'<td style="font-weight:600">{name_cell}</td>'
                f'<td style="font-weight:600">{fn(r["close"],1)}</td>'
                f'<td>{pc(r["daily_return_pct"])}</td>'
                f'<td>{fn(r["vol_ratio"])}x</td>'
                f'<td>{rc(r["rsi14"])}</td>'
                f'<td>{pc(r["ma28_bias_pct"])}</td>'
                f'<td>{fy(yr)}</td>'
                f'<td>{ic}天</td>'
                f'</tr>'
            )
            # FIX 2: 縮排對齊迴圈內層
            cr += row_inline_chart(sid, composite_charts, 'comp')
    else:
        cr = '<tr><td colspan="10" style="text-align:center;color:#8b949e;padding:24px">無綜合分資料</td></tr>'

    # ── 起漲預警 ──
    medals_e = ['🌱','🌿','🍃']
    er = ''
    if not early_df.empty:
        for _, r in early_df.head(TOP_EARLY).iterrows():
            sid       = r['stock_id']
            rk        = int(r['rank'])
            m         = medals_e[rk-1] if rk<=3 else f'#{rk}'
            star      = check_star(r)
            name_cell = r['name'] + (' ⭐' if star else '')
            er += INLINE_TH
            er += (
                f'<tr>'
                f'<td style="text-align:center">{m}</td>'
                f'<td>{yahoo_link(sid, "#3fb950")}</td>'
                f'<td>{name_cell}</td>'
                f'<td>{fn(r["close"],1)}</td>'
                f'<td>{pc(r["daily_return_pct"])}</td>'
                f'<td>{fn(r["vol_ratio"])}x</td>'
                f'<td>{rc(r["rsi14"])}</td>'
                f'<td>{pc(r["ma28_bias"])}</td>'
                f'<td>{fy(r["yoy_revenue_pct"])}</td>'
                f'<td>{r["inst_consec_days"]}天</td>'
                f'</tr>'
            )
            er += row_inline_chart(sid, early_charts, 'early')
    else:
        er = '<tr><td colspan="10" style="text-align:center;color:#8b949e;padding:24px">今日無起漲預警</td></tr>'

    # ── 強勢確認 ──
    medals_s = ['🥇','🥈','🥉']
    sr = ''
    if not strong_df.empty:
        early_ids = set(early_df['stock_id'].tolist()) if not early_df.empty else set()
        for _, r in strong_df.head(TOP_STRONG).iterrows():
            sid  = r['stock_id']
            rk   = int(r['rank'])
            m    = medals_s[rk-1] if rk<=3 else f'#{rk}'
            star = check_star({
                'is_early_breakout': sid in early_ids,
                'daily_return_pct':  r.get('daily_return_pct', 0),
                'inst_consec_days':  r.get('inst_consec', 0),
                'foreign_3d':        r.get('foreign_3d', 0),
                'trust_3d':          r.get('trust_3d', 0),
                'trust_today':       r.get('trust_today', 0),
                'vol_ratio':         r.get('vol_ratio', 0),
                'ma28_bias':         r.get('ma28_bias', 0),
                'rsi14':             r.get('rsi14', 0),
                'yoy_revenue_pct':   fin_data.get(sid, None),
                'turnover_億':       r.get('turnover_億', 0),
            })
            name_cell = r['name'] + (' ⭐' if star else '')
            yr = fin_data.get(sid, None)
            sr += INLINE_TH
            sr += (
                f'<tr>'
                f'<td style="text-align:center">{m}</td>'
                f'<td>{yahoo_link(sid, "#e6a817")}</td>'
                f'<td style="font-weight:600">{name_cell}</td>'
                f'<td style="font-weight:600">{fn(r["close"],1)}</td>'
                f'<td>{pc(r["daily_return_pct"])}</td>'
                f'<td>{fn(r["vol_ratio"])}x</td>'
                f'<td>{rc(r["rsi14"])}</td>'
                f'<td>{pc(r["ma28_bias"])}</td>'
                f'<td>{fy(yr)}</td>'
                f'<td>{r["inst_consec"]}天</td>'
                f'</tr>'
            )
            sr += row_inline_chart(sid, strong_charts, 'strong')
    else:
        sr = '<tr><td colspan="10" style="text-align:center;color:#8b949e;padding:24px">今日無符合條件個股</td></tr>'

    top1_id    = comp_df2.iloc[0]['stock_id']      if not comp_df2.empty else '-'
    top1_name  = comp_df2.iloc[0]['name']           if not comp_df2.empty else ''
    top1_score = fn(comp_df2.iloc[0]['_cs'])        if not comp_df2.empty else '-'

    # ── 快速瀏覽摘要 ──
    def make_chip(sid, name, anchor_id, is_star):
        star_class = ' st' if is_star else ''
        return (f'<a class="chip{star_class}" href="#{anchor_id}">'
                f'<span class="cd">{sid}</span>'
                f'<span class="nm">{name}</span>'
                f'</a>')

    # FIX 3: comp_chips 縮排對齊（頂格在 export_html 函式內）
    comp_chips = ''
    for _, r in comp_df2.iterrows():
        sid    = r['stock_id']
        star   = check_star(r)
        anchor = f'comp-{sid}' if sid in composite_charts else 'composite-section'
        comp_chips += make_chip(sid, r['name'], anchor, star)

    early_chips = ''
    if not early_df.empty:
        for _, r in early_df.head(TOP_EARLY).iterrows():
            sid    = r['stock_id']
            star   = check_star(r)
            anchor = f'early-{sid}' if sid in early_charts else 'early-section'
            early_chips += make_chip(sid, r['name'], anchor, star)

    strong_chips = ''
    if not strong_df.empty:
        early_ids_set = set(early_df['stock_id'].tolist()) if not early_df.empty else set()
        for _, r in strong_df.head(TOP_STRONG).iterrows():
            sid  = r['stock_id']
            star = check_star({
                'is_early_breakout': sid in early_ids_set,
                'daily_return_pct':  r.get('daily_return_pct', 0),
                'inst_consec_days':  r.get('inst_consec', 0),
                'foreign_3d':        r.get('foreign_3d', 0),
                'trust_3d':          r.get('trust_3d', 0),
                'trust_today':       r.get('trust_today', 0),
                'vol_ratio':         r.get('vol_ratio', 0),
                'ma28_bias':         r.get('ma28_bias', 0),
                'rsi14':             r.get('rsi14', 0),
                'yoy_revenue_pct':   fin_data.get(sid, None),
                'turnover_億':       r.get('turnover_億', 0),
            })
            anchor = f'strong-{sid}' if sid in strong_charts else 'strong-section'
            strong_chips += make_chip(sid, r['name'], anchor, star)

    html = f'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>上櫃操盤手 — {TODAY_DISP} 選股報告</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&display=swap');
:root{{--bg:#0d1117;--bg2:#161b22;--bg3:#1c2129;--border:#30363d;
  --gold:#e6a817;--green:#3fb950;--purple:#bd8af5;--coral:#f85149;
  --text:#e6edf3;--text2:#c9d1d9;--text3:#8b949e;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:'Noto Sans TC',sans-serif;font-size:15px;line-height:1.65;}}
.header{{background:linear-gradient(135deg,#0a1628 0%,#1a2744 50%,#0a1628 100%);
  border-bottom:2px solid var(--gold);padding:36px 48px;}}
.header-label{{color:var(--gold);font-size:.8em;font-weight:700;letter-spacing:4px;margin-bottom:8px;}}
.header h1{{font-size:1.85em;font-weight:900;}}
.header h1 span{{color:var(--gold);}}
.header-meta{{margin-top:12px;color:var(--text3);font-size:.88em;}}
.header-meta strong{{color:var(--text2);}}
.stats-bar{{display:flex;border-bottom:1px solid var(--border);}}
.stat-item{{flex:1;padding:18px 24px;border-right:1px solid var(--border);background:var(--bg2);}}
.stat-item:last-child{{border-right:none;}}
.stat-label{{font-size:.76em;color:var(--text3);letter-spacing:1px;margin-bottom:4px;}}
.stat-value{{font-size:1.55em;font-weight:900;color:var(--gold);}}
.stat-sub{{font-size:.76em;color:var(--text3);margin-top:2px;}}
.container{{max-width:1440px;margin:0 auto;padding:32px;}}
.section{{margin-bottom:48px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:var(--bg2);}}
.section-header{{padding:20px 28px;display:flex;align-items:center;gap:14px;}}
.section-header.composite{{background:linear-gradient(90deg,#2a1a3a,#1c2129);border-bottom:2px solid var(--purple);}}
.section-header.early{{background:linear-gradient(90deg,#1a2a1a,#1c2129);border-bottom:2px solid var(--green);}}
.section-header.strong{{background:linear-gradient(90deg,#2a2010,#1c2129);border-bottom:2px solid var(--gold);}}
.section-icon{{font-size:1.6em;}}
.section-title h2{{font-size:1.2em;font-weight:900;}}
.section-title p{{font-size:.82em;color:var(--text3);margin-top:2px;}}
.table-wrap{{overflow-x:auto;}}
table{{width:100%;border-collapse:collapse;font-size:.88em;}}
thead tr{{background:var(--bg3);border-bottom:1px solid var(--border);}}
th{{padding:12px 14px;text-align:left;color:var(--text3);font-weight:700;font-size:.8em;letter-spacing:.4px;white-space:nowrap;}}
td{{padding:13px 14px;border-bottom:1px solid rgba(48,54,61,.5);color:var(--text2);vertical-align:middle;}}
tbody tr:hover{{background:rgba(255,255,255,.03);}}
tbody tr:nth-child(1 of .data-row){{background:rgba(230,168,23,.07);}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;padding:12px 28px;background:var(--bg3);
  border-top:1px solid var(--border);font-size:.76em;color:var(--text3);}}
.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;}}
.footer{{text-align:center;padding:24px;color:var(--text3);font-size:.8em;border-top:1px solid var(--border);background:var(--bg2);}}
.fixed-nav{{position:fixed;bottom:24px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:6px;}}
.fixed-nav a{{display:block;text-align:center;padding:11px 10px;background:rgba(13,17,23,.92);
  color:var(--gold);font-size:15px;font-weight:700;text-decoration:none;border-radius:10px;
  border:1px solid rgba(230,168,23,.45);letter-spacing:2px;min-width:72px;transition:all .2s;}}
.fixed-nav a:hover{{background:rgba(230,168,23,.12);}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(20px)}}to{{opacity:1;transform:translateY(0)}}}}
.section{{animation:fadeUp .5s ease-out both;}}
.section:nth-child(2){{animation-delay:.1s;}}
.section:nth-child(3){{animation-delay:.2s;}}
</style>
</head>
<body>

<div class="header">
  <div class="header-label">上櫃操盤手 · 選股系統 v7.2</div>
  <h1>上櫃操盤手 — <span>{TODAY_DISP}</span> 收盤選股報告</h1>
  <div class="header-meta">
    掃描 <strong>{len(price_data)}</strong> 檔 ｜
    強勢確認 <strong>{len(strong_candidates)}</strong> 檔 ｜
    起漲預警 <strong>{len(early_candidates)}</strong> 檔
    &nbsp;·&nbsp;
    <span style="color:var(--purple)">↗ 點擊代碼開 Yahoo 股市</span>
  </div>
</div>

<div class="stats-bar">
  <div class="stat-item"><div class="stat-label">掃描標的</div><div class="stat-value">{len(price_data)}</div><div class="stat-sub">上櫃活躍股</div></div>
  <div class="stat-item"><div class="stat-label">強勢確認股</div><div class="stat-value" style="color:var(--coral)">{len(strong_candidates)}</div><div class="stat-sub">Top {TOP_STRONG} 顯示</div></div>
  <div class="stat-item"><div class="stat-label">起漲預警股</div><div class="stat-value" style="color:var(--green)">{len(early_candidates)}</div><div class="stat-sub">Top {TOP_EARLY} 顯示</div></div>
  <div class="stat-item"><div class="stat-label">綜合轉強 TOP1</div><div class="stat-value" style="font-size:1.15em;color:var(--purple)">{top1_id} {top1_name}</div><div class="stat-sub">綜合分 {top1_score}</div></div>
  <div class="stat-item"><div class="stat-label">報告日期</div><div class="stat-value" style="font-size:1.15em">{TODAY_DISP}</div><div class="stat-sub">收盤後分析</div></div>
</div>

<div class="container">

<div class="summary-block">
<style>
.summary-block{{padding:20px 32px 4px;max-width:1440px;margin:0 auto;}}
.ss{{margin-bottom:10px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:var(--bg2);}}
.sh{{display:flex;align-items:center;gap:10px;padding:9px 16px;background:var(--bg3);}}
.sh .ttl{{font-size:13.5px;font-weight:700;color:var(--text);}}
.sh .bdg{{font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px;white-space:nowrap;}}
.sh .sub{{font-size:11px;color:var(--text3);margin-left:auto;}}
.cg{{padding:9px 14px 11px;display:flex;flex-wrap:wrap;gap:6px;}}
.chip{{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;
  border:1px solid var(--border);font-size:12.5px;color:var(--text2);
  background:var(--bg);white-space:nowrap;text-decoration:none;transition:opacity .15s;}}
.chip:hover{{opacity:.65;}}
.chip .cd{{font-weight:700;}}
.chip .nm{{color:var(--text3);}}
.chip.st .cd{{color:#e6a817;}}
.chip.st{{border-color:rgba(230,168,23,.4);background:rgba(230,168,23,.08);}}
.s-comp .sh{{border-left:3px solid var(--purple);}}
.s-early .sh{{border-left:3px solid var(--green);}}
.s-strong .sh{{border-left:3px solid var(--gold);}}
.bdg-c{{background:rgba(189,138,245,.15);color:var(--purple);}}
.bdg-e{{background:rgba(63,185,80,.12);color:var(--green);}}
.bdg-s{{background:rgba(230,168,23,.12);color:var(--gold);}}
.sum-legend{{font-size:11px;color:var(--text3);padding:2px 2px 8px;display:flex;align-items:center;gap:6px;}}
.sum-dot{{width:7px;height:7px;border-radius:50%;background:var(--gold);display:inline-block;opacity:.8;}}
</style>

<div class="ss s-comp">
  <div class="sh">
    <span style="width:7px;height:7px;border-radius:50%;background:var(--purple);display:inline-block;flex-shrink:0;"></span>
    <span class="ttl">綜合轉強潛力股</span>
    <span class="bdg bdg-c">Top {TOP_COMPOSITE}</span>
    <span class="sub">↓ 點擊跳至詳細表</span>
  </div>
  <div class="cg">{comp_chips}</div>
</div>

<div class="ss s-early">
  <div class="sh">
    <span style="width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;flex-shrink:0;"></span>
    <span class="ttl">即將起漲潛力股</span>
    <span class="bdg bdg-e">Top {TOP_EARLY}</span>
    <span class="sub">↓ 點擊跳至詳細表</span>
  </div>
  <div class="cg">{early_chips}</div>
</div>

<div class="ss s-strong">
  <div class="sh">
    <span style="width:7px;height:7px;border-radius:50%;background:var(--gold);display:inline-block;flex-shrink:0;"></span>
    <span class="ttl">強勢確認股</span>
    <span class="bdg bdg-s">Top {TOP_STRONG}</span>
    <span class="sub">↓ 點擊跳至詳細表</span>
  </div>
  <div class="cg">{strong_chips}</div>
</div>

<div class="sum-legend"><span class="sum-dot"></span>金色底 = ⭐ 精選條件全符合 ｜ 點擊標籤跳至對應詳細表</div>
</div>

<div class="section" id="composite-section">
  <div class="section-header composite">
    <div class="section-icon">🔮</div>
    <div class="section-title">
      <h2>綜合轉強潛力股 Top {TOP_COMPOSITE}</h2>
      <p>綜合分 = 起漲分×0.45 + 強勢分×0.55 ｜ ⭐ 精選條件全中</p>
    </div>
  </div>
  <div class="table-wrap">
    <table><thead></thead><tbody>{cr}</tbody></table>
  </div>
  <div class="legend">
    <div><span class="dot" style="background:var(--purple)"></span> 綜合分 = early×0.45 + total×0.55</div>
    <div style="color:var(--purple)">↗ 點擊紫色代碼開 Yahoo 股市</div>
    <div>⭐ = 精選條件全符合（起漲+籌碼+量價+基本面）</div>
  </div>
</div>

<div class="section" id="early-section">
  <div class="section-header early">
    <div class="section-icon">🌱</div>
    <div class="section-title">
      <h2>即將起漲潛力股 Top {TOP_EARLY}</h2>
      <p>硬條件過濾 + 財務/籌碼加分排名 ｜ ⭐ 精選條件全中</p>
    </div>
  </div>
  <div class="table-wrap">
    <table><thead></thead><tbody>{er}</tbody></table>
  </div>
  <div class="legend">
    <div><span class="dot" style="background:var(--green)"></span> YoY&gt;20%→+16 ｜ 法人連買≥2→+24 ｜ 60日&lt;25%→+22</div>
    <div style="color:var(--green)">↗ 點擊綠色代碼開 Yahoo 股市</div>
    <div>⭐ = 精選條件全符合</div>
  </div>
</div>

<div class="section" id="strong-section">
  <div class="section-header strong">
    <div class="section-icon">🔥</div>
    <div class="section-title">
      <h2>強勢確認股 Top {TOP_STRONG}</h2>
      <p>量價齊揚 + 法人認同 + 技術突破 ｜ ⭐ 精選條件全中</p>
    </div>
  </div>
  <div class="table-wrap">
    <table><thead></thead><tbody>{sr}</tbody></table>
  </div>
  <div class="legend">
    <div><span class="dot" style="background:var(--gold)"></span> 量比×1.6 + 20日新高×1.4 + MA28乖離×1.0 + 連買×3.0 + 漲幅×0.8 + Z-score</div>
    <div><span style="color:var(--coral)">RSI⚠️</span> ≥78 追高需謹慎</div>
    <div style="color:var(--gold)">↗ 點擊金色代碼開 Yahoo 股市</div>
    <div>⭐ = 精選條件全符合</div>
  </div>
</div>

</div>

<div class="footer">
  上櫃操盤手選股系統 v7.2 ｜ {TODAY_DISP} ｜ early×0.45+total×0.55 ｜ 僅供參考，不構成投資建議
</div>

<nav class="fixed-nav">
  <a href="#composite-section">綜合轉強</a>
  <a href="#early-section">即將起漲</a>
  <a href="#strong-section">強勢確認</a>
</nav>

</body></html>'''

    os.makedirs('output', exist_ok=True)
    html_fname = f'output/OTC_report_{TODAY_STR}.html'
    with open(html_fname, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ HTML：{html_fname}（{len(html)//1024} KB）')
    return html_fname

# ============================================================
# 區塊 16：Telegram
# ============================================================

def send_telegram(strong_df, early_df, strong_candidates, early_candidates):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print('⚠️  未設定 Telegram，跳過')
        return
    lines = [
        f"📊 *上櫃操盤手 v7.2 — {TODAY_DISP}*", "",
        f"掃描{len(price_data_global)}檔 強勢{len(strong_candidates)} 預警{len(early_candidates)}", "",
    ]
    if not strong_df.empty:
        lines.append("*🔥 強勢Top5:*")
        for _, r in strong_df.head(5).iterrows():
            lines.append(f"  #{int(r['rank'])} {r['stock_id']} {r['name']} {r['total_score']:.1f}分")
        lines.append("")
    if not early_df.empty:
        lines.append("*🌱 預警Top5:*")
        for _, r in early_df.head(5).iterrows():
            lines.append(f"  #{int(r['rank'])} {r['stock_id']} {r['name']} {r['total_ew_score']:.1f}分")
    if GITHUB_PAGES_URL:
        lines.append(f"\n🌐 [報告]({GITHUB_PAGES_URL})")
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id':TELEGRAM_CHAT_ID,'text':'\n'.join(lines),'parse_mode':'Markdown'},
            timeout=15)
        print('✅ TG已發送' if resp.status_code==200 else f'⚠️{resp.text}')
    except Exception as e:
        print(f'⚠️TG:{e}')

# ============================================================
# 區塊 17：Email
# ============================================================

def send_email(csv_fname, html_fname, strong_df, early_df, strong_candidates, early_candidates):
    if not GMAIL_USER or not GMAIL_APP_PASS or not EMAIL_TO:
        print('⚠️  未設定 Email，跳過')
        return
    msg = MIMEMultipart('mixed')
    msg['Subject'] = f'上櫃操盤手 v7.2 {TODAY_DISP} 強勢{len(strong_candidates)} 預警{len(early_candidates)}'
    msg['From']    = GMAIL_USER
    msg['To']      = EMAIL_TO
    body = f'上櫃操盤手 v7.2 {TODAY_DISP}\n掃描{len(price_data_global)}檔'
    if GITHUB_PAGES_URL: body += f'\n報告：{GITHUB_PAGES_URL}'
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    for fpath in [csv_fname]:  # 只附加CSV，HTML請至GitHub Pages查看
        if fpath and os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition','attachment',filename=os.path.basename(fpath))
            msg.attach(part)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASS)
            s.sendmail(GMAIL_USER, EMAIL_TO.split(','), msg.as_string())
        print('✅ Email已發送')
    except Exception as e:
        print(f'⚠️Email:{e}')

# ============================================================
# 全域變數
# ============================================================
price_data_global = {}
fin_data_global   = {}

# ============================================================
# 主程式
# ============================================================

def main():
    global price_data_global, fin_data_global

    print("="*60)
    print("上櫃操盤手選股系統 v7.2")
    print("="*60)

    install_system_deps()
    font_path, _ = init_chinese_font()

    stock_ids, name_map  = load_stock_list()
    industry_map         = load_industry_map()
    print(f'有效代碼：{len(stock_ids)} 檔')

    api      = login_finmind()
    use_rest = detect_api_mode(api, stock_ids)

    price_data        = fetch_all_prices(stock_ids, api, use_rest)
    price_data_global = price_data
    valid_ids         = list(price_data.keys())

    inst_data       = fetch_all_inst(valid_ids, api, use_rest)
    fin_data        = fetch_all_revenue(valid_ids, api, use_rest)
    fin_data_global = fin_data

    strong_df, strong_candidates = run_strong_filter(price_data, inst_data, fin_data, name_map, industry_map)
    early_df,  early_candidates  = run_early_filter(price_data, inst_data, fin_data, name_map, industry_map)

    csv_fname, full_out = export_csvs(price_data, inst_data, fin_data, name_map, strong_df, early_df)

    # ── 綜合分 Top15 sid ──
    comp_chart_df = full_out[full_out['composite_score'] != ''].copy()
    comp_chart_df['_cs'] = pd.to_numeric(comp_chart_df['composite_score'], errors='coerce')
    comp_chart_sids = (comp_chart_df.dropna(subset=['_cs'])
                       .sort_values('_cs', ascending=False)
                       .head(TOP_COMPOSITE)['stock_id'].tolist())

    # ── K線圖：各區全數繪製，對應顯示上限 ──
    print('\n[K線圖] 繪製中...')
    strong_charts, early_charts, composite_charts = {}, {}, {}

    if not strong_df.empty:
        for sid in strong_df['stock_id'].head(TOP_STRONG).tolist():
            b = draw_kline(sid, price_data, name_map, font_path, '強勢確認')
            if b: strong_charts[sid] = b

    if not early_df.empty:
        for sid in early_df['stock_id'].head(TOP_EARLY).tolist():
            b = draw_kline(sid, price_data, name_map, font_path, '起漲預警')
            if b: early_charts[sid] = b

    for sid in comp_chart_sids:
        b = draw_kline(sid, price_data, name_map, font_path, '綜合轉強')
        if b: composite_charts[sid] = b

    print(f'  強勢{len(strong_charts)} 預警{len(early_charts)} 綜合{len(composite_charts)}')

    html_fname = export_html(
        price_data, inst_data, fin_data, name_map,
        strong_df, early_df, strong_candidates, early_candidates,
        strong_charts, early_charts, composite_charts, full_out
    )

    send_telegram(strong_df, early_df, strong_candidates, early_candidates)
    send_email(csv_fname, html_fname, strong_df, early_df, strong_candidates, early_candidates)

    print("\n"+"="*60)
    print(f"✅ CSV ：{csv_fname}")
    print(f"✅ HTML：{html_fname}")
    print("="*60)


if __name__ == '__main__':
    main()

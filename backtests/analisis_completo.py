"""
ANÁLISIS COMPLETO — v3 vectorizado
"""
import json,time,pickle
import numpy as np
import pandas as pd
import requests
from scipy.stats import linregress
from datetime import datetime,timezone,timedelta
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier

BINANCE_FAPI="https://testnet.binancefuture.com"
COMMISSION=0.0005;SPREAD=0.0002;SLIPPAGE_ATR_MULT=0.1
CAP=100.0;RISK=0.01
SYM=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT"]
CFG={
    'BTCUSDT':{'sm':2.8,'ct':74,'fd':30,'tp':1.5},
    'ETHUSDT':{'sm':2.6,'ct':72,'fd':30,'tp':1.8},
    'SOLUSDT':{'sm':2.3,'ct':70,'fd':90,'tp':2.0},
    'XRPUSDT':{'sm':2.3,'ct':68,'fd':60,'tp':1.8},
    'BNBUSDT':{'sm':2.8,'ct':68,'fd':30,'tp':1.6},
}

_m=None;_mc=None
def _l():
    global _m,_mc
    if _m is None:
        with open('ml_model.pkl','rb')as f:_m=pickle.load(f);_mc=_m.feature_names_in_
def proba(f):
    _l();X=pd.DataFrame([f])
    for c in _mc:
        if c not in X.columns:X[c]=0
    return _m.predict_proba(X[_mc].fillna(0))[0,1]

def fch(sym,iv,sd,ed):
    ab=[];n=0;st=sd
    while st<ed and n<50:
        n+=1;params={"symbol":sym,"interval":iv,"limit":1500,"startTime":int(st.timestamp()*1000)}
        for _ in range(3):
            try:
                r=requests.get(f"{BINANCE_FAPI}/fapi/v1/klines",params=params,timeout=30)
                if r.status_code==200:break
            except:pass
        else:break
        rows=r.json()
        if not rows:break
        cols=["ot","o","h","l","c","v","ct","qv","tr","tbb","tbq","ig"]
        df=pd.DataFrame(rows,columns=cols);df["ot"]=pd.to_datetime(df["ot"],unit="ms",utc=True)
        for c in ["o","h","l","c","v"]:df[c]=pd.to_numeric(df[c])
        ab.append(df);st=df["ot"].iloc[-1]+timedelta(minutes=1);time.sleep(0.1)
    if not ab:return None
    full=pd.concat(ab,ignore_index=True)
    full=full.drop_duplicates(subset=["ot"]).sort_values("ot").reset_index(drop=True)
    full=full[full["ot"]<ed].reset_index(drop=True)
    full["ts"]=full["ot"].astype('int64')//10**6
    return full

def cl_st(h,l,c,m):
    n=len(h);hl2=(h+l)/2
    tr=np.maximum.reduce([h-l,np.abs(h-np.roll(c,1)),np.abs(l-np.roll(c,1))]);tr[0]=h[0]-l[0]
    atr=pd.Series(tr).ewm(span=10,adjust=False).mean().values
    ub=hl2+m*atr;lb=hl2-m*atr;up=ub.copy();lo=lb.copy();d=np.ones(n,dtype=int)
    for i in range(1,n):
        if c[i-1]<=up[i-1]:up[i]=min(up[i],up[i-1])
        if c[i-1]>=lo[i-1]:lo[i]=max(lo[i],lo[i-1])
        d[i]=1 if c[i]>up[i-1] else(-1 if c[i]<lo[i-1] else d[i-1])
    return d

def cl_st14(h,l,c,m):
    n=len(h);hl2=(h+l)/2
    tr=np.maximum.reduce([h-l,np.abs(h-np.roll(c,1)),np.abs(l-np.roll(c,1))]);tr[0]=h[0]-l[0]
    atr=pd.Series(tr).ewm(span=14,adjust=False).mean().values
    ub=hl2+m*atr;lb=hl2-m*atr;up=ub.copy();lo=lb.copy();d=np.ones(n,dtype=int)
    for i in range(1,n):
        if c[i-1]<=up[i-1]:up[i]=min(up[i],up[i-1])
        if c[i-1]>=lo[i-1]:lo[i]=max(lo[i],lo[i-1])
        d[i]=1 if c[i]>up[i-1] else(-1 if c[i]<lo[i-1] else d[i-1])
    return d

def ema_arr(s,p):
    return pd.Series(s).ewm(span=p,adjust=False).mean().values

def run():
    sd=datetime(2026,1,1,tzinfo=timezone.utc);ed=datetime.now(timezone.utc)
    _l()
    print(f"{'='*60}\n  Fetching: {sd.date()}→{ed.date()}\n{'='*60}")

    # Fetch + precompute ALL indicators
    all_data={}
    for sym in SYM:
        print(f"  {sym}...",end=' ',flush=True)
        d={tf:fch(sym,tf,sd,ed) for tf in ['1d','4h','1h','15m','5m']}
        # Precompute indicator arrays for fast indexing
        o1h=d['1h']['o'].values;h1h=d['1h']['h'].values;l1h=d['1h']['l'].values
        c1h=d['1h']['c'].values;v1h=d['1h']['v'].values;ts1h=d['1h']['ts'].values
        n1h=len(c1h)

        # EMA arrays for 1h
        ema20_1h=ema_arr(c1h,20);ema50_1h=ema_arr(c1h,50)
        # ATR14 for 1h
        tr=[max(h1h[i]-l1h[i],abs(h1h[i]-c1h[i-1]),abs(l1h[i]-c1h[i-1]))for i in range(1,n1h)]
        tr_arr=np.array(tr);atr14_1h=np.full(n1h,np.nan);atr14_1h[14:]=pd.Series(tr_arr).rolling(14).mean().values[13:]
        bb20_sma=np.full(n1h,np.nan);bb20_std=np.full(n1h,np.nan)
        for j in range(19,n1h):
            bb20_sma[j]=c1h[j-19:j+1].mean();bb20_std[j]=c1h[j-19:j+1].std()

        # Precompute for 5m, 15m
        h5=d['5m']['h'].values;l5=d['5m']['l'].values;c5=d['5m']['c'].values;v5=d['5m']['v'].values;ts5=d['5m']['ts'].values;n5=len(c5)
        h15=d['15m']['h'].values;l15=d['15m']['l'].values;c15=d['15m']['c'].values;v15=d['15m']['v'].values;ts15=d['15m']['ts'].values;n15=len(c15)
        # 5m EMA
        ema20_5=ema_arr(c5,20);ema50_5=ema_arr(c5,50)
        ema20_15=ema_arr(c15,20);ema50_15=ema_arr(c15,50)

        # Supertrend for 5m, 15m (vectorized arrays)
        st5=cl_st(h5,l5,c5,CFG[sym]['sm']);st15=cl_st(h15,l15,c15,CFG[sym]['sm'])
        # 1h Supertrend 14 period
        if n1h>=16:
            st1h=cl_st14(h1h,l1h,c1h,CFG[sym]['sm'])
        else:
            st1h=np.ones(n1h,dtype=int)

        # Volume ratio arrays
        vr5=np.full(n5,1.0);vr15=np.full(n15,1.0)
        for j in range(19,n5):vr5[j]=v5[j]/v5[j-19:j+1].mean()
        for j in range(19,n15):vr15[j]=v15[j]/v15[j-19:j+1].mean()

        d['_p']={
            'n1h':n1h,'ts1h':ts1h,'c1h':c1h,'h1h':h1h,'l1h':l1h,'o1h':o1h,'v1h':v1h,
            'ema20_1h':ema20_1h,'ema50_1h':ema50_1h,'atr14_1h':atr14_1h,
            'bb20_sma':bb20_sma,'bb20_std':bb20_std,
            'n5':n5,'ts5':ts5,'c5':c5,'h5':h5,'l5':l5,'v5':v5,
            'n15':n15,'ts15':ts15,'c15':c15,'h15':h15,'l15':l15,'v15':v15,
            'ema20_5':ema20_5,'ema50_5':ema50_5,'ema20_15':ema20_15,'ema50_15':ema50_15,
            'st5':st5,'st15':st15,'st1h':st1h,'vr5':vr5,'vr15':vr15,
        }
        # Store 4h/1d raw (needed less often)
        d['_4h']={'h':d['4h']['h'].values,'l':d['4h']['l'].values,'c':d['4h']['c'].values,'o':d['4h']['o'].values,'v':d['4h']['v'].values,'ts':d['4h']['ts'].values}
        d['_1d']={'h':d['1d']['h'].values,'l':d['1d']['l'].values,'c':d['1d']['c'].values,'o':d['1d']['o'].values,'v':d['1d']['v'].values,'ts':d['1d']['ts'].values}
        all_data[sym]=d
        print(f"{n1h} 1h bars")

    print(f"\n{'='*60}\n  Processing...\n{'='*60}")

    all_tr=[]  # List of trade records with features and pnl

    for sym in SYM:
        print(f"  {sym}...",end=' ',flush=True)
        d=all_data[sym];p=d['_p'];cfg=CFG[sym]
        n=n1h=p['n1h'];ts1h=p['ts1h'];c1h=p['c1h'];h1h=p['h1h'];l1h=p['l1h'];o1h=p['o1h'];v1h=p['v1h']
        ema20_1h=p['ema20_1h'];ema50_1h=p['ema50_1h'];atr14_1h=p['atr14_1h']
        bb20_sma=p['bb20_sma'];bb20_std=p['bb20_std']
        n5=p['n5'];ts5=p['ts5'];c5=p['c5'];h5=p['h5'];l5=p['l5'];v5=p['v5']
        n15=p['n15'];ts15=p['ts15'];c15=p['c15'];h15=p['h15'];l15=p['l15'];v15=p['v15']
        ema20_5=p['ema20_5'];ema50_5=p['ema50_5'];ema20_15=p['ema20_15'];ema50_15=p['ema50_15']
        st5=p['st5'];st15=p['st15'];st1h=p['st1h'];vr5=p['vr5'];vr15=p['vr15']
        d4h=d['_4h'];d1d=d['_1d']

        sym_tr=0
        for i in range(10,n):
            ts=ts1h[i]
            # Index into sub-data (find <= ts)
            i5=np.searchsorted(ts5,ts,side='right')-1
            i15=np.searchsorted(ts15,ts,side='right')-1
            i4h=np.searchsorted(d4h['ts'],ts,side='right')-1
            i1d=np.searchsorted(d1d['ts'],ts,side='right')-1
            if i5<30 or i15<30 or i4h<5 or i1d<2:continue

            # Phase detection
            slope=linregress(np.arange(20),c1h[i-19:i+1])[0]if i>=19 else 0
            bw=(bb20_sma[i]+2*bb20_std[i]-(bb20_sma[i]-2*bb20_std[i]))/c1h[i]if not np.isnan(bb20_sma[i])and bb20_std[i]>0 else 0.05
            atr_v=atr14_1h[i];vol_r=float(v1h[i]/v1h[i-19:i+1].mean())if i>=19 else 1.0
            atr_r=atr_v/c1h[i]if atr_v and atr_v==atr_v and c1h[i]>0 else 0
            if bw<0.03 and atr_r<0.01 and vol_r<0.8:ph='compressing'
            elif bw>0.06 and vol_r>1.3 and abs(slope)>0.005:ph='expanding'
            elif atr_r>0.015 and abs(slope)>0.01:ph='trending'
            elif atr_r<0.008 and abs(slope)<0.003:ph='ranging'
            else:ph='neutral'

            # Momentum multi-tf
            ms={}
            for tf,wt in [('4h',0.5),('1h',0.35),('15m',0.15)]:
                if tf=='4h':
                    ii=i4h;cc=d4h['c'][:ii+1];vv=d4h['v'][:ii+1];ema20=pd.Series(cc).ewm(span=20,adjust=False).mean().iloc[-1];ema50=pd.Series(cc).ewm(span=50,adjust=False).mean().iloc[-1]
                elif tf=='1h':
                    ii=i;cc=c1h[:ii+1];vv=v1h[:ii+1];ema20=ema20_1h[ii];ema50=ema50_1h[ii]
                else:
                    ii=i15;cc=c15[:ii+1];vv=v15[:ii+1];ema20=ema20_15[ii];ema50=ema50_15[ii]
                if pd.isna(ema20)or pd.isna(ema50):continue
                d_s=1 if ema20>ema50 else -1
                vrr=vv[-1]/vv[-20:].mean()if len(vv)>=20 else 1
                ms[tf]=(d_s*0.7+(vrr-1)*0.5*0.3)*wt
            ms_tot=sum(ms.values())if ms else 0
            md='bullish'if ms_tot>0.15 else('bearish'if ms_tot<-0.15 else'neutral')

            # Fibonacci
            fb=None
            if i1d>=1:
                fd=cfg['fd'];start=max(0,i1d-fd+1);rd_h=d1d['h'][start:i1d+1];rd_l=d1d['l'][start:i1d+1]
                fh=rd_h.max();fl=rd_l.min();rng=fh-fl
                if rng>0:
                    lvls={k:fl+rng*k for k in[0,0.25,0.5,0.75,1,1.25,1.5,1.75,2]}
                    sl=sorted(lvls.items(),key=lambda x:x[1])
                    pr=c1h[i]
                    for j in range(len(sl)-1):
                        if sl[j][1]<=pr<=sl[j+1][1]:fb=(sl[j][0],sl[j][1],sl[j+1][0],sl[j+1][1]);break
            if fb is None:continue

            # Engulfing
            def be(k_h,k_l,k_c,k_o):
                if len(k_c)<2:return False
                return k_o[-2]<k_c[-2]and k_o[-1]>k_c[-1]and k_o[-1]>k_c[-2]and k_c[-1]<k_o[-2]
            def bu(k_h,k_l,k_c,k_o):
                if len(k_c)<2:return False
                return k_o[-2]>k_c[-2]and k_o[-1]<k_c[-1]and k_o[-1]<k_c[-2]and k_c[-1]>k_o[-2]
            sig='WAIT';dr='neutral'
            if be(d4h['h'][:i4h+1],d4h['l'][:i4h+1],d4h['c'][:i4h+1],d4h['o'][:i4h+1]):sig='SHORT';dr='bearish'
            elif bu(d4h['h'][:i4h+1],d4h['l'][:i4h+1],d4h['c'][:i4h+1],d4h['o'][:i4h+1]):sig='LONG';dr='bullish'
            if ph in('ranging','neutral'):sig='WAIT'

            # Filter indicators (from precomputed arrays)
            cv=0
            if i15>=14:
                hs=h15[i15-13:i15+1];ls=l15[i15-13:i15+1];cs=c15[i15-13:i15+1]
                tr=np.maximum.reduce([hs-ls,np.abs(hs-np.roll(cs,1)),np.abs(ls-np.roll(cs,1))]);tr[0]=hs[0]-ls[0]
                total=hs.max()-ls.min()
                cv=float(np.clip(100*np.log10(tr.sum()/total)/np.log10(14),0,100))if total>0 else 50
            tradeable=cv<cfg['ct']

            wr5=-50;wr5p=-50
            if i5>=14:
                hh=h5[i5-13:i5+1].max();ll=l5[i5-13:i5+1].min()
                wr5=-100*(hh-c5[i5])/(hh-ll)if hh!=ll else -50
                if i5>=15:
                    hhp=h5[i5-14:i5].max();llp=l5[i5-14:i5].min()
                    wr5p=-100*(hhp-c5[i5-1])/(hhp-llp)if hhp!=llp else -50
            wr15=-50
            if i15>=14:
                hh=h15[i15-13:i15+1].max();ll=l15[i15-13:i15+1].min()
                wr15=-100*(hh-c15[i15])/(hh-ll)if hh!=ll else -50
            lt=wr5p<=-80 and wr5>-80;st_wr=wr5p>=-20 and wr5<-20

            st5_v='bullish'if st5[i5]==1 else'bearish'
            st15_v='bullish'if st15[i15]==1 else'bearish'
            st1h_v='bullish'if st1h[i4h]==1 else'bearish'
            dirs=[st5_v,st15_v,st1h_v];b=dirs.count('bullish');be_c=dirs.count('bearish')
            al=b==3 or be_c==3;st_bias='bullish'if b>be_c else('bearish'if be_c>b else'mixed')

            # Veto
            if sig in('LONG','SHORT'):
                v=False
                if not tradeable:v=True
                if not v:
                    if(sig=='SHORT'and lt)or(sig=='LONG'and st_wr):v=True
                if not v:
                    if(sig=='SHORT'and st_bias=='bullish'and al)or(sig=='LONG'and st_bias=='bearish'and al):v=True
                if v:sig='WAIT'

            # Execute trade
            trade_obj=None
            if sig in('LONG','SHORT'):
                ep=float(c5[i5])
                if sig=='SHORT':sl=float(h15[i15-9:i15+1].max())*1.002
                else:sl=float(l15[i15-9:i15+1].min())*0.998
                sl_pct=abs(ep-sl)/ep
                if sl_pct>0.018:sl=ep*(1-0.018)if ep>sl else ep*(1+0.018);sl_pct=0.018
                ru=CAP*RISK;nl=ru/sl_pct;ct=nl/ep
                tp=ep+cfg['tp']*(ep-sl)if ep>sl else ep-cfg['tp']*(sl-ep)
                # Exit simulation
                dr2='bullish'if dr=='LONG'else'bearish'
                er=ep*(1+SPREAD)if dr2=='bullish'else ep*(1-SPREAD)
                se=SLIPPAGE_ATR_MULT*5*(atr_v if atr_v and atr_v==atr_v else 5)*0.1
                remain=ct
                for j5 in range(i5+1,n5):
                    if dr2=='bullish':
                        if h5[j5]>=tp:pnl=(tp-er)*remain;trade_obj={'pnl':pnl-tp*remain*COMMISSION,'type':'tp','dir':dr2};break
                        if l5[j5]<=sl:epr=sl-se;pnl=(epr-er)*remain;trade_obj={'pnl':pnl-epr*remain*COMMISSION,'type':'sl','dir':dr2};break
                    else:
                        if l5[j5]<=tp:pnl=(er-tp)*remain;trade_obj={'pnl':pnl-tp*remain*COMMISSION,'type':'tp','dir':dr2};break
                        if h5[j5]>=sl:epr=sl+se;pnl=(er-epr)*remain;trade_obj={'pnl':pnl-epr*remain*COMMISSION,'type':'sl','dir':dr2};break
                if trade_obj is None:
                    lp=c5[-1];pnl=(lp-er)*remain if dr2=='bullish'else(er-lp)*remain
                    trade_obj={'pnl':pnl-lp*remain*COMMISSION,'type':'forced','dir':dr2}

            # Build features
            f={}
            f['fib_low_key']=fb[0];f['fib_high_key']=fb[2];f['fib_width']=(fb[3]-fb[1])/fb[1]if fb[1]!=0 else 0
            f['ci_value']=cv;f['wr_5m']=wr5;f['wr_15m']=wr15
            f['st_aligned']=1 if al else 0;f['st_bias_bullish']=1 if st_bias=='bullish'else 0;f['st_bias_bearish']=1 if st_bias=='bearish'else 0
            f['mom_score']=ms_tot;f['mom_direction']=md
            f['vol_ratio_5m']=float(vr5[i5])
            f['body_ratio_4h']=0.0
            if i4h>=0:
                tr4=d4h['h'][i4h]-d4h['l'][i4h];b4=abs(d4h['c'][i4h]-d4h['o'][i4h])
                f['body_ratio_4h']=b4/tr4 if tr4>0 else 0
            f['hour_of_day']=int(datetime.fromtimestamp(ts/1000,tz=timezone.utc).hour)
            f['direction_long']=1 if dr=='LONG'else 0
            f['phase_compressing']=1 if ph=='compressing'else 0;f['phase_expanding']=1 if ph=='expanding'else 0;f['phase_trending']=1 if ph=='trending'else 0
            f['mom_bullish']=1 if md=='bullish'else 0;f['mom_bearish']=1 if md=='bearish'else 0;f['mom_neutral']=1 if md=='neutral'else 0

            if trade_obj:
                all_tr.append({'symbol':sym,'ts':ts,'features':f,'trade':trade_obj,'direction':dr})
                sym_tr+=1

        print(f"{sym_tr} signals")

    # Apply RF thresholds from cached trades
    _l()
    print(f"\n{'='*60}\n  Results\n{'='*60}")

    # Get RF probabilities
    rf_probs=[]
    for t in all_tr:
        rf_probs.append(proba(t['features']))

    def stats(tr):
        if not tr:return {'trades':0,'wr':0,'pnl':0,'ret':0}
        n=len(tr);w=sum(1 for t in tr if t['trade']['pnl']>0)
        tp=sum(t['trade']['pnl']for t in tr)
        ret=(tp+CAP*len(SYM))/(CAP*len(SYM))-1
        return {'trades':n,'wr':round(w/n*100,1),'pnl':round(tp,2),'ret':round(ret*100,2)}

    def per_sym(tr):
        ps={}
        for s in SYM:
            tt=[t for t in tr if t['symbol']==s]
            if not tt:ps[s]={'trades':0,'wr':0,'pnl':0};continue
            w=sum(1 for t in tt if t['trade']['pnl']>0)
            p=sum(t['trade']['pnl']for t in tt)
            ps[s]={'trades':len(tt),'wr':round(w/len(tt)*100,1),'pnl':round(p,2)}
        return ps

    results={'period':f"{sd.date()}→{ed.date()}","thresholds":{}}

    # Sin RF
    print(f"\n{'─'*60}\n  SIN RF\n{'─'*60}")
    s=stats(all_tr);ps=per_sym(all_tr)
    for s2 in SYM:
        p=ps[s2];print(f"  {s2:8s}: {p['trades']:3d}t WR {p['wr']:5.1f}% PnL ${p['pnl']:+7.2f}")
    print(f"  {'─'*40}")
    print(f"  TOTAL:   {s['trades']:3d}t WR {s['wr']:5.1f}% Ret {s['ret']:+7.2f}% PnL ${s['pnl']:+7.2f}")
    results['thresholds']['sin_rf']={**s,'per_symbol':ps}

    # Thresholds
    for th in [0.3,0.4,0.5,0.55,0.6,0.7]:
        sel=[t for i,t in enumerate(all_tr)if rf_probs[i]>=th]
        n_sel=len(sel);n_rej=len(all_tr)-n_sel
        print(f"\n{'─'*60}\n  TH={th} (aceptó {n_sel}/{n_sel+n_rej})\n{'─'*60}")
        s=stats(sel);ps=per_sym(sel)
        for s2 in SYM:
            p=ps[s2];print(f"  {s2:8s}: {p['trades']:3d}t WR {p['wr']:5.1f}% PnL ${p['pnl']:+7.2f}")
        print(f"  {'─'*40}")
        print(f"  TOTAL:   {s['trades']:3d}t WR {s['wr']:5.1f}% Ret {s['ret']:+7.2f}% PnL ${s['pnl']:+7.2f}")
        results['thresholds'][f'th_{th}']={**s,'accepted':n_sel,'rejected':n_rej,'per_symbol':ps}

    # Walk-forward retrain
    print(f"\n{'='*60}\n  WALK-FORWARD RETRAIN (Q1→Q2)\n{'='*60}")
    ts_split=datetime(2026,4,1,tzinfo=timezone.utc).timestamp()*1000
    train=[t for t in all_tr if t['ts']<ts_split]
    test=[t for t in all_tr if t['ts']>=ts_split]
    train_X=[t['features']for t in train];train_y=[1 if t['trade']['pnl']>0 else 0 for t in train]
    nt=len(train_X);nw=sum(train_y)
    print(f"  Train: {nt} trades ({nw} wins, {nw/nt*100:.1f}%)"if nt else"  No data")
    results['walkforward']=None

    if nt>=50:
        X_df=pd.DataFrame(train_X)
        for c in _mc:
            if c not in X_df.columns:X_df[c]=0
        X_tr=X_df[_mc].fillna(0)
        rf_new=RandomForestClassifier(n_estimators=100,max_depth=5,class_weight='balanced',random_state=42)
        rf_new.fit(X_tr,train_y)
        ta=(rf_new.predict(X_tr)==train_y).mean()
        print(f"  Train acc: {ta*100:.1f}%")
        fi=rf_new.feature_importances_
        top5=np.argsort(fi)[-5:][::-1]
        print(f"  Top features:")
        for j in top5:print(f"    {_mc[j]}: {fi[j]*100:.1f}%")

        sel_test=[]
        for t in test:
            prob=rf_new.predict_proba(pd.DataFrame([t['features']]).fillna(0)[_mc])[0,1]
            if prob>=0.5:sel_test.append(t)

        s=stats(sel_test);s_norf=stats(test)
        ps=per_sym(sel_test)
        print(f"\n  {'─'*40}")
        print(f"  TEST Q2 (th=0.5, RF re-entrenado Q1):")
        for s2 in SYM:
            p=ps[s2];print(f"  {s2:8s}: {p['trades']:3d}t WR {p['wr']:5.1f}% PnL ${p['pnl']:+7.2f}")
        print(f"  {'─'*40}")
        print(f"  TOTAL:   {s['trades']:3d}t WR {s['wr']:5.1f}% Ret {s['ret']:+7.2f}% PnL ${s['pnl']:+7.2f}")
        print(f"  (Sin RF en Q2: {s_norf['trades']}t WR {s_norf['wr']}% Ret {s_norf['ret']}%)")

        results['walkforward']={
            'train':nt,'train_winners':nw,'train_acc':round(float(ta)*100,1),
            'test':s['trades'],'test_wr':s['wr'],'test_ret':s['ret'],'test_pnl':s['pnl'],
            'sin_rf_test':s_norf['trades'],'sin_rf_test_wr':s_norf['wr'],'sin_rf_test_ret':s_norf['ret']
        }

    Path("analisis_completo.json").write_text(json.dumps(results,indent=2))
    print(f"\n{'='*60}\n  ✅ analisis_completo.json\n{'='*60}")

if __name__=="__main__":
    t0=time.time();run();print(f"  ⏱ {time.time()-t0:.0f}s")

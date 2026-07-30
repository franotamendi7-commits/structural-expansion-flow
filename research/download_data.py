#!/usr/bin/env python3
"""
Download historical data from Binance Futures for BTC, ETH, SOL, XRP, BNB
Timeframes: 1h, 4h, 1d
Period: 2022-01-01 to 2026-07-28 (approx 4.5 years)
"""

import os
import time
import pandas as pd
import requests
from datetime import datetime, timedelta
from pathlib import Path
import json

# Configuration
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT']
TIMEFRAMES = ['1h', '4h', '1d']
START_DATE = '2022-01-01'
END_DATE = '2026-07-28'
DATA_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/data')

# Binance Futures API
BASE_URL = 'https://fapi.binance.com/fapi/v1/klines'

# Timeframe to milliseconds mapping
TF_MS = {
    '1h': 3600 * 1000,
    '4h': 4 * 3600 * 1000,
    '1d': 24 * 3600 * 1000,
}

# Max klines per request
MAX_KLINES = 1500

def date_to_ms(date_str):
    """Convert date string to milliseconds timestamp"""
    return int(datetime.strptime(date_str, '%Y-%m-%d').timestamp() * 1000)

def ms_to_date(ms):
    """Convert milliseconds to date string"""
    return datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d %H:%M:%S')

def fetch_klines(symbol, timeframe, start_ms, end_ms):
    """Fetch klines from Binance Futures API"""
    all_klines = []
    current_start = start_ms
    
    while current_start < end_ms:
        params = {
            'symbol': symbol,
            'interval': timeframe,
            'startTime': current_start,
            'endTime': end_ms,
            'limit': MAX_KLINES
        }
        
        try:
            response = requests.get(BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            klines = response.json()
            
            if not klines:
                break
                
            all_klines.extend(klines)
            current_start = klines[-1][0] + TF_MS[timeframe]
            
            # Rate limiting
            time.sleep(0.1)
            
        except Exception as e:
            print(f"Error fetching {symbol} {timeframe}: {e}")
            time.sleep(1)
            continue
    
    return all_klines

def klines_to_dataframe(klines):
    """Convert klines to pandas DataFrame"""
    if not klines:
        return pd.DataFrame()
    
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
    
    # Convert numeric columns
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 
                    'trades', 'taker_buy_base', 'taker_buy_quote']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col])
    
    df.set_index('open_time', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades']]
    
    return df

def save_data(df, symbol, timeframe):
    """Save DataFrame to parquet file"""
    symbol_dir = DATA_DIR / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = symbol_dir / f"{symbol}_{timeframe}.parquet"
    df.to_parquet(filepath, compression='snappy')
    print(f"Saved: {filepath} ({len(df)} rows)")
    return filepath

def load_metadata():
    """Load existing metadata"""
    meta_file = DATA_DIR / 'metadata.json'
    if meta_file.exists():
        with open(meta_file, 'r') as f:
            return json.load(f)
    return {}

def save_metadata(metadata):
    """Save metadata"""
    meta_file = DATA_DIR / 'metadata.json'
    with open(meta_file, 'w') as f:
        json.dump(metadata, f, indent=2, default=str)

def download_symbol(symbol, timeframe, start_ms, end_ms):
    """Download data for a single symbol/timeframe"""
    print(f"\nDownloading {symbol} {timeframe}...")
    
    klines = fetch_klines(symbol, timeframe, start_ms, end_ms)
    
    if not klines:
        print(f"No data for {symbol} {timeframe}")
        return None
    
    df = klines_to_dataframe(klines)
    
    if df.empty:
        print(f"Empty dataframe for {symbol} {timeframe}")
        return None
    
    # Remove duplicates and sort
    df = df[~df.index.duplicated(keep='first')].sort_index()
    
    # Save
    filepath = save_data(df, symbol, timeframe)
    
    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'rows': len(df),
        'start': df.index[0].strftime('%Y-%m-%d %H:%M:%S'),
        'end': df.index[-1].strftime('%Y-%m-%d %H:%M:%S'),
        'file': str(filepath)
    }

def main():
    print("=" * 60)
    print("Downloading Binance Futures Historical Data")
    print("=" * 60)
    print(f"Symbols: {SYMBOLS}")
    print(f"Timeframes: {TIMEFRAMES}")
    print(f"Period: {START_DATE} to {END_DATE}")
    print(f"Output: {DATA_DIR}")
    print("=" * 60)
    
    start_ms = date_to_ms(START_DATE)
    end_ms = date_to_ms(END_DATE)
    
    metadata = load_metadata()
    
    for symbol in SYMBOLS:
        if symbol not in metadata:
            metadata[symbol] = {}
        
        for timeframe in TIMEFRAMES:
            key = f"{symbol}_{timeframe}"
            
            # Check if already downloaded
            if key in metadata.get(symbol, {}):
                print(f"\n{key} already exists, skipping...")
                continue
            
            result = download_symbol(symbol, timeframe, start_ms, end_ms)
            
            if result:
                metadata[symbol][key] = result
                save_metadata(metadata)
    
    print("\n" + "=" * 60)
    print("Download complete!")
    print("=" * 60)
    
    # Print summary
    for symbol in SYMBOLS:
        if symbol in metadata:
            print(f"\n{symbol}:")
            for key, info in metadata[symbol].items():
                print(f"  {info['timeframe']}: {info['rows']} rows ({info['start']} to {info['end']})")

if __name__ == '__main__':
    main()

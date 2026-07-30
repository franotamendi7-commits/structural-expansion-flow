#!/usr/bin/env python3
"""
Download historical data for Forex and Stocks using yfinance
"""

import os
import yfinance as yf
import pandas as pd
from pathlib import Path
import json
import time

# Configuration
FOREX_PAIRS = ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X', 'AUDUSD=X', 'USDCAD=X']
STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'JPM', 'V', 'JNJ']

DATA_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/data')
META_FILE = DATA_DIR / 'metadata.json'

def load_metadata():
    if META_FILE.exists():
        with open(META_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_metadata(metadata):
    with open(META_FILE, 'w') as f:
        json.dump(metadata, f, indent=2, default=str)

def download_forex():
    """Download forex data - 1h timeframe, 2 years"""
    print("=" * 60)
    print("Downloading Forex Data (1h, 2 years)")
    print("=" * 60)
    
    metadata = load_metadata()
    
    for symbol in FOREX_PAIRS:
        print(f"\nDownloading {symbol}...")
        
        try:
            # yfinance: period="2y", interval="1h"
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2y", interval="1h")
            
            if df.empty:
                print(f"  No data for {symbol}")
                continue
            
            # Clean up
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']
            df.index.name = 'open_time'
            
            # Remove duplicates
            df = df[~df.index.duplicated(keep='first')].sort_index()
            
            # Save
            symbol_clean = symbol.replace('=X', '')
            symbol_dir = DATA_DIR / symbol_clean
            symbol_dir.mkdir(parents=True, exist_ok=True)
            
            filepath = symbol_dir / f"{symbol_clean}_1h.parquet"
            df.to_parquet(filepath, compression='snappy')
            
            print(f"  Saved: {filepath} ({len(df)} rows)")
            print(f"  Period: {df.index[0]} to {df.index[-1]}")
            
            if 'forex' not in metadata:
                metadata['forex'] = {}
            metadata['forex'][symbol_clean] = {
                'symbol': symbol,
                'timeframe': '1h',
                'rows': len(df),
                'start': df.index[0].strftime('%Y-%m-%d %H:%M:%S'),
                'end': df.index[-1].strftime('%Y-%m-%d %H:%M:%S'),
                'file': str(filepath)
            }
            save_metadata(metadata)
            
        except Exception as e:
            print(f"  Error downloading {symbol}: {e}")
        
        time.sleep(0.5)  # Rate limiting

def download_stocks():
    """Download stocks data - daily timeframe, 2 years"""
    print("\n" + "=" * 60)
    print("Downloading Stocks Data (Daily, 2 years)")
    print("=" * 60)
    
    metadata = load_metadata()
    
    for symbol in STOCKS:
        print(f"\nDownloading {symbol}...")
        
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2y", interval="1d")
            
            if df.empty:
                print(f"  No data for {symbol}")
                continue
            
            # Clean up
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']
            df.index.name = 'open_time'
            
            # Remove duplicates
            df = df[~df.index.duplicated(keep='first')].sort_index()
            
            # Save
            symbol_dir = DATA_DIR / symbol
            symbol_dir.mkdir(parents=True, exist_ok=True)
            
            filepath = symbol_dir / f"{symbol}_1d.parquet"
            df.to_parquet(filepath, compression='snappy')
            
            print(f"  Saved: {filepath} ({len(df)} rows)")
            print(f"  Period: {df.index[0]} to {df.index[-1]}")
            
            if 'stocks' not in metadata:
                metadata['stocks'] = {}
            metadata['stocks'][symbol] = {
                'symbol': symbol,
                'timeframe': '1d',
                'rows': len(df),
                'start': df.index[0].strftime('%Y-%m-%d %H:%M:%S'),
                'end': df.index[-1].strftime('%Y-%m-%d %H:%M:%S'),
                'file': str(filepath)
            }
            save_metadata(metadata)
            
        except Exception as e:
            print(f"  Error downloading {symbol}: {e}")
        
        time.sleep(0.5)  # Rate limiting

def main():
    download_forex()
    download_stocks()
    
    print("\n" + "=" * 60)
    print("All downloads complete!")
    print("=" * 60)
    
    # Print summary
    metadata = load_metadata()
    for category in ['forex', 'stocks']:
        if category in metadata:
            print(f"\n{category.upper()}:")
            for symbol, info in metadata[category].items():
                print(f"  {symbol}: {info['rows']} rows ({info['start']} to {info['end']})")

if __name__ == '__main__':
    main()

# Dashboard V3 — Institutional Grade

## Overview
Premium dark-mode dashboard for the Structural Expansion Flow trading system. Designed to look like a Bloomberg Terminal / TradingView dark hybrid with glassmorphism effects, neon accents, and smooth animations.

## Features Implemented

### Visual Design
- **Dark Mode**: `#0a0a0f` background with subtle gradient
- **Glassmorphism Cards**: `backdrop-filter: blur(10px)` with semi-transparent borders
- **Neon Accents**: Cyan `#00d4ff` for highlights, Green `#00ff88` for positive, Red `#ff4757` for negative
- **Typography**: Inter for text, JetBrains Mono for numbers/data
- **Animations**: Pulse for live status, fade-in for timeline items, hover effects on cards

### Layout Structure
```
┌─────────────────────────────────────────────────┐
│  HEADER: Logo + Status Bot + Clock              │
├───────────┬───────────┬───────────┬─────────────┤
│  EQUITY   │   PNL     │   DD      │  RISK MODE  │
│  $4,450   │  +$127    │  -2.3%    │   NORMAL    │
├───────────┴───────────┴───────────┴─────────────┤
│              EQUITY CURVE (Plotly)               │
│              + DRAWDOWN (subchart)               │
├──────────────────────┬──────────────────────────┤
│  OPEN POSITIONS      │    RECENT SIGNALS         │
│  Tabla en vivo       │    Timeline scrollable    │
├──────────────────────┴──────────────────────────┤
│  PERFORMANCE METRICS (Sharpe, Sortino, PF, WR)  │
├─────────────────────────────────────────────────┤
│  RECENT TRADES TABLE                            │
├─────────────────────────────────────────────────┤
│  BOT LOG (últimas 15 líneas, auto-scroll)       │
├─────────────────────────────────────────────────┤
│  ACTIVE PARAMETERS                              │
└─────────────────────────────────────────────────┘
```

### Components

#### 1. Header
- Logo with neon glow effect
- Bot status indicator (green pulsing dot = running, red = stopped)
- UTC clock
- Connection status badge

#### 2. Metric Cards (4-column grid)
- **Equity**: Total balance + last trade PnL
- **Unrealized PnL**: Open positions PnL + count
- **Drawdown**: Current DD% + progress bar (green/orange/red)
- **Risk Mode**: NORMAL/CAUTION/REDUCED/PAUSED with color coding

#### 3. Equity Curve
- Plotly chart with dual subplots
- Top: Equity line with gradient fill
- Bottom: Drawdown area chart
- Dark theme matching dashboard

#### 4. Open Positions
- Real-time table from Binance Testnet API
- Columns: Pair, Side, Size, Entry, Mark, PnL, Leverage
- Color-coded badges (LONG=green, SHORT=red)
- Empty state with icon when no positions

#### 5. Recent Signals
- Vertical timeline with colored dots
- Each entry: Pair, Direction, Type, Entry price, Score
- Timestamps with date/time
- Scrollable container (max 10 visible)

#### 6. Performance Metrics
- 4-card grid: Sharpe/Sortino, PF/Win Rate, Trade Stats, Extremes
- Color-coded values (green for good, red for bad)
- Target benchmarks displayed

#### 7. Recent Trades
- Table with: Time, Bot, Side, Entry, Exit, PnL, Reason, Hold time
- PnL color coding
- Reason badges

#### 8. Bot Log
- Terminal-style display
- Syntax highlighting (INFO=grey, WARNING=yellow, ERROR=red, SUCCESS=green)
- Auto-scroll to newest
- Monospace font

#### 9. Active Parameters
- List of current trading parameters
- Key-value pairs with cyan values

### Technical Features

#### Auto-Refresh
- `streamlit-autorefresh` every 30 seconds
- Real-time data updates

#### Data Sources
- **Balance**: Binance Futures Testnet API
- **Positions**: Binance Futures Testnet API
- **Signals**: `signal_log.json`
- **Trades**: `trade_history.json`
- **Equity**: `equity_history.json`
- **Logs**: `logs/paper_trading.log`
- **Drawdown**: `drawdown_state.json`
- **Parameters**: `parameter_state.json`

#### Performance Calculations
- Sharpe Ratio (annualized)
- Sortino Ratio (downside deviation)
- Profit Factor
- Win Rate
- Average trade duration
- Best/Worst trade
- Expectancy

#### Error Handling
- Graceful fallback for missing files
- Default values for all state
- No crashes on API failures

#### Responsive Design
- 4 columns → 2 columns → 1 column (mobile)
- Header stacks vertically on mobile
- Touch-friendly on tablets

### CSS Features (24/24)
✅ set_page_config (wide layout)
✅ Google Fonts (Inter + JetBrains Mono)
✅ Glassmorphism (backdrop-blur)
✅ Dark theme (#0a0a0f)
✅ Cyan accent (#00d4ff)
✅ Green accent (#00ff88)
✅ Red accent (#ff4757)
✅ Pulse animation
✅ Fade-in animation
✅ Badge system (LONG/SHORT/WAIT/OK/WARN/STOP)
✅ Metric cards
✅ Section headers
✅ Log container
✅ Timeline
✅ Progress bar
✅ Responsive grid
✅ Custom tables
✅ Hover effects
✅ Scrollbar styling
✅ Empty states
✅ Tooltips
✅ Neon text glow
✅ Gradient fills

## File Information
- **Path**: `/Users/franciscootamendi/ai-agents-v3/app.py`
- **Size**: 47,846 bytes (46.7 KB)
- **Lines**: 1,520
- **Functions**: 16
- **CSS Lines**: ~800

## Dependencies
- streamlit >= 1.39.0
- pandas >= 1.5
- numpy >= 1.24
- plotly >= 5.0
- requests >= 2.28
- streamlit-autorefresh >= 0.3

## Running
```bash
cd /Users/franciscootamendi/ai-agents-v3
streamlit run app.py
```

Dashboard available at: `http://localhost:8501`

## Backup
Original file backed up as: `app.py.backup_YYYYMMDD_HHMMSS`

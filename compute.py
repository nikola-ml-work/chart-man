
from datetime import timedelta
from collections import deque
from scipy import stats
from constant import *
from tqdm import tqdm
from yahoo import *
from data import *
from config import *
from util import *
import pandas_ta as ta
import pandas as pd
import numpy as np

"""
Core logic modules
"""

# Get local peak points using Zig-Zag algorithm
def get_zigzag(df, final_date):
	pivots = []

	series = df['Close']
	init_date = df.index[0]
	
	win_dur = timedelta(days = zigzag_window)
	pad_dur = timedelta(days = zigzag_padding)

	win_end_date = final_date - pad_dur
	win_start_date = win_end_date - win_dur

	while win_start_date >= init_date:
		if len(series[win_start_date:win_end_date]) > 1:
			max_idx = series[win_start_date:win_end_date].idxmax()
			min_idx = series[win_start_date:win_end_date].idxmin()

			if max_idx < min_idx:
				if len(pivots) > 0:
					if pivots[-1][1] > 0:
						pivots.append((min_idx, -1, series[min_idx]))
						pivots.append((max_idx, 1, series[max_idx]))
					elif pivots[-1][2] < series[min_idx]:
						pivots.append((max_idx, 1, series[max_idx]))
					else:
						pivots[-1] = (min_idx, -1, series[min_idx])
						pivots.append((max_idx, 1, series[max_idx]))
				else:
					pivots.append((min_idx, -1, series[min_idx]))
					pivots.append((max_idx, 1, series[max_idx]))
			else:
				if len(pivots) > 0:
					if pivots[-1][1] < 0:
						pivots.append((max_idx, 1, series[max_idx]))
						pivots.append((min_idx, -1, series[min_idx]))
					elif pivots[-1][2] > series[max_idx]:
						pivots.append((min_idx, -1, series[min_idx]))
					else:
						pivots[-1] = (max_idx, 1, series[max_idx])
						pivots.append((min_idx, -1, series[min_idx]))
				else:
					pivots.append((max_idx, 1, series[max_idx]))
					pivots.append((min_idx, -1, series[min_idx]))

		win_end_date -= win_dur
		win_start_date -= win_dur

	pivots = pivots[::-1]

	for _ in range(zigzag_merges):
		merged_pivots = merge_zigzag_pivots(pivots)		
		if len(merged_pivots) < 4: break

		pivots = merged_pivots

	res = pd.DataFrame(columns = ['Date', 'Sign', 'Close'])
	
	for idx, sign, v in pivots:
		r = {'Date': idx, 'Sign': sign, 'Close': v}
		res = pd.concat([res, pd.Series(r).to_frame().T], ignore_index = True)

	res.set_index('Date', inplace = True)
	return res

# Refine peak points by merging Zig-Zag peaks
def merge_zigzag_pivots(pivots):
	if len(pivots) < 3: return pivots	
	res, i = [], 0

	while i < len(pivots) - 3:
		res.append(pivots[i])

		if pivots[i + 3][0] - pivots[i][0] < timedelta(days = zigzag_merge_dur_limit):
			v = [pivots[j][2] for j in range(i, i + 4)]

			if min(v[0], v[3]) < min(v[1], v[2]) and max(v[0], v[3]) > max(v[1], v[2]):
				if zigzag_merge_val_limit * (max(v[0], v[3]) - min(v[0], v[3])) > (max(v[1], v[2]) - min(v[1], v[2])):
					i += 3
				else:
					i += 1
			else:
				i += 1
		else:
			i += 1

	for j in range(i, len(pivots)):
		res.append(pivots[j])

	return res

# Get recent downfall pivot pairs from Zig-Zag peak points
def get_recent_downfalls(zdf, count):
	res = []

	for i in range(len(zdf) - 1, 1, -1):
		row, prev = zdf.iloc[i], zdf.iloc[i - 1]		

		if row['Sign'] > 0: continue
		
		hv, zv = prev['Close'], row['Close']

		if (hv - zv) < hv * fibo_pivot_diff_limit: continue

		res.append((prev.name, row.name))

		if len(res) == count: break

	return res[::-1]

# Get Fibonacci extension levels from a given set of downfall pivot pairs
def get_fib_extensions(zdf, downfalls, merge_thres, suppress_level):
	all_levels = []

	for i, f in enumerate(downfalls):
		hd, zd = f
		hv, zv = zdf.loc[hd]['Close'], zdf.loc[zd]['Close']
		dv = hv - zv

		for j, l in enumerate(FIB_EXT_LEVELS):
			lv = zv + dv * l
			if lv > suppress_level: break

			all_levels.append((i, hd, zd, hv, zv, j, round(lv, 4)))

	all_levels.sort(key = lambda x: x[-1])
	res, flags = [], []

	for i, level in enumerate(all_levels):
		if i in flags: continue

		lv = level[-1]
		th = lv * merge_thres

		flags.append(i)		
		g = [level]

		for j in range(i + 1, len(all_levels)):
			if j in flags: continue
			v = all_levels[j][-1]

			if v - lv <= th:
				flags.append(j)
				g.append(all_levels[j])

				lv = v

		res.append(g)

	return res

# Compute behaviors of Fibonacci extension levels
def get_fib_ext_behaviors(df, extensions, cur_date, merge_thres):
	res = {}
	cur_price = df.loc[cur_date]['Close']

	for g in extensions:
		lv = (g[0][-1] + g[-1][-1]) / 2
		is_resist = (lv >= cur_price)

		behavior, pv, start_date = None, None, None

		for d in df.loc[cur_date:].iloc:
			v = d.High if is_resist else d.Low

			if pv is not None:
				if (pv < lv and v >= lv) or (pv > lv and v <= lv):
					start_date = d.name
					break

			pv = d.Low if is_resist else d.High

		if start_date is not None:
			milestone_forward = FIB_BEHAVIOR_MILESTONE
			milestone_date = None

			while milestone_forward >= 5 and milestone_date is None:
				milestone_date = get_nearest_forward_date(df, start_date + timedelta(days = milestone_forward))
				milestone_forward //= 2

			if milestone_date is not None:
				mlv = df.loc[milestone_date]['Close']
				thres = lv * merge_thres

				has_mid_up, has_mid_down = False, False

				for d in df.loc[df.loc[start_date:milestone_date].index[1:-1]].iloc:
					if (d.Close - lv) >= thres:
						has_mid_up = True
					elif (lv - d.Close) >= thres:
						has_mid_down = True

				if (mlv - lv) >= thres:
					if has_mid_down:
						behavior = 'Res_Semi_Break' if is_resist else 'Sup_Semi_Sup'
					else:
						behavior = 'Res_Break' if is_resist else 'Sup_Sup'
				elif (lv - mlv) >= thres:
					if has_mid_up:
						behavior = 'Res_Semi_Res' if is_resist else 'Sup_Semi_Break'
					else:
						behavior = 'Res_Res' if is_resist else 'Sup_Break'
				elif has_mid_up == has_mid_down:
					end_date = get_nearest_forward_date(df, milestone_date + timedelta(days = milestone_forward))

					if end_date is not None:
						elv = df.loc[end_date]['Close']

						if (elv - lv) >= thres:
							behavior = 'Res_Semi_Break' if is_resist else 'Sup_Semi_Sup'
						elif (lv - elv) >= thres:
							behavior = 'Res_Semi_Res' if is_resist else 'Sup_Semi_Break'
						else:
							behavior = 'Vibration'
					else:
						behavior = 'Vibration'
				elif has_mid_up:
					behavior = 'Res_Break' if is_resist else 'Sup_Sup'
				else:
					behavior = 'Res_Res' if is_resist else 'Sup_Break'

		res[g[0]] = behavior

	return res

# Generate table-format data for Fibonacci extension analysis
def analyze_fib_extension(df, extensions, behaviors, cur_date, pivot_number, merge_thres, interval, symbol):
	cols = ['ExtID', 'Level', 'Type', 'Width', 'Behavior', 'Description', ' ']
	res = pd.DataFrame(columns = cols)
	
	cur_price = df.loc[cur_date]['Close']
	i = 0

	for g in extensions:
		lv = (g[0][-1] + g[-1][-1]) / 2
		b = behaviors[g[0]]
		i += 1

		record = [
			i,
			'{:.4f}$'.format(lv),
			'Resistance' if lv >= cur_price else 'Support',
			'{:.2f}%'.format(100 * (g[-1][-1] - g[0][-1]) / g[0][-1]) if len(g) > 1 else '',
			FIB_EXT_MARKERS[b][-1] if b is not None else '',
			' & '.join(['{:.1f}% of {:.4f}-{:.4f}'.format(FIB_EXT_LEVELS[j] * 100, zv, hv) for _, _, _, hv, zv, j, _ in g]),
			''
		]
		res = pd.concat([res, pd.Series(dict(zip(cols, record))).to_frame().T], ignore_index = True)

	res = pd.concat([res, pd.Series({}).to_frame().T], ignore_index = True)
	
	res = pd.concat([res, pd.Series({
		'ExtID': 'Ticker:',
		'Level': symbol,
		'Type': 'Current Date:',
		'Width': cur_date.strftime('%d %b %Y'),
		'Behavior': 'Current Price:',
		'Description': '{:.4f}$'.format(cur_price)
	}).to_frame().T], ignore_index = True)

	res = pd.concat([res, pd.Series({
		'Level': 'From: {}'.format(df.index[0].strftime('%d %b %Y')),
		'Type': 'To: {}'.format(df.index[-1].strftime('%d %b %Y')),
		'Width': 'By: ' + interval,
		'Behavior': 'Merge: {:.1f}%'.format(merge_thres * 100),
		'Description': 'Recent Pivots: {}'.format(pivot_number)
	}).to_frame().T], ignore_index = True)

	res = pd.concat([res, pd.Series({}).to_frame().T], ignore_index = True)
	return res

# Backtest using Fibonacci extension strategy
#
# (Logic)
# For each date point, get recent downfall pivot pairs (Hundred-Zero pairs)
# Calculate Fibonacci extension levels
# If the current date point crosses an extension level, it's time to send signal.
# If it falls to cross, a Short signal is raised.
# If it rises to cross, a Long signal is raised.
# With Short signal, either put Short position or seal ongoing Long position.
# With Long signal, either put Long position or seal ongoing Short position.
#
# (Return)
# Transaction records, position accuracy rate and cumulated profit on percentage basis
def backtest_fib_extension(df, interval, pivot_number, merge_thres, symbol):
	cols = ['TransID', 'Position', 'EnterDate', 'EnterPrice', 'ExitDate', 'ExitPrice', 'Offset', 'Profit', 'CumProfit', 'X', ' ']

	enter_date, position = None, None
	trans_count, match_count, cum_profit = 0, 0, 0

	signs = deque(maxlen = 4 if interval == INTERVAL_DAILY else 1)
	res = pd.DataFrame(columns = cols)

	for cur_date in tqdm(list(df.index), desc = 'backtesting', colour = 'red'):
		cur_candle = df.loc[cur_date]
		signs.append(np.sign(cur_candle['Close'] - cur_candle['Open']))

		if enter_date is not None and (cur_date - enter_date).days < MIN_FIB_EXT_TRANS_DUR: continue

		if signs.count(1) == len(signs):
			cur_sign = 1
		elif signs.count(-1) == len(signs):
			cur_sign = -1
		else:
			cur_sign = 0

		if cur_sign == 0: continue
		if position == cur_sign: continue

		min_cur_price = min(cur_candle['Close'], cur_candle['Open'])
		max_cur_price = max(cur_candle['Close'], cur_candle['Open'])

		zdf = get_zigzag(df, cur_date)
		downfalls = get_recent_downfalls(zdf, pivot_number)
		extensions = get_fib_extensions(zdf, downfalls, get_safe_num(merge_thres), cur_candle['Close'] * 2)

		has_signal = False

		for g in extensions:
			lv = (g[0][-1] + g[-1][-1]) / 2

			if min_cur_price <= lv and lv <= max_cur_price:
				has_signal = True
				break

		if position is None:
			position = cur_sign
			enter_date = cur_date
		else:
			price_offset = cur_candle['Close'] - df.loc[enter_date]['Close']
			true_sign = np.sign(price_offset)
			trans_count += 1

			if true_sign == position: match_count += 1

			profit = position * price_offset / df.loc[enter_date]['Close']
			cum_profit += profit			

			record = [
				trans_count,
				'Long' if position > 0 else 'Short',
				enter_date.strftime('%d %b %Y'),
				'{:.4f}$'.format(df.loc[enter_date]['Close']),
				cur_date.strftime('%d %b %Y'),
				'{:.4f}$'.format(cur_candle['Close']),
				'{:.2f}%'.format(100 * price_offset / df.loc[enter_date]['Close']),
				'{:.4f}%'.format(100 * profit),
				'{:.4f}%'.format(100 * cum_profit),
				'T' if true_sign == position else 'F',
				' '
			]
			res = pd.concat([res, pd.Series(dict(zip(cols, record))).to_frame().T], ignore_index = True)
			enter_date, position = None, None

	success_rate = (match_count / trans_count) if trans_count != 0 else 0
	res = pd.concat([res, pd.Series({}).to_frame().T], ignore_index = True)
	
	res = pd.concat([res, pd.Series({
		'TransID': 'Ticker:',
		'Position': symbol,
		'EnterDate': 'From: {}'.format(df.index[0].strftime('%d %b %Y')),
		'EnterPrice': 'To: {}'.format(df.index[-1].strftime('%d %b %Y')),
		'ExitDate': 'By: ' + interval,
		'ExitPrice': 'Recent Pivots: {}'.format(pivot_number),
		'Offset': 'Merge: {:.1f}%'.format(merge_thres * 100)
	}).to_frame().T], ignore_index = True)

	res = pd.concat([res, pd.Series({
		'EnterDate': 'Success Rate:',
		'EnterPrice': '{:.1f}%'.format(success_rate * 100),
		'ExitDate': 'Cumulative Profit:',
		'ExitPrice': '{:.1f}%'.format(cum_profit * 100)
	}).to_frame().T], ignore_index = True)

	res = pd.concat([res, pd.Series({}).to_frame().T], ignore_index = True)
	return res, success_rate, cum_profit

# Get information for dashboard
def get_dashboard_info():
	cols = ['Symbol', 'State', 'Current Price', 'New Highest']
	res = pd.DataFrame(columns = cols)

	for symbol in tqdm(load_stock_symbols(), desc = 'loading', colour = 'green'):
		df = load_yf(symbol, '1800-01-01', '2100-01-01', INTERVAL_DAILY)

		highs = df['High'].to_numpy()		
		last_date = df.index[-1]

		is_new_highest = (highs.argmax() == len(highs) - 1)
		is_bullish = df.loc[last_date]['Close'] >= df.loc[last_date]['Open']

		record = [
			symbol,
			'↑ Bullish' if is_bullish else '↓ Bearish',
			'{:.4f}$'.format(df.loc[last_date]['Close']),
			'√ {:.4f}'.format(highs[-1]) if is_new_highest else ''
		]
		res = pd.concat([res, pd.Series(dict(zip(cols, record))).to_frame().T], ignore_index = True)

	res = pd.concat([res, pd.Series({}).to_frame().T], ignore_index = True)
	return res, last_date

def is_pivot(candle, window, df):
	if candle - window < 0 or candle + window >= len(df): return 0
	
	pivot_high = 1
	pivot_low = 2
	
	for i in range(candle - window, candle + window + 1):
		if df.iloc[candle].Low > df.iloc[i].Low: pivot_low = 0
		if df.iloc[candle].High < df.iloc[i].High: pivot_high = 0
	
	if pivot_high and pivot_low:
		return 3
	elif pivot_high:
		return pivot_high
	elif pivot_low:
		return pivot_low
	else:
		return 0

def calculate_point_pos(row):
	if row['isPivot'] == 2:
		return row['Low'] - 1e-3
	elif row['isPivot'] == 1:
		return row['High'] + 1e-3
	else:
		return np.nan

def backtest_trendline(df, interval, symbol):
	combined_trades = pd.DataFrame()
	
	df['ID'] = range(len(df))
	df['Date'] = list(df.index)
	df.set_index('ID', inplace = True)
 
	atr = ta.atr(high = df['High'], low = df['Low'], close = df['Close'], length = 14)
	atr_multiplier = 2 
	stop_percentage = atr.iloc[-1] * atr_multiplier / df['Close'].iloc[-1]
        
	for level in range(2, 11, 2):
		window = 3 * level
		backcandles = 10 * window
		
		df['isPivot'] = df.apply(lambda row: is_pivot(row.name, window, df), axis = 1)
		df['isBreakOut'] = 0
  
		for i in range(backcandles + window, len(df)):
			df.loc[i, 'isBreakOut'] = is_breakout(i, backcandles, window, df, stop_percentage)

		trades_data = unit_trendline_backtest(df, level)
		combined_trades = pd.concat([combined_trades, trades_data])

	combined_trades = combined_trades.sort_values(by = 'Enter Date')
	combined_trades = combined_trades.drop_duplicates(subset = ['Enter Date', 'Exit Date'], keep = 'first')

	total_trades = len(combined_trades)
	profitable_trades = len(combined_trades[combined_trades['Profit/Loss'] > 0])
	success_rate = (profitable_trades / total_trades) * 100

	valid_trades = combined_trades.dropna(subset = ['Return']).copy()
	valid_trades['Cumulative Return'] = (1 + valid_trades['Return'] / 100).cumprod()

	overall_return = (valid_trades['Cumulative Return'].iloc[-1] - 1) * 100
	
	combined_trades = combined_trades.drop('Profit/Loss', axis = 1)
	combined_trades = combined_trades.round(4)

	return combined_trades, success_rate, overall_return

def collect_channel(candle, backcandles, window, df):
	best_r_squared_low = 0
	best_r_squared_high = 0
	best_slope_low = 0
	best_intercept_low = 0
	best_slope_high = 0
	best_intercept_high = 0
	best_backcandles_low = 0
	best_backcandles_high = 0
	
	for i in range(backcandles - backcandles // 2, backcandles + backcandles // 2, window):
		local_df = df.iloc[candle - i - window: candle - window]
		
		lows = local_df[local_df['isPivot'] == 2].Low.values[-4:]
		idx_lows = local_df[local_df['isPivot'] == 2].Low.index[-4:]
		highs = local_df[local_df['isPivot'] == 1].High.values[-4:]
		idx_highs = local_df[local_df['isPivot'] == 1].High.index[-4:]

		if len(lows) >= 2:
			slope_low, intercept_low, r_value_l, _, _ = stats.linregress(idx_lows, lows)
			
			if (r_value_l ** 2) * len(lows) > best_r_squared_low and (r_value_l ** 2) > 0.85:
				best_r_squared_low = (r_value_l ** 2)*len(lows)
				best_slope_low = slope_low
				best_intercept_low = intercept_low
				best_backcandles_low = i
		
		if len(highs) >= 2:
			slope_high, intercept_high, r_value_h, _, _ = stats.linregress(idx_highs, highs)
			
			if (r_value_h ** 2)*len(highs) > best_r_squared_high and (r_value_h ** 2)> 0.85:
				best_r_squared_high = (r_value_h ** 2)*len(highs)
				best_slope_high = slope_high
				best_intercept_high = intercept_high
				best_backcandles_high = i
	
	return best_backcandles_low, best_slope_low, best_intercept_low, best_r_squared_low, best_backcandles_high, best_slope_high, best_intercept_high, best_r_squared_high

def is_breakout(candle, backcandles, window, df, stop_percentage):
	if 'isBreakOut' not in df.columns: return 0

	for i in range(1, 2):
		if df['isBreakOut'].iloc[candle - i] != 0: return 0
  
	if candle - backcandles - window < 0: return 0
	best_back_l, sl_lows, interc_lows, r_sq_l, best_back_h, sl_highs, interc_highs, r_sq_h = collect_channel(candle, backcandles, window, df)
	
	thirdback = candle - 2
	thirdback_low = df.iloc[thirdback].Low
	thirdback_high = df.iloc[thirdback].High
	thirdback_volume = df.iloc[thirdback].Volume

	prev_idx = candle - 1
	prev_high = df.iloc[prev_idx].High
	prev_low = df.iloc[prev_idx].Low
	prev_close = df.iloc[prev_idx].Close
	prev_open = df.iloc[prev_idx].Open
	
	curr_idx = candle
	curr_high = df.iloc[curr_idx].High
	curr_low = df.iloc[curr_idx].Low
	curr_close = df.iloc[curr_idx].Close
	curr_open = df.iloc[curr_idx].Open
	curr_volume= max(df.iloc[candle].Volume, df.iloc[candle-1].Volume)
	breakpclow = (sl_lows * prev_idx + interc_lows  - curr_low) / curr_open
	breakpchigh = (curr_high - sl_highs * prev_idx - interc_highs) / curr_open

	if ( 
		thirdback_high > sl_lows * thirdback + interc_lows and
		curr_volume >thirdback_volume and
		prev_close < prev_open and
		curr_close < curr_open and
		sl_lows > 0 and
		prev_close < sl_lows * prev_idx + interc_lows and
		curr_close < sl_lows * prev_idx + interc_lows):
		return 1
	elif (
		thirdback_low < sl_highs * thirdback + interc_highs and
		curr_volume > thirdback_volume and
		prev_close > prev_open and 
		curr_close > curr_open and
		sl_highs < 0 and
		prev_close > sl_highs * prev_idx + interc_highs and
		curr_close > sl_highs * prev_idx + interc_highs):
		return 2
	else:
		return 0

def calculate_breakpoint_pos(row):
	if row['isBreakOut'] == 2:
		return row['Low'] - 3e-3
	elif row['isBreakOut'] == 1:
		return row['High'] + 3e-3
	else:
		return np.nan

def unit_trendline_backtest(df, level):
    trades = []

    for i in range(1, len(df)):
        signal_type = df['isBreakOut'].iloc[i]
        signal = ""
        
        if signal_type == 2:
            signal = "Long"
            entry_date = df['Date'].iloc[i].strftime(YMD_FORMAT)
            entry_price = df['Close'].iloc[i]
            exit_price = None

            for j in range(i + 1, len(df)):
                if df['isPivot'].iloc[j] != 0:
                    exit_date = df['Date'].iloc[j].strftime(YMD_FORMAT)
                    exit_price = df['Close'].iloc[j]
                    break

            if exit_price is None:
                exit_date = df['Date'].iloc[-1].strftime(YMD_FORMAT)
                exit_price = df['Close'].iloc[-1]

            profit_or_stopped = calculate_profit_or_stopped(entry_price, exit_price, signal_type)
            trades.append((entry_date, entry_price, exit_date, exit_price, profit_or_stopped,signal,level))
        elif signal_type == 1:
              signal = "Short"
              entry_date = df['Date'].iloc[i].strftime(YMD_FORMAT)
              entry_price = df['Close'].iloc[i]
              exit_price = None
              
              for j in range(i + 1, len(df)):
                if df['isPivot'].iloc[j] != 0:
                    exit_date = df['Date'].iloc[j].strftime(YMD_FORMAT)
                    exit_price = df['Close'].iloc[j]
                    break

              if exit_price is None:
                    exit_date = df['Date'].iloc[-1].strftime(YMD_FORMAT)
                    exit_price = df['Close'].iloc[-1]

              profit_or_stopped = calculate_profit_or_stopped(entry_price, exit_price, signal_type)
              trades.append((entry_date, entry_price, exit_date, exit_price, profit_or_stopped, signal, level))

    trade_data = pd.DataFrame(trades, columns = ['Enter Date', 'Enter Price', 'Exit Date', 'Exit Price', 'Profit/Loss', 'Signal', 'Level'])
    trade_data['Return'] = trade_data['Profit/Loss'] * abs(trade_data['Enter Price'] - trade_data['Exit Price'] ) / trade_data['Enter Price']

    return trade_data

def calculate_profit_or_stopped(entry_price, exit_price, long_or_short):
  if long_or_short == 2:
    if exit_price >= entry_price :
        return 1
    else:
        return -1
  elif long_or_short == 1:
    if exit_price <= entry_price :
        return 1
    else:
        return -1

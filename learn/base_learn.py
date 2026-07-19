#基础
# 视频学习地址：
# https://www.douyin.com/jingxuan/search/%E3%80%90%E9%87%8F%E5%8C%96%E5%85%A5%E9%97%A801%E3%80%91%E9%87%8F%E5%8C%96%E4%BA%A4%E6%98%93%E5%AE%8C%E6%95%B4%E5%B7%A5%E4%BD%9C%E6%A1%86%E6%9E%B6%EF%BC%9A%E4%BB%8E%E6%95%B0%E6%8D%AE%E5%88%B0%E5%AE%9E%E7%9B%98%E7%9A%84%E4%BA%94%E4%B8%AA%E7%8E%AF%E8%8A%82?aid=f5bdf288-b99a-4022-881c-241a1eb514d9&modal_id=7627189422605274406&type=general
# https://www.douyin.com/jingxuan/search/%E3%80%90%E9%87%8F%E5%8C%96%E5%85%A5%E9%97%A802%E3%80%91%20%E9%87%8F%E5%8C%96%E5%9B%9E%E6%B5%8B%E4%B8%89%E5%A4%A7%E9%99%B7%E9%98%B1?aid=2aa0ff2b-2468-4b88-a10a-c76057678a86&modal_id=7632715987900681522&type=general
# https://www.douyin.com/jingxuan/search/%E3%80%90%E9%87%8F%E5%8C%96%E5%85%A5%E9%97%A803%E3%80%91%E6%89%8B%E6%92%B88%E4%B8%AA%E7%BB%8F%E5%85%B8%E6%8A%80%E6%9C%AF%E5%9B%A0%E5%AD%90?aid=f5ed5bcf-5dd7-4b14-9a47-c8049edff3c4&modal_id=7632835025167322419&type=general

import data
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import warnings
import os
import seaborn as sns
warnings.filterwarnings('ignore')

#设置中文显示和全局绘图风格
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.figsize'] = (12, 6)
plt.rcParams['figure.dpi'] = 100
print("环境准备完成!")
print(f"NumPy版本:{np.__version__}")
print(f"Pandas版本:{pd.__version__}")

print("=" * 60)
###################################################################



def display(data):
    """美化打印DataFrame或其他数据"""
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', None)
    pd.set_option('display.width', None)
    if isinstance(data, pd.DataFrame):
        print(data.to_string())
    else:
        print(data)

#尝试使用yfinance获取真实数据,失败时使用合成数据
def get_stock_data(tickers, start='2020-01-01', end='2024-12-31', use_cache=True):
    """获取股票数据,支持本地缓存和yfinance回退

    Args:
        tickers: 股票代码或列表
        start: 开始日期
        end: 结束日期
        use_cache: 是否使用本地缓存,默认True

    Returns:
        DataFrame: 股票收盘价数据
    """
    cache_dir = '.stock_cache'
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)

    # 生成缓存键
    ticker_str = str(tickers).replace(" ", "").replace("'", "")
    cache_file = os.path.join(cache_dir, f"{ticker_str}_{start}_{end}.csv")

    # 先从缓存读
    if use_cache and os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            print(f"从缓存读取数据: {cache_file}")
            return data
        except Exception as e:
            print(f"缓存读取失败({e}),重新获取")

    # 缓存未命中,调接口获取数据
    try:
        import yfinance as yf
        data = yf.download(tickers, start=start, end=end)['Close']
        if isinstance(data, pd.Series):
            data = data.to_frame(name=tickers if isinstance(tickers, str) else tickers[0])
        print(f"成功从yfinance获取数据,共{len(data)}个交易日")

        # 写入缓存
        data.to_csv(cache_file)
        print(f"数据已缓存到: {cache_file}")

        return data
    except Exception as e:
        print(f"yfinance不可用({e}),使用合成数据")
        return None

#获取数据
#SPY=SPDRS&P500ETF(追踪标普500指数,代表美国大盘股整体表现)
#QQQ=InvescoQQTrust(追踪纳斯达克100指数,以科技股为主)
# IWM = iShares Russell 2000 ETF (追踪罗素2000指数,代表美国小盘股)
#这三个ETF(交易所交易基金)是美国市场最常用的宽基指数基金
tickers = ['SPY', 'QQQ', 'IWM']
prices = get_stock_data(tickers, start='2020-01-01', end='2024-12-31')
print("\n数据预览(前10条):")
print(prices.head(10))

#计算日收益率
daily_returns = prices.pct_change().dropna()
#计算对数收益率
log_returns = np.log(prices / prices.shift(1)).dropna()
#计算累积收益
cumulative_returns = (1 + daily_returns).cumprod() - 1
#打印基本统计信息
print("=" * 60)
print("日收益率统计")
print("=" * 60)
stats = pd.DataFrame({
    '日均收益率':daily_returns.mean(),
    '日波动率':daily_returns.std(),
    '年化收益率':daily_returns.mean()*252,
    '年化波动率':daily_returns.std()*np.sqrt(252),
    '偏度':daily_returns.skew(), # 含义：分布是否均匀，值越接近0，则分布越均匀。正值说明有极端正值，负值说明有极端负值。
    '峰度':daily_returns.kurtosis() # 含义：分布的形状，值越接近0，则分布越集中。
})
# .round(4)会将每个数值保留 4位小数，比如 0.1234567会变成 0.1235。
display(stats.round(4))

print("=" * 60)
###################################################################



#可视化:价格走势和累积收益
fig,axes=plt.subplots(2, 2, figsize=(14,10))
#1.价格走势
ax=axes [0, 0]
for col in prices.columns:
    normalized = prices [col] / prices[col].iloc[0] * 100
    ax.plot(normalized, label=col, linewidth=1.2)
ax.set_title('归一化价格走势(基期=100)',fontsize=13)
ax.legend()
ax.grid(alpha=0.3)
#2.累积收益
ax = axes [0, 1]
for col in cumulative_returns.columns:
    ax.plot(cumulative_returns[col] * 100, label=col,linewidth=1.2)
ax.set_title('累积收益率(%)',fontsize=13)
ax.legend()
ax.grid(alpha=0.3)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_:f'{x:.0f}%'))
#3.日收益率分布
ax = axes [1, 0]
for col in daily_returns.columns:
    ax.hist(daily_returns[col], bins=80, alpha=0.5, label=col,density=True)
ax.set_title('日收益率分布',fontsize=13)
ax.legend()
ax.grid(alpha=0.3)
#4.滚动波动率
ax = axes [1, 1]
rolling_vol = daily_returns.rolling(20).std() * np.sqrt(252)
for col in rolling_vol.columns:
    ax.plot(rolling_vol[col], label=col, linewidth=1)
ax.set_title('20日滚动年化波动率',fontsize=13)
ax.legend()
ax.grid(alpha=0.3)
plt.tight_layout()
# plt.show()  # 如有要展示图，就打开注释

print("=" * 60)
###################################################################




def rolling_sharpe(returns, window=252, rf_annual=0.02):
    """计算滚动夏普比率
    Parameters:
    returns:日收益率序列
    window:滚动窗口(交易日数)
    rf_annual:年化无风险利率
    Returns:
    滚动年化夏普比率序列
    """""
    rf_daily = rf_annual / 252 # 计算日无风险利率
    excess_returns = returns - rf_daily # 计算超额收益
    rolling_mean = excess_returns.rolling(window).mean()
    rolling_std = excess_returns.rolling(window).std()
    return (rolling_mean / rolling_std) * np.sqrt(252)

def max_drawdown(prices_series):
    """计算最大回撤序列
    Parameters:
    prices_series:价格序列
    Returns:
    回撤序列(负值表示回撤幅度)
    """""
    cummax = prices_series.cummax()
    drawdown = (prices_series - cummax) / cummax
    return drawdown

def max_drawdown_value(prices_series):
    """计算最大回撤数值"""
    dd = max_drawdown(prices_series)
    return dd.min()

#计算滚动夏普
sharpe_df = pd.DataFrame()
for col in daily_returns.columns:
    sharpe_df[col] = rolling_sharpe(daily_returns[col], window=252)
#计算回撤
dd_df= pd.DataFrame()
for col in prices.columns:
    dd_df[col] = max_drawdown(prices[col])
#可视化
fig, axes = plt.subplots(2, 1, figsize=(14, 8))

#滚动夏普
ax = axes [0]
for col in sharpe_df.columns:
    ax.plot(sharpe_df[col], label=col, linewidth=1.2)
ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
ax.axhline(y=1, color='green',linestyle='--', linewidth=0.8, alpha=0.5,label='Sharpe=1')
ax.axhline(y=-1, color='red',linestyle='--', linewidth=0.8, alpha=0.5,label='Sharpe=-1')
ax.set_title('252日滚动年化夏普比率',fontsize=13)
ax.legend(loc='upper right')
ax.grid(alpha=0.3)

#回撤
ax = axes [1]
for col in dd_df.columns:
    ax.fill_between(dd_df.index, dd_df[col] * 100, 0,alpha=0.3, label=col)
ax.set_title('历史回撤(%)',fontsize=13)
ax.legend(loc='lower right')
ax.grid(alpha=0.3)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_:f'{x:.0f}%'))
plt.tight_layout()
# plt.show()   # 如有要展示图，就打开注释

#打印最大回撤
print("\n最大回撤:")
for col in prices.columns:
    mdd = max_drawdown_value(prices [col])
    print(f"{col}:{mdd:.2%}")

print("=" * 60)
###################################################################




def dual_ma_strategy(prices_series, short_window=20, long_window=60):
    """
    双均线策略（金叉买入、死叉卖出）

    Parameters:
    ----------
    prices_series : pandas.Series
        资产的价格时间序列（如每日收盘价）
    short_window : int, default 20
        短期移动平均线的窗口长度（例如20天）
    long_window : int, default 60
        长期移动平均线的窗口长度（例如60天）

    Returns:
    -------
    pandas.DataFrame
        包含原始价格、日收益率、两条均线、交易信号、
        持仓状态、策略日收益以及累积收益的数据表，
        并删除了因计算均线和滞后信号产生的缺失值行。
    """
    # 创建一个与价格序列索引相同的新DataFrame
    df = pd.DataFrame(index=prices_series.index)

    # 保存原始价格
    df['price'] = prices_series

    # 计算日收益率：今日价格相对于昨日价格的百分比变化
    # pct_change() 会自动处理第一个NaN值
    df['returns'] = prices_series.pct_change()

    # ---------- 计算两条移动平均线 ----------
    # 短期均线：使用 rolling() 创建滑动窗口，然后求均值
    df['MA_short'] = prices_series.rolling(short_window).mean()
    # 长期均线：同理，但窗口更长
    df['MA_long'] = prices_series.rolling(long_window).mean()

    # ---------- 生成交易信号 ----------
    # 初始化信号列为全0（默认空仓）
    df['signal'] = 0
    # 当短期均线高于长期均线时，产生买入信号（持有=1）
    # 此处使用了布尔索引进行条件赋值
    df.loc[df['MA_short'] > df['MA_long'], 'signal'] = 1

    # ---------- 消除前视偏差 ----------
    # 实际交易中，信号只能在下一个交易日开盘时执行
    # 因此将信号向后平移一天：今天的持仓由昨天的信号决定
    df['position'] = df['signal'].shift(1)

    # ---------- 计算策略每日收益率 ----------
    # 策略收益 = 持仓状态 × 当日市场收益率
    # 当持仓为1时获得全部市场收益，持仓为0时收益为0
    df['strategy_returns'] = df['position'] * df['returns']

    # ---------- 计算累积收益曲线 ----------
    # 市场累积收益：假设始终满仓，每日复利累计
    df['cum_market'] = (1 + df['returns']).cumprod()
    # 策略累积收益：根据策略信号每日复利累计
    df['cum_strategy'] = (1 + df['strategy_returns']).cumprod()

    # 删除所有包含NaN值的行
    # 这些NaN主要来自：初始几天的收益率、均线未形成、shift造成的首行缺失
    #dropna()：删除缺失行后，返回的数据从第一个有效信号开始，便于后续分析和绘图。
    return df.dropna()

#对SPY运行策略
spy_prices = prices ['SPY']
result = dual_ma_strategy(spy_prices, short_window=20, long_window=60)
print(f"策略回测结果(SPY,MA20/MA60):")
print(f"回测区间:{result.index[0].strftime('%Y-%m-%d')}至{result.index[-1].strftime('%Y-%m-%d')}")
print(f"总交易日数:{len(result)}")
print(f"持仓天数占比:{result['position'].mean():.1%}")
result.tail()
display(result.tail(10))

# 可视化：价格、均线、买卖点和策略收益
fig, axes = plt.subplots(2, 1, figsize=(14, 10))

# 图1: 价格、均线和买卖点
ax = axes[0]
ax.plot(result.index, result['price'], label='SPY价格', linewidth=2, color='black')
ax.plot(result.index, result['MA_short'], label='MA20', linewidth=1.5, alpha=0.7)
ax.plot(result.index, result['MA_long'], label='MA60', linewidth=1.5, alpha=0.7)

# 标出买入点（position从0变为1）
buy_signals = result[result['position'].diff() == 1]
ax.scatter(buy_signals.index, buy_signals['price'], color='green', marker='^', s=150, label='买入', zorder=5)

# 标出卖出点（position从1变为0）
sell_signals = result[result['position'].diff() == -1]
ax.scatter(sell_signals.index, sell_signals['price'], color='red', marker='v', s=150, label='卖出', zorder=5)

ax.set_title('双均线策略 - 价格、均线和交易信号', fontsize=13, fontweight='bold')
ax.legend(loc='upper left', fontsize=10)
ax.grid(alpha=0.3)
ax.set_ylabel('价格($)', fontsize=11)

# 图2: 策略收益 vs 市场收益
ax = axes[1]
ax.plot(result.index, (result['cum_market'] - 1) * 100, label='市场收益(%)', linewidth=1.5, color='blue')
ax.plot(result.index, (result['cum_strategy'] - 1) * 100, label='策略收益(%)', linewidth=1.5, color='orange')
ax.set_title('累积收益对比', fontsize=13, fontweight='bold')
ax.legend(loc='upper left', fontsize=10)
ax.grid(alpha=0.3)
ax.set_ylabel('收益率(%)', fontsize=11)
ax.set_xlabel('日期', fontsize=11)

plt.tight_layout()
# plt.show()

# 打印策略性能指标
print("\n" + "="*60)
print("策略性能指标:")
print("="*60)
final_market_return = (result['cum_market'].iloc[-1] - 1) * 100
final_strategy_return = (result['cum_strategy'].iloc[-1] - 1) * 100
strategy_outperformance = final_strategy_return - final_market_return
print(f"市场累积收益: {final_market_return:.2f}%")
print(f"策略累积收益: {final_strategy_return:.2f}%")
print(f"策略超额收益: {strategy_outperformance:.2f}%")
print(f"买入次数: {len(buy_signals)}")
print(f"卖出次数: {len(sell_signals)}")



print("="*60)
###################################################################

# 相关性热力图
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# 选择 数据
selected_tickers = ['SPY', 'QQQ', 'IWM']
selected_returns = daily_returns[selected_tickers]

# 计算相关系数矩阵
correlation_matrix = selected_returns.corr()

# 图1: 日收益率相关性热力图
ax = axes[0]
sns.heatmap(correlation_matrix, annot=True, fmt='.4f', cmap='coolwarm',
            square=True, cbar_kws={'label': '相关系数'}, ax=ax,
            vmin=-1, vmax=1, linewidths=2, linecolor='white')
ax.set_title('日收益率相关性热力图', fontsize=13, fontweight='bold')

# 图2: 价格相关性热力图
ax = axes[1]
price_correlation = prices[selected_tickers].corr()
sns.heatmap(price_correlation, annot=True, fmt='.4f', cmap='coolwarm',
            square=True, cbar_kws={'label': '相关系数'}, ax=ax,
            vmin=-1, vmax=1, linewidths=2, linecolor='white')
ax.set_title('收盘价相关性热力图', fontsize=13, fontweight='bold')

plt.tight_layout()
# plt.show()

# 打印相关性统计
print("\n相关性统计:")
print("="*60)
print("日收益率相关系数:")
display(correlation_matrix)
print("\n收盘价相关系数:")
display(price_correlation)
print(f"\nSPY 与 QQQ 的日收益率相关系数: {correlation_matrix.loc['SPY', 'QQQ']:.4f}")
print(f"SPY 与 QQQ 的收盘价相关系数: {price_correlation.loc['SPY', 'QQQ']:.4f}")
print("="*60)

# 滚动相关性曲线
print("\n绘制滚动相关性曲线...")
print("="*60)

fig, ax = plt.subplots(figsize=(14, 6))

# 计算三只股票两两之间的滚动相关系数（窗口=60日）
rolling_window = 60

# SPY vs QQQ
rolling_corr_spy_qqq = daily_returns['SPY'].rolling(rolling_window).corr(daily_returns['QQQ'])
ax.plot(rolling_corr_spy_qqq.index, rolling_corr_spy_qqq, label='SPY vs QQQ', linewidth=1.5)

# SPY vs IWM
rolling_corr_spy_iwm = daily_returns['SPY'].rolling(rolling_window).corr(daily_returns['IWM'])
ax.plot(rolling_corr_spy_iwm.index, rolling_corr_spy_iwm, label='SPY vs IWM', linewidth=1.5)

# QQQ vs IWM
rolling_corr_qqq_iwm = daily_returns['QQQ'].rolling(rolling_window).corr(daily_returns['IWM'])
ax.plot(rolling_corr_qqq_iwm.index, rolling_corr_qqq_iwm, label='QQQ vs IWM', linewidth=1.5)

# 添加参考线
ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5, alpha=0.3)
ax.axhline(y=0.5, color='green', linestyle='--', linewidth=0.8, alpha=0.3, label='相关性=0.5')
ax.axhline(y=-0.5, color='red', linestyle='--', linewidth=0.8, alpha=0.3, label='相关性=-0.5')

ax.set_title(f'{rolling_window}日滚动相关系数', fontsize=13, fontweight='bold')
ax.set_xlabel('日期', fontsize=11)
ax.set_ylabel('相关系数', fontsize=11)
ax.legend(loc='best', fontsize=10)
ax.grid(alpha=0.3)
ax.set_ylim(-1, 1)

plt.tight_layout()
plt.show()

# 打印滚动相关性统计
print(f"\n{rolling_window}日滚动相关性统计:")
print(f"SPY vs QQQ - 平均: {rolling_corr_spy_qqq.mean():.4f}, 最小: {rolling_corr_spy_qqq.min():.4f}, 最大: {rolling_corr_spy_qqq.max():.4f}")
print(f"SPY vs IWM - 平均: {rolling_corr_spy_iwm.mean():.4f}, 最小: {rolling_corr_spy_iwm.min():.4f}, 最大: {rolling_corr_spy_iwm.max():.4f}")
print(f"QQQ vs IWM - 平均: {rolling_corr_qqq_iwm.mean():.4f}, 最小: {rolling_corr_qqq_iwm.min():.4f}, 最大: {rolling_corr_qqq_iwm.max():.4f}")
print("="*60)

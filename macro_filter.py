"""
宏观过滤器 - 结合基本面自动调整交易策略
根据市场情绪、新闻、宏观指标动态调整风控参数
"""
import json
import os
from datetime import datetime, timedelta

# 宏观风险等级
RISK_LEVELS = {
    'LOW': {'max_positions': 4, 'position_pct': 0.25, 'signal_threshold': 3, 'atr_multiplier': 2.0},
    'MEDIUM': {'max_positions': 3, 'position_pct': 0.15, 'signal_threshold': 4, 'atr_multiplier': 1.5},
    'HIGH': {'max_positions': 2, 'position_pct': 0.10, 'signal_threshold': 5, 'atr_multiplier': 1.2},
    'EXTREME': {'max_positions': 0, 'position_pct': 0, 'signal_threshold': 999, 'atr_multiplier': 1.0}
}

# 宏观关键词词典
MACRO_KEYWORDS = {
    'bullish': ['bull run', 'rally', 'surge', 'adoption', 'etf approval', 'halving', 'institutional'],
    'bearish': ['crash', 'dump', 'sell-off', 'liquidation', 'hack', 'ban', 'regulation crackdown', 'miners losing'],
    'extreme_fear': ['extreme fear', 'panic sell', 'all-time high fear', 'massive liquidation', 'war', 'ultimatum'],
    'extreme_greed': ['fomo', 'to the moon', 'parabolic', 'mania', 'bubble']
}

class MacroFilter:
    """宏观过滤器主类"""
    
    def __init__(self, data_dir='data'):
        self.data_dir = data_dir
        self.state_file = os.path.join(data_dir, 'macro_state.json')
        self.risk_level = 'MEDIUM'  # 默认中等风险
        self.last_update = None
        self._ensure_dir()
        self._load_state()
    
    def _ensure_dir(self):
        """确保数据目录存在"""
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
    
    def _load_state(self):
        """加载状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.risk_level = data.get('risk_level', 'MEDIUM')
                    self.last_update = datetime.fromisoformat(data.get('last_update', datetime.now().isoformat()))
            except:
                pass
    
    def _save_state(self):
        """保存状态"""
        with open(self.state_file, 'w') as f:
            json.dump({
                'risk_level': self.risk_level,
                'last_update': datetime.now().isoformat()
            }, f)
    
    def analyze_news_sentiment(self, news_list):
        """
        分析新闻情绪
        
        Args:
            news_list: 新闻列表 [{'title': str, 'source': str, 'time': str}]
        
        Returns:
            {'score': float(-1 to 1), 'risk_level': str, 'reason': str}
        """
        if not news_list:
            return {'score': 0, 'risk_level': 'MEDIUM', 'reason': '无新闻数据'}
        
        bullish_count = 0
        bearish_count = 0
        extreme_fear_count = 0
        extreme_greed_count = 0
        
        for news in news_list:
            title = news.get('title', '').lower()
            
            for keyword in MACRO_KEYWORDS['bullish']:
                if keyword in title:
                    bullish_count += 1
            
            for keyword in MACRO_KEYWORDS['bearish']:
                if keyword in title:
                    bearish_count += 1
            
            for keyword in MACRO_KEYWORDS['extreme_fear']:
                if keyword in title:
                    extreme_fear_count += 1
            
            for keyword in MACRO_KEYWORDS['extreme_greed']:
                if keyword in title:
                    extreme_greed_count += 1
        
        # 计算情绪分数 (-1 到 1)
        total_signals = bullish_count + bearish_count + extreme_fear_count + extreme_greed_count
        if total_signals == 0:
            score = 0
        else:
            # 极端恐惧权重更高
            fear_weight = extreme_fear_count * 2
            greed_weight = extreme_greed_count * 2
            score = (bullish_count - bearish_count - fear_weight + greed_weight) / total_signals
        
        # 确定风险等级
        if extreme_fear_count >= 2 or score < -0.7:
            risk_level = 'EXTREME'
            reason = f'检测到{extreme_fear_count}个极端恐慌信号'
        elif extreme_fear_count == 1 or score < -0.4:
            risk_level = 'HIGH'
            reason = f'市场情绪偏空(分数:{score:.2f})，检测到恐慌信号'
        elif score < -0.1:
            risk_level = 'MEDIUM'
            reason = f'市场情绪略偏空(分数:{score:.2f})'
        elif score > 0.4:
            risk_level = 'LOW'
            reason = f'市场情绪偏多(分数:{score:.2f})'
        else:
            risk_level = 'MEDIUM'
            reason = f'市场情绪中性(分数:{score:.2f})'
        
        return {
            'score': score,
            'risk_level': risk_level,
            'reason': reason,
            'details': {
                'bullish': bullish_count,
                'bearish': bearish_count,
                'extreme_fear': extreme_fear_count,
                'extreme_greed': extreme_greed_count
            }
        }
    
    def update_risk_level(self, news_list=None, manual_level=None):
        """
        更新风险等级
        
        Args:
            news_list: 新闻列表（可选）
            manual_level: 手动设置风险等级（可选，覆盖自动分析）
        """
        if manual_level:
            self.risk_level = manual_level
        elif news_list:
            analysis = self.analyze_news_sentiment(news_list)
            self.risk_level = analysis['risk_level']
        
        self.last_update = datetime.now()
        self._save_state()
    
    def get_trading_params(self):
        """
        获取当前风险等级对应的交易参数
        
        Returns:
            {
                'max_positions': int,
                'position_pct': float,
                'signal_threshold': int,
                'atr_multiplier': float,
                'can_trade': bool
            }
        """
        params = RISK_LEVELS.get(self.risk_level, RISK_LEVELS['MEDIUM']).copy()
        params['can_trade'] = params['max_positions'] > 0
        params['risk_level'] = self.risk_level
        return params
    
    def check_can_open_position(self, current_positions=0):
        """
        检查是否可以开新仓
        
        Args:
            current_positions: 当前持仓数量
        
        Returns:
            (can_open, reason)
        """
        params = self.get_trading_params()
        
        if not params['can_trade']:
            return False, f'宏观风险等级为{self.risk_level}，禁止新开仓'
        
        if current_positions >= params['max_positions']:
            return False, f'当前持仓{current_positions}已达到上限{params["max_positions"]}'
        
        return True, f'允许开仓 (风险等级: {self.risk_level})'
    
    def get_status(self):
        """获取当前状态"""
        params = self.get_trading_params()
        return {
            'risk_level': self.risk_level,
            'last_update': self.last_update.isoformat() if self.last_update else None,
            'params': params
        }


# 便捷函数
def quick_macro_check(news_titles=None):
    """
    快速宏观检查
    
    Args:
        news_titles: 新闻标题列表
    
    Returns:
        {'can_trade': bool, 'risk_level': str, 'reason': str}
    """
    filter = MacroFilter()
    
    if news_titles:
        news_list = [{'title': t} for t in news_titles]
        analysis = filter.analyze_news_sentiment(news_list)
        filter.risk_level = analysis['risk_level']
    
    params = filter.get_trading_params()
    return {
        'can_trade': params['can_trade'],
        'risk_level': filter.risk_level,
        'max_positions': params['max_positions'],
        'position_pct': params['position_pct'],
        'reason': analysis.get('reason', '默认中等风险') if news_titles else '无新闻分析'
    }


if __name__ == '__main__':
    # 测试
    test_news = [
        {'title': 'Bitcoin options signal extreme fear as downside protection premium hits new all-time high'},
        {'title': 'Bitcoin miners are losing $19,000 on every BTC produced'},
        {'title': 'Trump gives 48-hour ultimatum on Iran power plants'},
        {'title': 'XRP falls 3% as breakdown below $1.44'},
    ]
    
    filter = MacroFilter()
    result = filter.analyze_news_sentiment(test_news)
    
    print('=== 宏观过滤器测试结果 ===')
    print(f"情绪分数: {result['score']:.2f}")
    print(f"风险等级: {result['risk_level']}")
    print(f"原因: {result['reason']}")
    print(f"详细统计: {result['details']}")
    print()
    
    filter.update_risk_level(test_news)
    params = filter.get_trading_params()
    print('=== 交易参数 ===')
    print(f"是否可交易: {params['can_trade']}")
    print(f"最大持仓: {params['max_positions']}")
    print(f"仓位比例: {params['position_pct']*100}%")
    print(f"信号阈值: {params['signal_threshold']}")
    print(f"ATR倍数: {params['atr_multiplier']}")

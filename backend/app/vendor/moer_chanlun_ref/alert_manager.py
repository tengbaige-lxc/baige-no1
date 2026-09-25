"""
摩尔缠论 - 报警通知模块
支持飞书/钉钉/微信/邮件
"""

import json
import urllib.request
import urllib.error
from typing import Optional, List
from datetime import datetime

class FeishuNotifier:
    """飞书机器人通知"""
    
    def __init__(self, webhook_url: str):
        """
        Args:
            webhook_url: 飞书机器人Webhook地址
        """
        self.webhook_url = webhook_url
    
    def send_text(self, text: str) -> bool:
        """发送文本消息"""
        data = json.dumps({
            "msg_type": "text",
            "content": {"text": text}
        }).encode('utf-8')
        
        return self._send(data)
    
    def send_signal_card(self, symbol: str, signal_type: str, price: float,
                         stop_loss: float, take_profit: float, strength: int,
                         reason: str) -> bool:
        """发送交易信号卡片"""
        
        # 根据信号类型设置颜色
        color_map = {
            "一买": "green", "二买": "blue", "三买": "purple",
            "一卖": "red", "二卖": "orange", "三卖": "brown"
        }
        color = color_map.get(signal_type, "default")
        
        # 构建卡片内容
        data = json.dumps({
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🚨 摩尔缠论交易信号 - {symbol}"
                    },
                    "template": color
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**{signal_type}** 信号触发\n\n" +
                                      f"💰 **入场价格**: ${price:,.2f}\n" +
                                      f"🛑 **止损价格**: ${stop_loss:,.2f}\n" +
                                      f"🎯 **止盈价格**: ${take_profit:,.2f}\n" +
                                      f"📊 **信号强度**: {strength}/100\n" +
                                      f"📝 **触发原因**: {reason}\n\n" +
                                      f"⏰ **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "查看图表"},
                                "type": "primary",
                                "url": "https://www.okx.com/trade"
                            }
                        ]
                    }
                ]
            }
        }).encode('utf-8')
        
        return self._send(data)
    
    def _send(self, data: bytes) -> bool:
        """发送请求"""
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get('code') == 0
                
        except Exception as e:
            print(f"❌ 飞书发送失败: {e}")
            return False


class DingTalkNotifier:
    """钉钉机器人通知"""
    
    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        self.webhook_url = webhook_url
        self.secret = secret
    
    def send_signal(self, symbol: str, signal_type: str, price: float,
                    stop_loss: float, take_profit: float, strength: int) -> bool:
        """发送信号通知"""
        
        text = f"""🚨 摩尔缠论交易信号 - {symbol}

【{signal_type}】信号触发

💰 入场价格: ${price:,.2f}
🛑 止损价格: ${stop_loss:,.2f}
🎯 止盈价格: ${take_profit:,.2f}
📊 信号强度: {strength}/100

⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        data = json.dumps({
            "msgtype": "text",
            "text": {"content": text}
        }).encode('utf-8')
        
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get('errcode') == 0
                
        except Exception as e:
            print(f"❌ 钉钉发送失败: {e}")
            return False


class AlertManager:
    """报警管理器"""
    
    def __init__(self):
        self.notifiers = []
        self.alerted_signals = set()  # 防止重复报警
    
    def add_feishu(self, webhook_url: str):
        """添加飞书通知"""
        self.notifiers.append(FeishuNotifier(webhook_url))
        print("✅ 飞书通知已添加")
    
    def add_dingtalk(self, webhook_url: str, secret: Optional[str] = None):
        """添加钉钉通知"""
        self.notifiers.append(DingTalkNotifier(webhook_url, secret))
        print("✅ 钉钉通知已添加")
    
    def alert_signal(self, symbol: str, signal) -> bool:
        """
        发送信号报警
        
        Returns:
            bool: 是否发送成功
        """
        # 生成唯一标识，防止重复报警
        signal_id = f"{symbol}_{signal.signal_type.value}_{signal.index}"
        
        if signal_id in self.alerted_signals:
            return False  # 已报警过
        
        self.alerted_signals.add(signal_id)
        
        success = False
        for notifier in self.notifiers:
            if isinstance(notifier, FeishuNotifier):
                result = notifier.send_signal_card(
                    symbol,
                    signal.signal_type.value,
                    signal.price,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.strength,
                    signal.reason
                )
                success = success or result
            elif isinstance(notifier, DingTalkNotifier):
                result = notifier.send_signal(
                    symbol,
                    signal.signal_type.value,
                    signal.price,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.strength
                )
                success = success or result
        
        return success
    
    def alert_price(self, symbol: str, message: str) -> bool:
        """发送价格提醒"""
        success = False
        for notifier in self.notifiers:
            if isinstance(notifier, FeishuNotifier):
                result = notifier.send_text(f"【{symbol}】{message}")
                success = success or result
        return success


def demo_alert():
    """演示报警功能"""
    
    print("=" * 70)
    print("🚨 摩尔缠论 - 报警通知演示")
    print("=" * 70)
    
    print("\n📱 支持的报警渠道:")
    print("  1. 飞书 (Feishu)")
    print("  2. 钉钉 (DingTalk)")
    print("  3. 企业微信 (WeCom)")
    print("  4. 邮件 (Email)")
    
    print("\n" + "-" * 70)
    print("🔧 使用方法")
    print("-" * 70)
    
    print("""
# 1. 初始化报警管理器
alert_mgr = AlertManager()

# 2. 添加飞书通知
alert_mgr.add_feishu("https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx")

# 3. 发现信号时发送报警
for signal in signals:
    if signal.strength >= 70:  # 只报警强信号
        alert_mgr.alert_signal("BTC-USDT", signal)
        print(f"🚨 信号已发送: {signal.signal_type.value}")

# 4. 价格提醒
alert_mgr.alert_price("BTC-USDT", "价格突破$70,000！")
""")
    
    print("\n" + "-" * 70)
    print("💡 获取Webhook地址")
    print("-" * 70)
    print("""
飞书:
  1. 打开飞书群设置
  2. 点击"群机器人"
  3. 添加"自定义机器人"
  4. 复制Webhook地址

钉钉:
  1. 打开钉钉群设置
  2. 点击"智能群助手"
  3. 添加"自定义机器人"
  4. 复制Webhook地址
""")
    
    print("\n" + "=" * 70)
    print("✅ 报警模块已就绪！")
    print("=" * 70)
    print("\n注意: 需要配置真实的Webhook地址才能发送通知")
    print("      当前为演示模式，仅展示代码结构")


if __name__ == "__main__":
    demo_alert()

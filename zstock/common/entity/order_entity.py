import logging
from datetime import datetime
from typing import Dict, List, Optional

class Order:
    """订单对象。"""

    def __init__(self, order_id: str, stock_code: str, direction: str,
                 volume: int, price_type: str = 'market', price: Optional[float] = None):
        self.order_id = order_id
        self.stock_code = stock_code
        self.direction = direction
        self.volume = volume
        self.price_type = price_type
        self.price = price
        self.status = 'pending'
        self.filled_volume = 0
        self.filled_price = None
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict:
        return {
            'order_id': self.order_id,
            'stock_code': self.stock_code,
            'direction': self.direction,
            'volume': self.volume,
            'price_type': self.price_type,
            'price': self.price,
            'status': self.status,
            'filled_volume': self.filled_volume,
            'filled_price': self.filled_price,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    def __repr__(self) -> str:
        return f"Order({self.order_id}, {self.stock_code}, {self.direction}, {self.volume})"


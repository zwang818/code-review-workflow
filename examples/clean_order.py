"""订单列表处理：保留输入列表，每次返回独立结果。"""

def add_order(item, orders=None):
    result = [] if orders is None else list(orders)
    result.append(item)
    return result

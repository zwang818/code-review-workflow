"""离线教学反例，故意保留共享状态与原地修改问题。"""

def add_order(item, orders=[]):
    orders.append(item)
    return orders

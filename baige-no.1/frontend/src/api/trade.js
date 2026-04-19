import request from './request'

export function placeOrder(data) {
  return request.post('/trade/order', data)
}

export function cancelOrder(orderId) {
  return request.delete(`/trade/order/${orderId}`)
}

export function getOrders(params) {
  return request.get('/trade/orders', { params })
}

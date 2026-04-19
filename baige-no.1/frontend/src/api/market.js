import request from './request'

export function getTicker(symbol) {
  return request.get(`/market/ticker/${symbol}`)
}

export function getKlines(symbol, params) {
  return request.get(`/market/klines/${symbol}`, { params })
}

export function getBalance() {
  return request.get('/market/balance')
}

export function getOrderbook(symbol) {
  return request.get(`/market/orderbook/${symbol}`)
}

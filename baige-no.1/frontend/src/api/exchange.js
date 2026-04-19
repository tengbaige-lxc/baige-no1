import request from './request'

export function getExchangeConfigs() {
  return request.get('/exchange')
}

export function createExchangeConfig(data) {
  return request.post('/exchange', data)
}

export function updateExchangeConfig(id, data) {
  return request.put(`/exchange/${id}`, data)
}

export function deleteExchangeConfig(id) {
  return request.delete(`/exchange/${id}`)
}

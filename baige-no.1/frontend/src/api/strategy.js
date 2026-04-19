import request from './request'

export function getStrategies(params) {
  return request.get('/strategy', { params })
}

export function createStrategy(data) {
  return request.post('/strategy', data)
}

export function updateStrategy(id, data) {
  return request.put(`/strategy/${id}`, data)
}

export function deleteStrategy(id) {
  return request.delete(`/strategy/${id}`)
}

export function getStrategyLogs(id, params) {
  return request.get(`/strategy/${id}/logs`, { params })
}

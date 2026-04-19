import request from './request'

export function getStats() {
  return request.get('/dashboard/stats')
}

export function getRecentLogs() {
  return request.get('/dashboard/recent-logs')
}

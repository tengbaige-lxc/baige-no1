import request from './request'

export function getPerformanceOverview(days = 30) {
  return request({
    url: '/performance/overview',
    method: 'get',
    params: { days }
  })
}

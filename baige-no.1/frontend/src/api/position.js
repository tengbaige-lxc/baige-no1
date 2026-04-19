import request from './request'

export function getLivePositions() {
  return request({
    url: '/positions/live',
    method: 'get'
  })
}

import request from './request'

export function getSignals(params) {
  return request({
    url: '/signals',
    method: 'get',
    params
  })
}

export function scanSignal(data) {
  return request({
    url: '/signals/scan',
    method: 'post',
    data
  })
}

export function scanBatchSignals(data) {
  return request({
    url: '/signals/scan-batch',
    method: 'post',
    data
  })
}

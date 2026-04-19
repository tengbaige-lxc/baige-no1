import request from './request'

// 减仓记录
export function getReduceRecords(params) {
  return request({
    url: '/reduce-short/reduce',
    method: 'get',
    params
  })
}

export function createReduceRecord(params) {
  return request({
    url: '/reduce-short/reduce',
    method: 'post',
    params
  })
}

export function updateReduceRecord(id, params) {
  return request({
    url: `/reduce-short/reduce/${id}`,
    method: 'put',
    params
  })
}

// 做空记录
export function getShortRecords(params) {
  return request({
    url: '/reduce-short/short',
    method: 'get',
    params
  })
}

export function createShortRecord(params) {
  return request({
    url: '/reduce-short/short',
    method: 'post',
    params
  })
}

export function updateShortRecord(id, params) {
  return request({
    url: `/reduce-short/short/${id}`,
    method: 'put',
    params
  })
}

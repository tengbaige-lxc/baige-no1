import request from './request'

export function getTradeRecords(params) {
  return request({
    url: '/trade-records',
    method: 'get',
    params
  })
}

export function createTradeRecord(data) {
  return request({
    url: '/trade-records',
    method: 'post',
    data
  })
}

export function updateTradeRecord(id, data) {
  return request({
    url: `/trade-records/${id}`,
    method: 'put',
    data
  })
}

export function deleteTradeRecord(id) {
  return request({
    url: `/trade-records/${id}`,
    method: 'delete'
  })
}

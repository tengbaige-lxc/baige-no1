import request from './request'

export function getBacktests(params) {
  return request({
    url: '/backtest',
    method: 'get',
    params
  })
}

export function createBacktest(data) {
  return request({
    url: '/backtest',
    method: 'post',
    data
  })
}

export function getBacktestDetail(id) {
  return request({
    url: `/backtest/${id}`,
    method: 'get'
  })
}

export function deleteBacktest(id) {
  return request({
    url: `/backtest/${id}`,
    method: 'delete'
  })
}

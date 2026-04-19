import request from './request'

export function getRiskConfigs(params) {
  return request({
    url: '/risk-config',
    method: 'get',
    params
  })
}

export function createRiskConfig(data) {
  return request({
    url: '/risk-config',
    method: 'post',
    data
  })
}

export function updateRiskConfig(id, data) {
  return request({
    url: `/risk-config/${id}`,
    method: 'put',
    data
  })
}

export function deleteRiskConfig(id) {
  return request({
    url: `/risk-config/${id}`,
    method: 'delete'
  })
}

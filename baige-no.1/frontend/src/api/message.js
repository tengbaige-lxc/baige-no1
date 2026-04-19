import request from './request'

export function getMessageList(params) {
  return request.get('/messages', { params })
}

export function getUnreadCount() {
  return request.get('/messages/unread-count')
}

export function createMessage(data) {
  return request.post('/messages', data)
}

export function markRead(id) {
  return request.put(`/messages/${id}/read`)
}

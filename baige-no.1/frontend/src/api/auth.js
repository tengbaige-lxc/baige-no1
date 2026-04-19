import request from './request'

export function login(data) {
  const params = new URLSearchParams()
  params.append('username', data.username)
  params.append('password', data.password)
  return request.post('/login/access-token', params, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
}

export function getProfile() {
  return request.get('/login/profile')
}

export function isExternal(path) {
  return /^(https?:|mailto:|tel:)/.test(path)
}

export function validUsername(str) {
  return /^[a-zA-Z0-9_]{3,20}$/.test(str)
}

export function validEmail(email) {
  return /^\S+@\S+\.\S+$/.test(email)
}

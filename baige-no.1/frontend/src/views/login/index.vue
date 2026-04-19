<template>
  <div class="login-container">
    <div class="login-bg">
      <div class="gradient-orb orb-1"></div>
      <div class="gradient-orb orb-2"></div>
      <div class="gradient-orb orb-3"></div>
    </div>
    <el-card class="login-box" shadow="never">
      <div class="login-header">
        <div class="logo-icon">
          <el-icon size="40" color="#58a6ff"><Promotion /></el-icon>
        </div>
        <h2 class="title">白鸽一号</h2>
        <p class="subtitle">数字货币智能交易管理平台</p>
      </div>
      <el-form
        ref="loginFormRef"
        :model="loginForm"
        :rules="loginRules"
        size="large"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="用户名"
            prefix-icon="User"
            clearable
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="密码"
            prefix-icon="Lock"
            show-password
            clearable
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="loading"
            style="width: 100%"
            size="large"
            @click="handleLogin"
          >
            登录
          </el-button>
        </el-form-item>
      </el-form>
      <div class="login-tips">
        <p>默认账号: admin / admin123</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const loginFormRef = ref(null)
const loading = ref(false)

const loginForm = reactive({
  username: 'admin',
  password: 'admin123',
})

const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await loginFormRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login(loginForm)
    await userStore.getProfile()
    ElMessage.success('登录成功')
    router.push('/')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-primary);
  position: relative;
  overflow: hidden;
}

.login-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  
  .gradient-orb {
    position: absolute;
    border-radius: 50%;
    filter: blur(80px);
    opacity: 0.15;
  }
  
  .orb-1 {
    width: 400px;
    height: 400px;
    background: #58a6ff;
    top: -100px;
    left: -100px;
  }
  
  .orb-2 {
    width: 300px;
    height: 300px;
    background: #a371f7;
    bottom: -50px;
    right: -50px;
  }
  
  .orb-3 {
    width: 250px;
    height: 250px;
    background: #3fb950;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
  }
}

.login-box {
  width: 420px;
  padding: 20px;
  border-radius: 16px;
  background: var(--bg-card) !important;
  border: 1px solid var(--border-color) !important;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5) !important;
  z-index: 1;
  position: relative;

  .login-header {
    text-align: center;
    margin-bottom: 32px;
    
    .logo-icon {
      width: 72px;
      height: 72px;
      border-radius: 16px;
      background: linear-gradient(135deg, rgba(88, 166, 255, 0.2), rgba(88, 166, 255, 0.05));
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 16px;
      border: 1px solid rgba(88, 166, 255, 0.2);
    }
    
    .title {
      font-size: 28px;
      color: var(--text-primary);
      font-weight: 700;
      letter-spacing: 1px;
    }
    
    .subtitle {
      margin-top: 8px;
      font-size: 14px;
      color: var(--text-secondary);
    }
  }

  .login-tips {
    margin-top: 20px;
    text-align: center;
    font-size: 12px;
    color: var(--text-muted);
  }
}
</style>

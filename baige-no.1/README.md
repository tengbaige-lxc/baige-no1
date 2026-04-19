# 白鸽一号 - 后台管理系统

## 项目简介

白鸽一号是一套基于 **Vue 3 + FastAPI** 开发的现代化后台管理系统，功能齐全、界面美观、易于扩展。

## 技术栈

### 后端
- **FastAPI** - 高性能 Python Web 框架
- **SQLAlchemy 2.0** - 异步 ORM
- **Pydantic** - 数据验证
- **JWT** - 身份认证
- **SQLite** (开发) / PostgreSQL (生产)

### 前端
- **Vue 3** + Vite
- **Element Plus** - UI 组件库
- **Pinia** - 状态管理
- **Vue Router** - 路由管理
- **ECharts** - 数据可视化
- **Axios** - HTTP 客户端

## 功能模块

| 模块 | 功能 |
|------|------|
| 仪表盘 | 数据统计卡片、趋势图表、快捷入口、最近操作 |
| 用户管理 | 用户增删改查、角色分配、状态控制 |
| 角色管理 | 角色增删改查、菜单权限分配 |
| 菜单管理 | 菜单树形管理、路由配置、权限标识 |
| 操作日志 | 操作记录查询、状态码展示 |
| 消息中心 | 消息发送、未读提醒、消息分类 |
| 个人中心 | 资料查看、信息编辑 |

## 快速开始

### 1. 启动后端

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

后端 API 地址: http://localhost:8000

默认管理员账号: `admin` / `admin123`

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端地址: http://localhost:5173

## 项目结构

```
baige-no.1/
├── backend/          # FastAPI 后端
│   ├── app/
│   │   ├── api/      # API 路由
│   │   ├── core/     # 核心配置
│   │   ├── db/       # 数据库
│   │   ├── models/   # 数据模型
│   │   ├── schemas/  # Pydantic 模型
│   │   └── main.py   # 入口文件
│   └── requirements.txt
├── frontend/         # Vue3 前端
│   ├── src/
│   │   ├── api/      # API 请求
│   │   ├── components/
│   │   ├── router/   # 路由
│   │   ├── stores/   # Pinia 状态
│   │   ├── views/    # 页面视图
│   │   └── App.vue
│   └── package.json
└── README.md
```

## 开发计划

- [x] 基础架构搭建
- [x] JWT 认证 + RBAC 权限
- [x] 用户/角色/菜单管理
- [x] 消息中心
- [x] 操作日志
- [ ] 数据字典
- [ ] 文件上传
- [ ] 系统监控

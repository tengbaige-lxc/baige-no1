#!/bin/bash
set -e

echo "🚀 启动 白鸽一号 后台管理系统..."

# Start backend
if [ ! -d "backend/venv" ]; then
    echo "📦 创建 Python 虚拟环境..."
    cd backend && python3 -m venv venv && cd ..
fi

echo "📦 安装后端依赖..."
cd backend
source venv/bin/activate
pip install -q -r requirements.txt
cd ..

echo "🌐 启动后端服务 (http://localhost:8000)..."
cd backend
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
cd ..

# Start frontend
if [ ! -d "frontend/node_modules" ]; then
    echo "📦 安装前端依赖..."
    cd frontend && npm install && cd ..
fi

echo "🎨 启动前端服务 (http://localhost:5173)..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "✅ 白鸽一号 已启动!"
echo "   前端: http://localhost:5173"
echo "   后端: http://localhost:8000"
echo "   API文档: http://localhost:8000/docs"
echo ""
echo "默认账号: admin / admin123"
echo ""
echo "按 Ctrl+C 停止所有服务"

wait $BACKEND_PID $FRONTEND_PID

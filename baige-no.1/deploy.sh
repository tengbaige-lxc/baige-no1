#!/bin/bash
set -e

echo "🚀 白鸽一号 生产部署脚本"
echo "   访问地址: http://43.159.130.219:3022"
echo ""

# 1. Build frontend
echo "📦 构建前端..."
cd frontend
npm install
npm run build
cd ..

# 2. Install backend deps
echo "📦 安装后端依赖..."
cd backend
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt
cd ..

# 3. Start backend (serves both API + static frontend)
echo "🌐 启动服务 (端口 3022)..."
cd backend
source venv/bin/activate

# Use nohup to run in background
nohup uvicorn app.main:app --host 0.0.0.0 --port 3022 > ../server.log 2>&1 &
echo $! > ../server.pid

cd ..

echo ""
echo "✅ 部署完成!"
echo "   访问: http://43.159.130.219:3022"
echo "   API文档: http://43.159.130.219:3022/docs"
echo "   默认账号: admin / admin123"
echo ""
echo "查看日志: tail -f server.log"
echo "停止服务: kill \$(cat server.pid)"

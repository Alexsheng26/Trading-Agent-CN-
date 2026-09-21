# Trading-Agent-CN 常用命令
.PHONY: help setup dev dev-backend dev-frontend test lint build docker clean

help:
	@echo "make setup         安装前后端依赖（首次运行）"
	@echo "make dev           同时启动前后端（开发模式）"
	@echo "make dev-backend   只启动后端 :8000"
	@echo "make dev-frontend  只启动前端 :3000"
	@echo "make test          跑后端测试"
	@echo "make lint          跑 ruff + tsc"
	@echo "make build         构建前端生产包"
	@echo "make docker        docker compose 一键起全套"
	@echo "make clean         清理构建产物与缓存"

setup:
	cd backend && python3 -m venv .venv && \
		.venv/bin/pip install --upgrade pip && \
		.venv/bin/pip install -e ".[dev,akshare,tushare,openai]"
	cd frontend && pnpm install
	@test -f .env || (cp .env.example .env && echo "已生成 .env，请填入 API Key")

dev-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	cd frontend && pnpm dev

# 前后端一起起；Ctrl-C 会把两个都停掉
dev:
	@trap 'kill 0' INT TERM; \
	$(MAKE) dev-backend & \
	$(MAKE) dev-frontend & \
	wait

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check app tests
	cd frontend && pnpm typecheck

build:
	cd frontend && pnpm build

docker:
	docker compose up -d --build
	@echo "打开 http://localhost:3000"

clean:
	rm -rf frontend/.next frontend/out
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache

#!/bin/bash
# ============================================================
# deploy.sh — Open DeepWiki 一键升级部署脚本
#
# 用法：
#   ./deploy.sh                  # 智能检测变更服务，仅重建必要部分
#   ./deploy.sh --no-cache       # 强制全量无缓存重建（适用于依赖变更）
#   ./deploy.sh api worker       # 仅重建指定服务
#   ./deploy.sh --no-cache api   # 组合使用
#
# 前置条件（服务器上执行一次）：
#   chmod +x deploy.sh
#   创建 docker-compose.override.yml（见脚本末尾说明）
# ============================================================

set -euo pipefail

COMPOSE_CMD="docker compose"
ALL_SERVICES=(api worker mcp frontend)
NO_CACHE=""
FORCE_SERVICES=()
FORCE_ALL=false

# ─── 参数解析 ──────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE="--no-cache" ;;
    --force)    FORCE_ALL=true ;;
    api|worker|mcp|frontend) FORCE_SERVICES+=("$arg") ;;
    *) echo "未知参数: $arg  （可选值：--no-cache --force api worker mcp frontend）"; exit 1 ;;
  esac
done

# ─── 1. 拉取最新代码 ───────────────────────────────────────
echo ""
echo "==> [1/5] 拉取最新代码 (develop)"
git fetch origin develop

# 暂存追踪文件的本地修改，避免 merge 冲突
if ! git diff --quiet HEAD 2>/dev/null; then
  echo "    检测到本地修改，临时暂存..."
  git stash push -m "deploy-stash-$(date +%Y%m%d_%H%M%S)"
  STASHED=true
else
  STASHED=false
fi

git merge origin/develop --ff-only

if [ "$STASHED" = true ]; then
  echo "    恢复暂存的本地修改..."
  git stash pop || echo "    ⚠ stash pop 失败，请手动执行 git stash list 检查"
fi

# ─── 2. 智能检测需要重建的服务 ─────────────────────────────
echo ""
echo "==> [2/5] 分析变更范围"

if [ ${#FORCE_SERVICES[@]} -gt 0 ]; then
  BUILD_SERVICES=("${FORCE_SERVICES[@]}")
  echo "    指定重建：${BUILD_SERVICES[*]}"
elif [ "$FORCE_ALL" = true ]; then
  BUILD_SERVICES=("${ALL_SERVICES[@]}")
  echo "    --force 全量重建：${BUILD_SERVICES[*]}"
else
  CHANGED=$(git diff HEAD~1 HEAD --name-only 2>/dev/null || echo "")
  BUILD_SERVICES=()

  _changed() {
    # 检查是否有文件匹配给定前缀/关键词
    local patterns=("$@")
    for p in "${patterns[@]}"; do
      echo "$CHANGED" | grep -q "$p" && return 0
    done
    return 1
  }

  # requirements.txt / migrations 变更 → api + worker + mcp 均需重建
  DEPS_CHANGED=false
  if _changed "requirements.txt" "migrations/"; then
    DEPS_CHANGED=true
  fi

  # api / worker 共用 Python 后端代码
  if $DEPS_CHANGED || _changed "app/" "docker/Dockerfile.api" "docker/Dockerfile_zh.api" \
                                      "docker/Dockerfile.worker" "docker/Dockerfile_zh.worker"; then
    BUILD_SERVICES+=(api worker)
  fi

  # mcp 独立（app/mcp_server.py 或依赖变更）
  if $DEPS_CHANGED || _changed "app/mcp_server.py" "docker/Dockerfile.mcp" "docker/Dockerfile_zh.mcp"; then
    # 去重：requirements.txt 变更时 api/worker 已加入，mcp 单独判断
    printf '%s\n' "${BUILD_SERVICES[@]}" | grep -q "^mcp$" || BUILD_SERVICES+=(mcp)
  fi

  # frontend 纯前端变更
  if _changed "frontend/" "docker/Dockerfile.frontend" "docker/Dockerfile_zh.frontend"; then
    BUILD_SERVICES+=(frontend)
  fi

  if [ ${#BUILD_SERVICES[@]} -eq 0 ]; then
    echo "    未检测到影响镜像的变更（文档/配置更新），仅重启容器"
    $COMPOSE_CMD restart
    echo ""
    echo "✅  完成（重启，无需重建）"
    $COMPOSE_CMD ps
    exit 0
  fi

  echo "    需重建：${BUILD_SERVICES[*]}"
fi

# ─── 3. 停止待更新的服务 ───────────────────────────────────
echo ""
echo "==> [3/5] 停止服务：${BUILD_SERVICES[*]}"
$COMPOSE_CMD stop "${BUILD_SERVICES[@]}" 2>/dev/null || true
$COMPOSE_CMD rm -f "${BUILD_SERVICES[@]}" 2>/dev/null || true

# ─── 4. 逐服务构建（顺序模式，防止小内存服务器 OOM）──────────
echo ""
echo "==> [4/5] 逐服务构建 $( [ -n "$NO_CACHE" ] && echo "（--no-cache 全量模式）" || echo "（增量缓存模式）" )"
for svc in "${BUILD_SERVICES[@]}"; do
  echo ""
  echo "    ── 构建 $svc ──────────────────────────────────"
  $COMPOSE_CMD build $NO_CACHE "$svc"
done

# ─── 5. 启动所有服务 ───────────────────────────────────────
echo ""
echo "==> [5/5] 启动服务"
$COMPOSE_CMD up -d
# 注：数据库迁移由 api 容器启动时自动执行 alembic upgrade head，无需手动操作

echo ""
echo "✅  部署完成"
echo ""
$COMPOSE_CMD ps

# ============================================================
# 【服务器首次设置】在项目根目录创建 docker-compose.override.yml
# 该文件已在 .gitignore 中忽略，只需创建一次，之后 git pull 永不冲突
#
# cat > docker-compose.override.yml << 'EOF'
# # 服务器专用覆盖：使用国内镜像源版本的 Dockerfile
# services:
#   api:
#     build:
#       dockerfile: docker/Dockerfile_zh.api
#   worker:
#     build:
#       dockerfile: docker/Dockerfile_zh.worker
#   mcp:
#     build:
#       dockerfile: docker/Dockerfile_zh.mcp
#   frontend:
#     build:
#       dockerfile: docker/Dockerfile_zh.frontend
# EOF
#
# ============================================================

import pytest
from unittest.mock import patch, MagicMock


class TestRepositoryAPI:
    """仓库 API 集成测试"""

    @pytest.mark.asyncio
    async def test_submit_public_repo(self, client):
        """提交公开仓库应返回 201 + task_id"""
        # 注意：process_repository_task 在 handler 内部动态导入，需 patch 源模块
        with patch("app.tasks.process_repo.process_repository_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-task-id-123")
            response = await client.post("/api/repositories", json={
                "url": "https://github.com/pallets/flask",
            })
        assert response.status_code == 201
        data = response.json()
        assert "task_id" in data
        assert "repo_id" in data
        assert data["status"] == "pending"
        assert data["message"] == "任务已提交，正在排队处理"

    @pytest.mark.asyncio
    async def test_submit_invalid_url(self, client):
        """无效 URL 应返回 400"""
        response = await client.post("/api/repositories", json={
            "url": "not-a-valid-url",
        })
        assert response.status_code == 400
        data = response.json()
        assert data["detail"]["error"] == "INVALID_URL"

    @pytest.mark.asyncio
    async def test_get_task_status_not_found(self, client):
        """查询不存在的任务应返回 404"""
        response = await client.get("/api/tasks/nonexistent-task-id")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_repositories_empty(self, client):
        """初始状态仓库列表应为空"""
        response = await client.get("/api/repositories")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """健康检查端点"""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_duplicate_repo_returns_409(self, client):
        """同一仓库在处理中重复提交应返回 409"""
        with patch("app.tasks.process_repo.process_repository_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-task-id-456")
            # 第一次提交
            r1 = await client.post("/api/repositories", json={
                "url": "https://github.com/pallets/werkzeug",
            })
        assert r1.status_code == 201

        # 第二次提交（任务还在处理中）
        with patch("app.tasks.process_repo.process_repository_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-task-id-789")
            r2 = await client.post("/api/repositories", json={
                "url": "https://github.com/pallets/werkzeug",
            })
        assert r2.status_code == 409
        assert r2.json()["detail"]["error"] == "REPO_PROCESSING"

"""Tests for the HTTP server endpoints."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from openmax.server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(queue_dir=tmp_path / "queue", max_slots=6)
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_list_tasks(client):
    resp = client.post("/api/tasks", json={"task": "fix bug", "cwd": "/tmp"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["task"] == "fix bug"
    assert data["status"] == "queued"

    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_create_task_empty_body(client):
    resp = client.post("/api/tasks", json={"task": ""})
    assert resp.status_code == 400


def test_get_task(client):
    resp = client.post("/api/tasks", json={"task": "test task", "cwd": "/tmp"})
    task_id = resp.json()["id"]

    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["task"] == "test task"


def test_get_task_not_found(client):
    resp = client.get("/api/tasks/nonexistent")
    assert resp.status_code == 404


def test_update_task_priority(client):
    resp = client.post("/api/tasks", json={"task": "update me", "cwd": "/tmp"})
    task_id = resp.json()["id"]

    resp = client.patch(f"/api/tasks/{task_id}", json={"priority": 10})
    assert resp.status_code == 200
    assert resp.json()["priority"] == 10


def test_update_task_size(client):
    resp = client.post("/api/tasks", json={"task": "size me", "cwd": "/tmp"})
    task_id = resp.json()["id"]

    resp = client.patch(f"/api/tasks/{task_id}", json={"size": "large"})
    assert resp.status_code == 200
    assert resp.json()["size"] == "large"
    assert resp.json()["size_override"] is True


def test_delete_task(client):
    resp = client.post("/api/tasks", json={"task": "delete me", "cwd": "/tmp"})
    task_id = resp.json()["id"]

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 200

    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 404


def test_stats(client):
    client.post("/api/tasks", json={"task": "t1", "cwd": "/tmp"})
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json()["queued"] == 1


def test_static_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "openMax" in resp.text

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.core.security import require_session
from app.main import app
from app.models.cluster_configuration import ClusterConfiguration
from app.models.project import PipelineConnection, PipelineNode, Project
import app.api.routes.projects as project_routes


def test_run_all_uses_saved_graph_order_and_run_survives_project_autosave(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_session] = lambda: object()
    captured = {}

    def fake_submit(paths, config, request_document):
        captured["request"] = request_document
        captured["paths"] = paths
        return "456789"

    monkeypatch.setattr(project_routes, "submit_run", fake_submit)

    with TestingSession() as db:
        db.add(Project(id="project-1", name="Test", description="", cluster_config_id="cluster-1"))
        db.add(ClusterConfiguration(
            id="cluster-1", name="Test cluster", job_name="spi_test", partition="a100serv",
            stdout="managed-by-spi", stderr="managed-by-spi", nodes=1, tasks=1,
            cpus=4, time="01:00:00", memory="8G",
        ))
        db.add_all([
            PipelineNode(id="input", project_id="project-1", module_id="segyin", x=0, y=0,
                         parameters={"path": "storage/data/input.sgy"}, sort_order=0),
            PipelineNode(id="output", project_id="project-1", module_id="segyout", x=200, y=0,
                         parameters={"path": "storage/data/output.sgy"}, sort_order=1),
        ])
        db.add(PipelineConnection(
            id="edge-1", project_id="project-1", source_node_id="input",
            target_node_id="output", sort_order=0,
        ))
        db.commit()

    client = TestClient(app)
    try:
        response = client.post("/api/projects/project-1/runs")
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "Queued"
        assert run["slurmJobId"] == "456789"
        assert captured["request"]["execution_order"] == ["input", "output"]

        # The graph autosave body does not carry the newly created run; that must not delete it.
        project_response = client.put("/api/projects/project-1", json={
            "id": "project-1",
            "name": "Test",
            "description": "updated",
            "clusterConfigId": "cluster-1",
            "nodes": [
                {"id": "input", "moduleId": "segyin", "x": 0, "y": 0,
                 "parameters": {"path": "storage/data/input.sgy"}},
                {"id": "output", "moduleId": "segyout", "x": 200, "y": 0,
                 "parameters": {"path": "storage/data/output.sgy"}},
            ],
            "connections": [
                {"id": "edge-1", "sourceNodeId": "input", "targetNodeId": "output"}
            ],
            "runs": [],
        })
        assert project_response.status_code == 200, project_response.text
        assert any(item["id"] == run["id"] for item in project_response.json()["runs"])
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()

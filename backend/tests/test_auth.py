from datetime import timedelta

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.core.security import digest_token, utc_now
from app.models.auth import AuthSession, AuthUser

TEST_USERNAME = "spiadmin"
TEST_PASSWORD = "correct horse battery staple length"
ORIGIN = "http://127.0.0.1:5173"


def make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, engine, testing_session


def cleanup(client, engine):
    client.close()
    app.dependency_overrides.clear()
    engine.dispose()


def seed_user(testing_session):
    with testing_session() as db:
        db.add(AuthUser(
            id="auth-user-1",
            username=TEST_USERNAME,
            password_hash=PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(TEST_PASSWORD),
        ))
        db.commit()


def test_login_sets_httponly_session_cookie_and_csrf_cookie_and_protects_api():
    client, engine, testing_session = make_client()
    try:
        seed_user(testing_session)

        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/cluster-configurations").status_code == 401
        assert client.get("/api/data-assets/files").status_code == 401
        assert client.get("/api/hpc/status").status_code == 401
        assert client.get("/api/hpc/access").status_code == 401
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health/database").status_code == 401

        login = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert login.status_code == 200, login.text
        assert login.json()["username"] == TEST_USERNAME

        session_cookie = login.cookies.get("spi_session")
        csrf_cookie = login.cookies.get("spi_csrf")
        assert session_cookie
        assert csrf_cookie
        assert login.cookies.get("spi_session") not in login.text

        set_cookie_headers = login.headers.get_list("set-cookie")
        assert any("spi_session=" in item and "httponly" in item.lower() and "samesite=strict" in item.lower() for item in set_cookie_headers)
        assert any("spi_csrf=" in item and "samesite=strict" in item.lower() for item in set_cookie_headers)

        with testing_session() as db:
            stored_session = db.get(AuthSession, digest_token(session_cookie))
            assert stored_session is not None
            assert stored_session.token_hash != session_cookie
            assert stored_session.csrf_token_hash == digest_token(csrf_cookie)

        assert client.get("/api/projects").status_code == 200
        access_status = client.get("/api/hpc/access")
        assert access_status.status_code == 200, access_status.text
        assert access_status.json()["state"] == "disconnected"

        project_body = {
            "id": "auth-protected-project",
            "name": "Protected",
            "description": "",
            "clusterConfigId": "",
            "nodes": [],
            "connections": [],
            "runs": [],
        }
        csrf_failure = client.post("/api/projects", headers={"Origin": ORIGIN}, json=project_body)
        assert csrf_failure.status_code == 403

        created = client.post(
            "/api/projects",
            headers={"Origin": ORIGIN, "X-SPI-CSRF": csrf_cookie},
            json=project_body,
        )
        assert created.status_code == 201, created.text

        logout = client.post(
            "/api/auth/logout",
            headers={"Origin": ORIGIN, "X-SPI-CSRF": csrf_cookie},
        )
        assert logout.status_code == 204
        assert client.get("/api/projects").status_code == 401
    finally:
        cleanup(client, engine)


def test_invalid_login_is_generic_and_rate_limited():
    client, engine, testing_session = make_client()
    try:
        seed_user(testing_session)
        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                headers={"Origin": ORIGIN},
                json={"username": TEST_USERNAME, "password": "wrong password"},
            )
            assert response.status_code == 401
            assert response.json()["detail"] == "Invalid username or password."
        limited = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert limited.status_code == 429
    finally:
        cleanup(client, engine)


def test_untrusted_origin_is_rejected():
    client, engine, testing_session = make_client()
    try:
        seed_user(testing_session)
        response = client.post(
            "/api/auth/login",
            headers={"Origin": "https://attacker.example"},
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert response.status_code == 403
    finally:
        cleanup(client, engine)


def test_idle_session_expiration_is_enforced():
    client, engine, testing_session = make_client()
    try:
        seed_user(testing_session)
        login = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        )
        assert login.status_code == 200
        token = login.cookies["spi_session"]
        with testing_session() as db:
            session_record = db.get(AuthSession, digest_token(token))
            session_record.last_activity_at = utc_now() - timedelta(minutes=45)
            db.commit()

        expired = client.get("/api/projects")
        assert expired.status_code == 401
        with testing_session() as db:
            assert db.get(AuthSession, digest_token(token)) is None
    finally:
        cleanup(client, engine)

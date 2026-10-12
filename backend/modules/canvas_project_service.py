"""Canvas v2 project storage（节点式 AI 创意画布）.

按用户存储在本地 SQLite，避免依赖
Supabase 迁移。nodes_json 采用 schema_version=2 的角色型节点结构
（prompt/asset/image-gen/image-view/...），并为 M4 社区预留
share_id/is_public/clone_of 等列。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config import Config

CANVAS_SCHEMA_VERSION = 2


class CanvasProjectStorageError(Exception):
    """Storage failure for canvas v2 projects."""


class CanvasProjectValidationError(CanvasProjectStorageError):
    """Invalid project input."""


class CanvasProjectService:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.DATABASE_PATH
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self):
        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS canvas_projects (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  schema_version INTEGER NOT NULL DEFAULT 2,
                  nodes_json TEXT NOT NULL,
                  edges_json TEXT NOT NULL,
                  viewport_json TEXT NOT NULL,
                  production_json TEXT,
                  thumbnail_url TEXT,
                  is_public INTEGER NOT NULL DEFAULT 0,
                  share_id TEXT UNIQUE,
                  published_at TEXT,
                  clone_of TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(canvas_projects)')}
            if 'production_json' not in columns:
                conn.execute('ALTER TABLE canvas_projects ADD COLUMN production_json TEXT')
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_canvas_projects_user_updated
                ON canvas_projects(user_id, updated_at DESC)
                """
            )
            conn.commit()
        except Exception as exc:
            raise CanvasProjectStorageError(f"初始化创意画布存储失败: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _json_dumps(value) -> str:
        return json.dumps(value if value is not None else [], ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _json_loads(raw, fallback):
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except Exception:
            return fallback

    @classmethod
    def _row_to_project(cls, row) -> Dict:
        data = dict(row)
        return {
            "id": data["id"],
            "user_id": data["user_id"],
            "title": data["title"],
            "schema_version": data.get("schema_version") or CANVAS_SCHEMA_VERSION,
            "nodes": cls._json_loads(data.get("nodes_json"), []),
            "edges": cls._json_loads(data.get("edges_json"), []),
            "viewport": cls._json_loads(data.get("viewport_json"), {"x": 0, "y": 0, "zoom": 1}),
            "production": cls._json_loads(data.get("production_json"), None),
            "thumbnail_url": data.get("thumbnail_url"),
            "is_public": bool(data.get("is_public")),
            "share_id": data.get("share_id"),
            "clone_of": data.get("clone_of"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    @staticmethod
    def _coerce_title(title: str) -> str:
        value = str(title or "").strip()
        return value[:80] if value else "未命名画布"

    @staticmethod
    def _coerce_list(value, field_name: str) -> List[Dict]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise CanvasProjectValidationError(f"{field_name} 必须是数组")
        if len(value) > (1000 if field_name == 'nodes' else 3000):
            raise CanvasProjectValidationError(f'{field_name} 超过数量限制')
        if any(not isinstance(item, dict) for item in value):
            raise CanvasProjectValidationError(f'{field_name} 每一项必须是对象')
        return value

    @staticmethod
    def _coerce_viewport(value) -> Dict:
        if not isinstance(value, dict):
            return {"x": 0, "y": 0, "zoom": 1}
        return {
            "x": value.get("x", 0),
            "y": value.get("y", 0),
            "zoom": value.get("zoom", 1),
        }

    def list_projects(self, *, user_id: str, limit: int = 30) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT * FROM canvas_projects
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, max(1, min(int(limit or 30), 100))),
            ).fetchall()
            return [self._row_to_project(row) for row in rows]
        except Exception as exc:
            raise CanvasProjectStorageError(f"读取创意画布列表失败: {exc}") from exc
        finally:
            conn.close()

    def get_project(self, *, user_id: str, project_id: str) -> Dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT * FROM canvas_projects
                WHERE id = ? AND user_id = ?
                """,
                (project_id, user_id),
            ).fetchone()
            if not row:
                raise CanvasProjectStorageError("画布不存在或无权访问")
            return self._row_to_project(row)
        finally:
            conn.close()

    def upsert_project(
        self,
        *,
        user_id: str,
        title: Optional[str] = None,
        nodes: Optional[List[Dict]] = None,
        edges: Optional[List[Dict]] = None,
        viewport: Optional[Dict] = None,
        project_id: Optional[str] = None,
        production: Optional[Dict] = None,
    ) -> Dict:
        now = self._now()
        project_id = str(project_id or "").strip() or str(uuid.uuid4())

        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM canvas_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row and row['user_id'] != user_id:
                raise CanvasProjectStorageError('画布不存在或无权访问')
            existing = self._row_to_project(row) if row else None
            title = self._coerce_title(title if title is not None else (existing or {}).get('title'))
            nodes = self._coerce_list(nodes if nodes is not None else (existing or {}).get('nodes'), 'nodes')
            edges = self._coerce_list(edges if edges is not None else (existing or {}).get('edges'), 'edges')
            viewport = self._coerce_viewport(viewport if viewport is not None else (existing or {}).get('viewport'))
            # Older autosave clients omit production or send null. Neither erases it.
            saved_production = (existing or {}).get('production')
            if production is not None:
                from modules.studio_service import StudioValidationError, normalize_production
                try:
                    saved_production = normalize_production(production, base=saved_production)
                except StudioValidationError as exc:
                    raise CanvasProjectValidationError(str(exc)) from exc
            production_json = self._json_dumps(saved_production) if saved_production is not None else None
            if existing:
                conn.execute(
                    """
                    UPDATE canvas_projects
                    SET title = ?, nodes_json = ?, edges_json = ?, viewport_json = ?, production_json = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        title,
                        self._json_dumps(nodes),
                        self._json_dumps(edges),
                        self._json_dumps(viewport),
                        production_json,
                        now,
                        project_id,
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO canvas_projects
                    (id, user_id, title, schema_version, nodes_json, edges_json, viewport_json, production_json,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        user_id,
                        title,
                        CANVAS_SCHEMA_VERSION,
                        self._json_dumps(nodes),
                        self._json_dumps(edges),
                        self._json_dumps(viewport),
                        production_json,
                        now,
                        now,
                    ),
                )
            conn.commit()
            return self.get_project(user_id=user_id, project_id=project_id)
        except CanvasProjectStorageError:
            raise
        except Exception as exc:
            raise CanvasProjectStorageError(f"保存创意画布失败: {exc}") from exc
        finally:
            conn.close()

    def delete_project(self, *, user_id: str, project_id: str) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM canvas_projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            raise CanvasProjectStorageError(f"删除创意画布失败: {exc}") from exc
        finally:
            conn.close()

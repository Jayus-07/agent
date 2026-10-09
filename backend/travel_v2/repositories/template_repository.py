from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def _decode(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class TemplateRepository:
    def insert(self, cursor, *, template_id: UUID, slug: str, title: str,
               destination: str, summary: str, cover_image_url: str | None,
               tags: list[str], sort_weight: int, content: dict,
               created_by: str) -> dict:
        cursor.execute(
            """INSERT INTO travel_v2.templates
                   (template_id, slug, title, destination, summary,
                    cover_image_url, tags, sort_weight, content, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
               ON CONFLICT (slug) DO NOTHING
               RETURNING template_id, slug, title, destination, summary,
                         cover_image_url, tags, version, status, sort_weight,
                         content::text, created_by, published_at, created_at, updated_at""",
            (str(template_id), slug, title, destination, summary,
             cover_image_url, tags, sort_weight,
             json.dumps(content, ensure_ascii=False), created_by),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    def get_published(self, cursor, key: str, *, by_slug: bool = True) -> dict | None:
        column = "slug" if by_slug else "template_id"
        cast = "" if by_slug else "::uuid"
        cursor.execute(
            f"""SELECT template_id, slug, title, destination, summary,
                       cover_image_url, tags, version, status, sort_weight,
                       content::text, created_by, published_at, created_at, updated_at
                  FROM travel_v2.templates
                 WHERE {column} = %s{cast} AND status = 'published'""",
            (key,),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    def get_any(self, cursor, template_id: UUID | str) -> dict | None:
        cursor.execute(
            """SELECT template_id, slug, title, destination, summary,
                      cover_image_url, tags, version, status, sort_weight,
                      content::text, created_by, published_at, created_at, updated_at
                 FROM travel_v2.templates WHERE template_id = %s::uuid""",
            (str(template_id),),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    def update(self, cursor, template_id: UUID | str, *,
               expected_version: int, title: str, destination: str,
               summary: str, cover_image_url: str | None, tags: list[str],
               sort_weight: int, content: dict) -> dict | None:
        cursor.execute(
            """UPDATE travel_v2.templates
                  SET title = %s, destination = %s, summary = %s,
                      cover_image_url = %s, tags = %s, sort_weight = %s,
                      content = %s::jsonb, version = version + 1, updated_at = now()
                WHERE template_id = %s::uuid AND version = %s
                  AND status <> 'archived'
               RETURNING template_id, slug, title, destination, summary,
                         cover_image_url, tags, version, status, sort_weight,
                         content::text, created_by, published_at, created_at, updated_at""",
            (title, destination, summary, cover_image_url, tags, sort_weight,
             json.dumps(content, ensure_ascii=False), str(template_id),
             expected_version),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    def list_published(self, cursor, *, destination: str | None = None,
                       limit: int = 50) -> list[dict]:
        where = "status = 'published'"
        values: list[Any] = []
        if destination:
            where += " AND destination = %s"
            values.append(destination)
        values.append(max(1, min(int(limit), 100)))
        cursor.execute(
            f"""SELECT template_id, slug, title, destination, summary,
                       cover_image_url, tags, version, status, sort_weight,
                       content::text, created_by, published_at, created_at, updated_at
                  FROM travel_v2.templates WHERE {where}
                 ORDER BY sort_weight DESC, published_at DESC, template_id
                 LIMIT %s""",
            values,
        )
        return [self._row(row) for row in cursor.fetchall()]

    def publish(self, cursor, template_id: UUID | str) -> dict | None:
        cursor.execute(
            """UPDATE travel_v2.templates
                  SET status = 'published', published_at = COALESCE(published_at, now()),
                      updated_at = now()
                WHERE template_id = %s::uuid AND status IN ('draft', 'published')
               RETURNING template_id, slug, title, destination, summary,
                         cover_image_url, tags, version, status, sort_weight,
                         content::text, created_by, published_at, created_at, updated_at""",
            (str(template_id),),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: tuple) -> dict:
        return {
            "template_id": str(row[0]), "slug": row[1], "title": row[2],
            "destination": row[3], "summary": row[4],
            "cover_image_url": row[5], "tags": list(row[6] or []),
            "version": int(row[7]), "status": row[8],
            "sort_weight": row[9], "content": _decode(row[10]),
            "created_by": row[11],
            "published_at": row[12].isoformat() if row[12] else None,
            "created_at": row[13].isoformat(),
            "updated_at": row[14].isoformat(),
        }

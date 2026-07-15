"""
PostgreSQL ``image_proxy_cache`` repository (remote-email-image proxy cache).
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from database import connection
from database.contracts import ImageProxyCacheStore
from database.errors import DatabaseError, QueryError
from database.queries import image_proxy_cache as queries


class PgImageProxyCacheStore(ImageProxyCacheStore):
    """PostgreSQL-backed implementation of :py:class:`ImageProxyCacheStore`.

    Mirrors :py:class:`PgEmailAttachmentStore`'s capture pattern. The
    ``InvalidTextRepresentation`` guard that store carries for its UUID
    keys is deliberately omitted here — ``url_hash`` is TEXT (a SHA-256
    hex), so there is no UUID cast that could ever raise it.
    """

    def get(self, url_hash: str) -> dict[str, Any] | None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(queries.GET_BY_URL_HASH, {"url_hash": url_hash})
                    row = cur.fetchone()
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to get image proxy cache entry.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected image proxy cache get error ({type(exc).__name__}): {exc}"
            ) from exc
        if row is None:
            return None
        image_bytes = row["image_bytes"]
        return {
            "content_type": row["content_type"],
            "image_bytes": bytes(image_bytes) if image_bytes is not None else b"",
        }

    def upsert(
        self, url_hash: str, url: str, content_type: str, image_bytes: bytes,
    ) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        queries.UPSERT_IMAGE,
                        {
                            "url_hash": url_hash,
                            "url": url,
                            "content_type": content_type,
                            "image_bytes": psycopg2.Binary(image_bytes),
                        },
                    )
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to upsert image proxy cache entry.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected image proxy cache upsert error ({type(exc).__name__}): {exc}"
            ) from exc

    def touch_last_accessed(self, url_hash: str) -> None:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.TOUCH_LAST_ACCESSED, {"url_hash": url_hash})
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to touch image proxy cache last_accessed.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected image proxy cache touch error ({type(exc).__name__}): {exc}"
            ) from exc

    def purge_expired(self) -> tuple[int, int]:
        try:
            with connection.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(queries.PURGE_EXPIRED)
                    rows = cur.fetchall()
            count = len(rows)
            freed = sum(int(r[1] or 0) for r in rows)
            return count, freed
        except DatabaseError:
            raise
        except psycopg2.Error as exc:
            raise QueryError("Failed to purge expired image proxy cache entries.") from exc
        except Exception as exc:
            raise QueryError(
                f"Unexpected image proxy cache purge error ({type(exc).__name__}): {exc}"
            ) from exc


image_proxy_cache_store = PgImageProxyCacheStore()

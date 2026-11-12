"""Rebuild the Redis cache from PostgreSQL, the source of truth.

Usage (inside the app container):
    docker compose exec app python -m app.sync_cache
"""

from app.config import get_settings
from app.main import build_service


def main() -> None:
    service = build_service(get_settings())
    try:
        service.sync_cache(force=True)
        board = service.dashboard()
        print(f"Redis rebuilt: {len(board.recent)} recent reports, {board.total} in total.")
    finally:
        service.cache.client.close()
        service.repo.pool.close()


if __name__ == "__main__":
    main()

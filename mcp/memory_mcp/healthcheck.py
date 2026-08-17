import sys

from .config import Settings
from .repository import MemoryRepository


def main():
    settings = Settings.from_env()
    repository = MemoryRepository(
        settings.database_url,
        settings.ollama_url,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    return 0 if repository.ready() else 1


if __name__ == "__main__":
    sys.exit(main())

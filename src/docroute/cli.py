import sys
from pathlib import Path

from docroute.ingestion.ingest import ingest_pdf

SUPPORTED_EXT = {".pdf"}  # image-only inputs (.png/.jpg) can be added back
                          # once a matching route_document() path exists for them


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: docroute <file_or_folder>")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if input_path.is_dir():
        files = [p for p in sorted(input_path.iterdir()) if p.suffix.lower() in SUPPORTED_EXT]
        if not files:
            print(f"No supported files found in {input_path}")
            sys.exit(1)
    elif input_path.is_file():
        files = [input_path]
    else:
        print(f"Not found: {input_path}")
        sys.exit(1)

    for f in files:
        ingest_pdf(f)


if __name__ == "__main__":
    main()

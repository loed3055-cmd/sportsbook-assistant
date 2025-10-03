.PHONY: build run serve dev

build:
	podman build -t sportsbook-dev -f Containerfile .

run:
	podman run -it --rm \
		-v $(PWD):/workspace \
		-w /workspace \
		-p 8000:8000 \
		sportsbook-dev bash

serve:
	UV_SYSTEM_PYTHON=1 uvicorn src.app.main:app --host 0.0.0.0 --port 8000 --reload
# Handy shortcut: rebuild & run FastAPI directly
dev: build
	podman run -it --rm \
		-v $(PWD):/workspace \
		-w /workspace \
		-p 8000:8000 \
		sportsbook-dev \
		make serve
